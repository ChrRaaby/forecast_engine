"""Live entry point: a thin adapter between Metaculus (via forecasting-tools) and our own core (ADR-0005).

The adapter only fetches questions, converts them to QuestionSnapshots, calls gather_research() and forecast(),
writes records, and (only with --publish) posts the forecast and comment. All forecasting logic lives in
forecast_engine/. Dry-run is the default.

    poetry run python main.py --mode test_questions               # dry run on the bot-testing area
    poetry run python main.py --mode tournament --publish         # what GitHub Actions runs
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import timedelta

import dotenv
import requests

from bot_helpers import check_environment, print_run_summary_banner, silence_noisy_dependencies

silence_noisy_dependencies()

from forecasting_tools import (  # noqa: E402
    ApiFilter,
    BinaryQuestion,
    ForecastBot,
    MetaculusClient,
    MetaculusQuestion,
    MultipleChoiceQuestion,
    NumericDistribution,
    NumericQuestion,
    Percentile,
    PredictedOptionList,
)
from forecasting_tools.data_models.data_organizer import DataOrganizer  # noqa: E402
from forecasting_tools.data_models.forecast_report import ForecastReport  # noqa: E402
from forecasting_tools.data_models.multiple_choice_report import PredictedOption  # noqa: E402

from forecast_engine import guards, parsing  # noqa: E402
from forecast_engine.clock import Clock, SystemClock  # noqa: E402
from forecast_engine.config import (  # noqa: E402
    CONFIG_VERSION,
    DEFAULT_CONFIG,
    WIDE_CONFIG,
    DEFAULT_MAX_QUESTIONS_PER_RUN,
    DEFAULT_MAIN_SITE_PER_RUN,
    DEFAULT_MAX_RUN_COST_USD,
    MAIN_SITE_HORIZON_DAYS,
    BotConfig,
)
from forecast_engine.core import ForecastFailed, forecast  # noqa: E402
from forecast_engine.llm import LlmClient, OpenRouterClient  # noqa: E402
from forecast_engine.records import RecordWriter  # noqa: E402
from forecast_engine.retries import RetryLedger  # noqa: E402
from forecast_engine.research import gather_research  # noqa: E402
from forecast_engine.schema import ForecastRecord, QuestionSnapshot  # noqa: E402

dotenv.load_dotenv()
logger = logging.getLogger(__name__)

TOURNAMENT_URLS = {
    "tournament": "https://www.metaculus.com/futureeval/",
    "wide": "https://www.metaculus.com/tournament/metaculus-cup-fall-2026/",
    "test_questions": "https://www.metaculus.com/tournament/bot-testing-area/",
}


def to_snapshot(q: MetaculusQuestion) -> QuestionSnapshot:
    common = dict(
        question_id=q.id_of_question,
        post_id=q.id_of_post,
        question_text=q.question_text,
        background_info=q.background_info or "",
        resolution_criteria=q.resolution_criteria or "",
        fine_print=q.fine_print or "",
        page_url=q.page_url or "",
        tournaments=tuple(q.tournament_slugs or ()),
        open_time=q.open_time,
        scheduled_resolution_time=q.scheduled_resolution_time,
        unit_of_measure=q.unit_of_measure or "",
    )
    if isinstance(q, BinaryQuestion):
        return QuestionSnapshot(question_type="binary", **common)
    if isinstance(q, MultipleChoiceQuestion):
        return QuestionSnapshot(question_type="multiple_choice", options=tuple(q.options), **common)
    if isinstance(q, NumericQuestion):  # includes DiscreteQuestion
        return QuestionSnapshot(
            question_type="discrete" if q.question_type == "discrete" else "numeric",
            lower_bound=q.lower_bound,
            upper_bound=q.upper_bound,
            open_lower_bound=q.open_lower_bound,
            open_upper_bound=q.open_upper_bound,
            nominal_lower_bound=q.nominal_lower_bound,
            nominal_upper_bound=q.nominal_upper_bound,
            **common,
        )
    raise NotImplementedError(f"question type {type(q).__name__} is not supported in M1 (AIB uses binary/MC/numeric)")


def to_prediction(q: MetaculusQuestion, aggregate):
    if isinstance(q, BinaryQuestion):
        return float(aggregate)
    if isinstance(q, MultipleChoiceQuestion):
        guards.check_mc_options(aggregate, q.options)
        return PredictedOptionList(
            predicted_options=[PredictedOption(option_name=k, probability=v) for k, v in aggregate.items()]
        )
    points, _ = parsing.fit_to_bounds(aggregate, q.lower_bound, q.upper_bound, q.open_lower_bound, q.open_upper_bound)
    points = parsing.make_strictly_increasing(points, scale=q.upper_bound - q.lower_bound)
    percentiles = [Percentile(percentile=p, value=v) for p, v in points]
    return NumericDistribution.from_question(percentiles, q)


def build_comment(q: MetaculusQuestion, record: ForecastRecord, readable: str) -> str:
    lines = [
        "# SUMMARY",
        f"*Question*: {q.question_text}",
        f"*Final Prediction*: {readable}",
        f"*Config*: `{record.config_version}` (hash `{record.config_hash}`), {record.aggregation} of "
        f"{record.n_ok}/{len(record.forecasters)} forecasters",
        f"*Cost*: ${record.cost_usd:.4f}",
        "",
        "## Forecasters",
    ]
    for f in record.forecasters:
        if f.ok:
            status = _readable_member(q, f.prediction)
        elif f.excluded:
            status = f"excluded ({f.excluded})"
        else:
            status = f"failed ({f.call.error or f.parse_error})"
        lines.append(f"* `{f.model}`: {status}")
    lines += ["", "# RESEARCH", record.research.as_prompt_text().replace("\n#", "\n\\#"), "", "# FORECASTS"]
    for i, f in enumerate(record.forecasters, 1):
        if f.ok:
            body = f.call.output.replace("\n#", "\n\\#")
            lines += [f"## Forecaster {i}: {f.model}", body, ""]
    text = "\n".join(lines)
    if len(text) > 140_000:
        text = text[:140_000] + "\n\n(truncated)"
    return text


def _readable_member(q: MetaculusQuestion, pred) -> str:
    if isinstance(q, BinaryQuestion):
        return f"{pred:.1%}"
    if isinstance(q, MultipleChoiceQuestion):
        return ", ".join(f"{k}: {v:.1%}" for k, v in pred.items())
    return ", ".join(f"P{int(p * 100)}={v:g}" for p, v in pred)


class EngineBot(ForecastBot):
    """Uses ForecastBot only for question filtering, concurrency and publishing; the forecast comes from our core."""

    def __init__(
        self,
        *,
        clock: Clock,
        cfg: BotConfig,
        llm: LlmClient,
        writer: RecordWriter,
        publish: bool,
        max_run_cost_usd: float,
        retries: RetryLedger | None = None,
        max_concurrent: int = 3,
    ) -> None:
        super().__init__(
            publish_reports_to_metaculus=publish,
            skip_previously_forecasted_questions=True,
            llms={"default": None, "summarizer": None, "researcher": None, "parser": None},
            enable_summarize_research=False,
        )
        self.clock, self.cfg, self.llm, self.writer = clock, cfg, llm, writer
        self.max_run_cost_usd = max_run_cost_usd
        self.retries = retries
        self.spent_usd = 0.0
        self._sem = asyncio.Semaphore(max_concurrent)

    async def _run_individual_question(self, question: MetaculusQuestion) -> ForecastReport:
        async with self._sem:
            if self.spent_usd >= self.max_run_cost_usd:
                raise RuntimeError(f"run cost cap reached (${self.spent_usd:.2f} >= ${self.max_run_cost_usd:.2f}); skipped")
            start = time.monotonic()
            snapshot = to_snapshot(question)
            research = await gather_research(snapshot, self.clock, self.cfg.research)
            try:
                record = await forecast(snapshot, self.clock, research, self.cfg, self.llm)
            except ForecastFailed as e:
                self.spent_usd += e.record.cost_usd
                self.writer.write(e.record, published=False, status="failed")
                self._count_failure(question)
                raise
            self.spent_usd += record.cost_usd
            if self.publish_reports_to_metaculus and research.is_empty:
                self.writer.write(record, published=False, status="refused_no_research")
                raise RuntimeError(f"refusing to publish without research: {research.errors}")

            if isinstance(question, NumericQuestion):
                _, clipped = parsing.fit_to_bounds(record.aggregate, question.lower_bound, question.upper_bound,
                                                   question.open_lower_bound, question.open_upper_bound)
                if clipped:
                    record.errors.append("numeric percentiles clipped to the range Metaculus accepts")
            report_type = DataOrganizer.get_report_type_for_question_type(type(question))
            try:
                prediction = to_prediction(question, record.aggregate)
            except Exception as e:
                record.errors.append(f"could not convert forecast for Metaculus: {type(e).__name__}: {e}")
                self.writer.write(record, published=False, status="failed")
                self._count_failure(question)
                raise
            report = report_type(
                question=question,
                prediction=prediction,
                explanation=build_comment(question, record, report_type.make_readable_prediction(prediction)),
                price_estimate=record.cost_usd,
                minutes_taken=(time.monotonic() - start) / 60,
                errors=record.errors + research.errors,
            )
            published = False
            if self.publish_reports_to_metaculus:
                ok, why = question_forecastable(question, self.clock)
                if not ok:  # the run can take minutes; re-check right before publishing (B-09)
                    record.errors.append(f"not published: {why}")
                    self.writer.write(record, published=False, status="failed")
                    raise RuntimeError(f"not published: {why}")
                try:
                    await report.publish_report_to_metaculus(metaculus_client=self.metaculus_client)
                except Exception as e:
                    record.errors.append(f"publishing failed: {type(e).__name__}: {e}")
                    self.writer.write(record, published=False, status="failed")
                    raise
                published = True
            self.writer.write(record, published=published, status="ok")
            if self.retries:
                self.retries.succeeded(question.id_of_question)
            return report

    def _count_failure(self, question: MetaculusQuestion) -> None:
        """B-61: forecasting failures repeat on every run, so they count towards the retry cap."""
        if self.retries:
            self.retries.failed(question.id_of_question, self.clock.now())

    # The template's per-type hooks are unused: _run_individual_question above replaces the whole pipeline.
    async def run_research(self, question):  # pragma: no cover
        raise NotImplementedError

    async def _run_forecast_on_binary(self, question, research):  # pragma: no cover
        raise NotImplementedError

    async def _run_forecast_on_multiple_choice(self, question, research):  # pragma: no cover
        raise NotImplementedError

    async def _run_forecast_on_numeric(self, question, research):  # pragma: no cover
        raise NotImplementedError


def question_forecastable(q: MetaculusQuestion, clock: Clock) -> tuple[bool, str]:
    state = q.state.value if q.state is not None else None
    return guards.forecastable(state, q.close_time, q.resolution_string, clock.now())


def skip_capped(questions: list[MetaculusQuestion], retries: RetryLedger, clock: Clock) -> list[MetaculusQuestion]:
    """Drop questions that hit the retry cap (B-61), logging why."""
    out = []
    for q in questions:
        why = retries.blocked(q.id_of_question, clock.now())
        if why:
            logger.warning(f"Skipping {q.page_url}: {why}")
        else:
            out.append(q)
    return out


def check_clock(clock: Clock) -> None:
    """Wrong-"today" guard (B-09, R-18): abort if our clock disagrees with Metaculus's server by more than a day."""
    resp = requests.get("https://www.metaculus.com/api/posts/", params={"limit": 1},
                        headers={"Authorization": f"Token {os.environ['METACULUS_TOKEN']}"}, timeout=30)
    date = resp.headers.get("Date")
    if not date:
        logger.warning("No Date header from Metaculus; clock check skipped")
        return
    drift = guards.clock_drift_days(clock.now(), date)
    if drift > 1:
        raise guards.GuardError(f"system clock is {drift:.1f} days off Metaculus's server time; refusing to run")


