"""Tests for the B-46 reasoning graph: quote check, tracing, final probability from the record, malformed extractor output."""
import json

import pytest

from evals import graph_extract as gx
from forecast_engine import graph as g

RATIONALE = """(b) The status quo outcome is NO.

Historically, about 10% of such decrees are reversed within a month.
The government has explicitly said the decree “stays,” and the measure is tied to an IMF program.
**Protests are escalating** in Santa Cruz.

Probability: 7%"""

ITEMS = [
    {"source": "asknews_latest", "title": "Santa Cruz Drivers Suspend 48-Hour Strike, Demand National Negotiations",
     "url": "https://eldeber.com.bo/santa-cruz/paro-48-horas", "text": "Transport unions suspended the strike."},
    {"source": "asknews_latest", "title": "Bolivia Eliminates Diesel Subsidies",
     "url": "https://www.lostiempos.com/economia/diesel", "text": "President Paz said the decree stays; the IMF program requires it."},
    {"source": "gemini_grounded", "title": None, "url": None, "text": "Unrelated summary about Honduras fuel prices."},
]


def raw_graph(**over):
    d = {
        "base_rate": {"status": "stated", "reference_class": "decrees reversed within a month", "value": 0.10,
                      "quote": "Historically, about 10% of such decrees are reversed within a month."},
        "drivers": [
            {"id": "d1", "text": "Government commitment", "direction": "down", "strength": 3,
             "quote": 'The government has explicitly said the decree "stays," and the measure is tied to an IMF program.'},
            {"id": "d2", "text": "Protests", "direction": "up", "strength": 2, "quote": "Protests are escalating in Santa Cruz."},
        ],
        "evidence": [
            {"id": "e1", "claim": "President Paz said the decree stays, IMF program requires it", "driver_id": "d1",
             "direction": "down", "strength": 3, "source_title": None, "source_url": None, "source_date": None,
             "research_item": 1, "quote": "The government has explicitly said the decree “stays,” and the measure is tied to an IMF program."},
        ],
        "scenarios": [{"name": "NO", "probability": None, "description": "status quo",
                       "quote": "(b) The status quo outcome is NO."}],
        "final": {"stated_shift_from_base": "slightly below the base rate", "quote": "Probability: 7%"},
    }
    d.update(over)
    return d


def build(raw, final=0.07, items=ITEMS):
    return g.build_graph(raw, rationale=RATIONALE, final_probability=final, research_items=items)


# ---- quote check
def test_valid_quotes_survive_with_formatting_normalised():
    gr = build(raw_graph())
    assert gr.checks.n_dropped == 0
    assert gr.base_rate.status == "stated" and gr.base_rate.value == pytest.approx(0.10)
    assert [d.id for d in gr.drivers] == ["d1", "d2"]  # curly quotes and **bold** don't matter
    assert len(gr.evidence) == 1 and len(gr.scenarios) == 1


def test_paraphrased_or_invented_quotes_are_dropped_and_counted():
    raw = raw_graph()
    raw["drivers"][1]["quote"] = "Protests are growing quickly in Santa Cruz."  # paraphrase
    raw["evidence"].append({**raw["evidence"][0], "id": "e2", "quote": "The IMF threatened to cancel the program."})
    raw["base_rate"]["quote"] = "About 10% of decrees ... are reversed."  # elided
    gr = build(raw)
    assert [d.id for d in gr.drivers] == ["d1"]
    assert [e.id for e in gr.evidence] == ["e1"]
    assert gr.base_rate.status == "none" and gr.base_rate.value is None
    assert gr.checks.dropped == {"driver": 1, "evidence": 1, "base_rate": 1}
    assert gr.checks.n_dropped == 3 and len(gr.checks.dropped_quotes) == 3


def test_quote_from_research_not_rationale_is_dropped():
    raw = raw_graph()
    raw["evidence"][0]["quote"] = "Transport unions suspended the strike."  # in the research, not in the member's text
    assert build(raw).evidence == []


