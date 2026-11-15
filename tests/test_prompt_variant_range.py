"""EXP-015 prompt variant "range-calibrated-2026-11-14": the live prompt with only the numeric range sentence changed.
Offline; synthetic fixture questions only."""
import pytest

from evals import prompt_archive as pa
from forecast_engine import prompt_versions
from forecast_engine.prompt_variants import range_calibrated_2026_11_14 as variant

VERSION = variant.VERSION
CASES = pa.fixture_cases()


def test_variant_is_registered_and_archived():
    assert VERSION in prompt_versions.VARIANTS
    assert VERSION in pa.ARCHIVE
    assert VERSION not in (prompt_versions.LIVE,)


@pytest.mark.parametrize("case", [c for c in CASES if c[1].question_type in ("binary", "multiple_choice")], ids=lambda c: c[0])
def test_binary_and_multiple_choice_prompts_are_byte_identical_to_live(case):
    _, q, items, as_of = case
    assert pa.variant_prompt(VERSION, q, items, as_of) == pa.live_prompt(q, items, as_of)


@pytest.mark.parametrize("case", [c for c in CASES if c[1].question_type in ("numeric", "discrete")], ids=lambda c: c[0])
def test_numeric_prompt_differs_only_by_the_range_sentence(case):
    _, q, items, as_of = case
    live = pa.live_prompt(q, items, as_of)
    new = pa.variant_prompt(VERSION, q, items, as_of)
    assert variant.OLD_RANGE_SENTENCE in live and variant.NEW_RANGE_SENTENCE not in live
    assert live.count(variant.OLD_RANGE_SENTENCE) == 1
    assert new == live.replace(variant.OLD_RANGE_SENTENCE, variant.NEW_RANGE_SENTENCE)
    assert variant.OLD_RANGE_SENTENCE not in new


def test_frozen_copy_renders_what_the_variant_renders():
    for _, q, items, as_of in CASES:
        assert pa.ARCHIVE[VERSION].build(q, items, as_of) == pa.variant_prompt(VERSION, q, items, as_of)