def fetch_questions(client: MetaculusClient, mode: str, clock: Clock, main_site_max: int = 0) -> list[MetaculusQuestion]:
    if mode == "tournament":
        ids = [client.CURRENT_AI_COMPETITION_ID, client.CURRENT_MINIBENCH_ID]
    elif mode == "wide":
        return fetch_wide(client, clock, main_site_max)
    else:
        ids = ["bot-testing-area"]
    questions: list[MetaculusQuestion] = []
    for tid in ids:
        questions += client.get_all_open_questions_from_tournament(tid)
    return questions


def fetch_wide(client: MetaculusClient, clock: Clock, main_site_max: int) -> list[MetaculusQuestion]:
    """Questions outside FutureEval, forecast to unlock their outcomes for evaluation (B-40, research/05).

    All new Metaculus Cup questions, plus up to `main_site_max` main-site questions per run, soonest-resolving first.
    Metaculus allows bots on both (bot comments stay private); bots are not eligible for Cup prizes.
    """
    cup = [q for q in client.get_all_open_questions_from_tournament(client.CURRENT_METACULUS_CUP_ID) if not q.already_forecasted]
    if main_site_max <= 0:
        return cup
    flt = ApiFilter(
        allowed_types=["binary", "multiple_choice", "numeric", "discrete"],
        allowed_statuses=["open"],
        scheduled_resolve_time_lt=clock.now() + timedelta(days=MAIN_SITE_HORIZON_DAYS),
        is_previously_forecasted_by_user=False,
        is_in_main_feed=True,
        order_by="scheduled_resolve_time",
    )
    candidates = asyncio.run(client.get_questions_matching_filter(flt, num_questions=50, error_if_question_target_missed=False))
    skip = {"fall-futureeval-2026", "minibench", "bot-testing-area", "metaculus-cup-fall-2026"}
    main = [q for q in candidates if not q.already_forecasted and not (set(q.tournament_slugs or []) & skip)]
    return cup + main[:main_site_max]


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    parser = argparse.ArgumentParser(description="forecast_engine live bot")
    parser.add_argument("--mode", choices=["tournament", "wide", "test_questions"], default="tournament")
    parser.add_argument("--main-site-max", type=int, default=DEFAULT_MAIN_SITE_PER_RUN, help="wide mode: main-site questions per run")
    parser.add_argument("--publish", action="store_true", help="post forecasts + comments to Metaculus (default: dry run)")
    parser.add_argument("--max-questions", type=int, default=DEFAULT_MAX_QUESTIONS_PER_RUN)
    parser.add_argument("--max-cost", type=float, default=DEFAULT_MAX_RUN_COST_USD, help="stop starting new questions above this $")
    args = parser.parse_args()

    check_environment()
    clock = SystemClock()
    run_id = clock.now().strftime("%Y%m%dT%H%M%SZ") + f"-{args.mode}" + ("" if args.publish else "-dryrun")
    run_cfg = WIDE_CONFIG if args.mode == "wide" else DEFAULT_CONFIG
    print(f"🤖  forecast_engine {CONFIG_VERSION} (config {run_cfg.config_hash()}) mode={args.mode} "
          f"publish={'yes' if args.publish else 'no (dry run)'} max_questions={args.max_questions} max_cost=${args.max_cost}")

    retries = RetryLedger(CONFIG_VERSION) if args.publish else None  # dry runs neither use nor change the ledger
    bot = EngineBot(
        clock=clock,
        cfg=run_cfg,
        llm=OpenRouterClient(clock),
        writer=RecordWriter(run_id),
        publish=args.publish,
        max_run_cost_usd=args.max_cost,
        retries=retries,
    )
    check_clock(clock)
    questions = fetch_questions(bot.metaculus_client, args.mode, clock, args.main_site_max)
    open_qs = []
    for q in questions:
        ok, why = question_forecastable(q, clock)
        if ok:
            open_qs.append(q)
        else:
            logger.info(f"Skipping {q.page_url}: {why}")
    questions = open_qs
    if args.mode in ("tournament", "wide") or args.publish:
        questions = [q for q in questions if not q.already_forecasted]
    supported = [q for q in questions if isinstance(q, (BinaryQuestion, MultipleChoiceQuestion, NumericQuestion))]
    if retries:
        supported = skip_capped(supported, retries, clock)
    if len(supported) < len(questions):
        logger.warning(f"Skipping {len(questions) - len(supported)} unsupported questions (date/conditional)")
    selected = supported[: args.max_questions]
    logger.info(f"{len(supported)} eligible questions, forecasting {len(selected)}")

    bot.skip_previously_forecasted_questions = False  # already filtered above
    reports = asyncio.run(bot.forecast_questions(selected, return_exceptions=True))
    print_run_summary_banner(reports, will_publish=args.publish, tournament_url=TOURNAMENT_URLS.get(args.mode))
    print(f"Records: {bot.writer.run_dir}  |  run cost ${bot.spent_usd:.4f}")
    if retries:
        retries.save()
    all_failed = bool(reports) and all(isinstance(r, BaseException) for r in reports)
    return 1 if all_failed else 0


if __name__ == "__main__":
    sys.exit(main())