def test_empty_or_tiny_quote_fails():
    assert not g.quote_ok("", g.normalize_text(RATIONALE))
    assert not g.quote_ok("NO.", g.normalize_text(RATIONALE))


def test_evidence_pointing_at_dropped_driver_becomes_orphan():
    raw = raw_graph()
    raw["drivers"][0]["quote"] = "invented sentence that is not there"
    gr = build(raw)
    assert gr.evidence[0].driver_id is None and gr.checks.orphan_evidence == 1


def test_malformed_nodes_counted_as_invalid():
    raw = raw_graph()
    raw["drivers"].append({"id": "d3", "text": "x", "direction": "sideways", "strength": 2, "quote": "Probability: 7%"})
    raw["drivers"].append({"id": "d4", "text": "x", "direction": "up", "strength": 7, "quote": "Probability: 7%"})
    gr = build(raw)
    assert gr.checks.invalid == {"driver": 2} and len(gr.drivers) == 2


# ---- final probability always from the record
def test_final_probability_comes_from_record_not_extractor():
    raw = raw_graph()
    raw["final"]["probability"] = 0.9  # an extractor that invents a number is ignored
    gr = build(raw, final=0.07)
    assert gr.final.probability == pytest.approx(0.07)
    assert gr.final.shift_from_base == pytest.approx(-0.03)


def test_failing_final_quote_drops_quote_but_keeps_record_probability():
    raw = raw_graph()
    raw["final"]["quote"] = "Probability: 90%"
    gr = build(raw, final=0.07)
    assert gr.final.quote == "" and gr.final.probability == pytest.approx(0.07) and gr.checks.dropped == {"final": 1}


def test_percent_base_rate_is_scaled():
    raw = raw_graph()
    raw["base_rate"]["value"] = 10
    assert build(raw).base_rate.value == pytest.approx(0.10)


# ---- tracing (in code, not trusted from the extractor)
def test_trace_by_cited_url_then_title():
    text = g.normalize_text("Per eldeber.com.bo/santa-cruz/paro-48-horas and 'Bolivia Eliminates Diesel Subsidies', ...")
    e = g.Evidence(id="e", claim="x", direction="up", strength=1, quote="q",
                   source_url="http://eldeber.com.bo/santa-cruz/paro-48-horas/")
    g.trace_evidence(e, ITEMS, g.normalize_text("Per http://eldeber.com.bo/santa-cruz/paro-48-horas/ the strike ended."))
    assert (e.traced_to, e.trace_method, e.source_cited) == (0, "url", True)
    e = g.Evidence(id="e", claim="x", direction="up", strength=1, quote="q", source_title="Bolivia Eliminates Diesel Subsidies")
    g.trace_evidence(e, ITEMS, text)
    assert (e.traced_to, e.trace_method) == (1, "title")


def test_source_copied_from_research_but_not_cited_is_not_a_trace():
    """The extractor can fill a URL from the research; that must not count as the member citing it."""
    e = g.Evidence(id="e", claim="x", direction="up", strength=1, quote="q",
                   source_url="https://eldeber.com.bo/santa-cruz/paro-48-horas", source_title="Bolivia Eliminates Diesel Subsidies")
    g.trace_evidence(e, ITEMS, g.normalize_text(RATIONALE))
    assert e.source_cited is False and e.traced_to == "untraced"


def test_direction_mismatch_flags_inverted_answer():
    """All cited reasons push down, yet the answer is high: the pattern of a member answering for NO."""
    f = g.graph_features(build(raw_graph(drivers=[raw_graph()["drivers"][0]]), final=0.82))
    assert f["net_push"] < 0 and f["direction_mismatch"] is True
    assert g.graph_features(build(raw_graph(drivers=[raw_graph()["drivers"][0]]), final=0.07))["direction_mismatch"] is False


def test_trace_by_text_overlap_and_extractor_claim_is_not_trusted():
    gr = build(raw_graph())
    ev = gr.evidence[0]
    assert ev.traced_to == 1 and ev.trace_method == "text" and ev.extractor_traced_to == 1
    raw = raw_graph()
    raw["evidence"][0]["research_item"] = 2  # extractor points at an item that doesn't contain the claim
    ev = build(raw).evidence[0]
    assert ev.traced_to == 1 and ev.extractor_traced_to == 2


def test_background_knowledge_is_untraced():
    raw = raw_graph()
    raw["evidence"][0].update(claim="2010 gasolinazo was reversed within days",
                              quote="Historically, about 10% of such decrees are reversed within a month.")
    ev = build(raw).evidence[0]
    assert ev.traced_to == "untraced" and ev.trace_method is None
    assert g.graph_features(build(raw))["untraced_share"] == 1.0


# ---- malformed extractor JSON
@pytest.mark.parametrize("bad", [None, [], "text", {"drivers": []}, {**raw_graph(), "drivers": "d1"},
                                 {**raw_graph(), "final": "7%"}])
def test_malformed_structure_raises(bad):
    with pytest.raises(g.MalformedGraph):
        build(bad)


def test_parse_json_text_handles_fence_and_garbage():
    assert gx.parse_json_text('```json\n{"a": 1}\n```') == {"a": 1}
    with pytest.raises(g.MalformedGraph):
        gx.parse_json_text('{"base_rate": {"status": "stated"')  # truncated


def _record(outputs):
    return {
        "config_version": "v", "as_of": "2026-10-05T00:00:00+00:00", "aggregate": 0.2,
        "question": {"question_id": 1, "question_type": "binary", "question_text": "Will the decree be repealed?"},
        "research": {"items": ITEMS},
        "forecasters": [
            {"model": "m1", "prediction": 0.07, "call": {"output": RATIONALE}},
            {"model": "m2", "prediction": 0.2, "call": {"output": RATIONALE}},
            {"model": "m3", "prediction": None, "parse_error": "no number", "call": {"output": "garbled"}},
        ],
    }


def _fake_post(texts):
    it = iter(texts)

    def post(model, prompt, thinking):
        assert "FORECASTER'S TEXT" in prompt
        return {"candidates": [{"content": {"parts": [{"text": next(it)}]}, "finishReason": "STOP"}],
                "usageMetadata": {"promptTokenCount": 4000, "candidatesTokenCount": 800, "thoughtsTokenCount": 200}}
    return post


def test_extract_record_isolates_malformed_member_and_counts_cost():
    cfg = gx.ExtractorConfig()
    out = gx.extract_record(_record(None), "runs/x/q1.json", cfg,
                            post=_fake_post([json.dumps(raw_graph()), "Sorry, I cannot help with that."]))
    assert [m["model"] for m in out["members"]] == ["m1", "m2"]  # unparsed member skipped
    ok, bad = out["members"]
    assert ok["graph"]["final"]["probability"] == pytest.approx(0.07) and ok["error"] is None
    assert bad["graph"] is None and bad["error"].startswith("malformed") and bad["raw_output"].startswith("Sorry")
    per_call = (4000 * cfg.usd_per_m_in + 1000 * cfg.usd_per_m_out) / 1e6
    assert out["cost_usd"] == pytest.approx(2 * per_call) and len(out["errors"]) == 1


def test_extract_member_survives_api_error():
    def boom(*a):
        raise RuntimeError("Gemini HTTP 500")
    out = gx.extract_member(_record(None), _record(None)["forecasters"][0], gx.ExtractorConfig(), post=boom)
    assert out["graph"] is None and "HTTP 500" in out["error"]


def test_non_binary_record_has_no_members():
    rec = _record(None)
    rec["question"]["question_type"] = "numeric"
    assert gx.binary_members(rec) == []


def test_stored_graph_matches_json_schema():
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(build(raw_graph()).to_dict(), g.GRAPH_JSON_SCHEMA)
    jsonschema.Draft202012Validator.check_schema(g.EXTRACTOR_RESPONSE_SCHEMA)
