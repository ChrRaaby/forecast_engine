"""Rough per-question cost model for forecast_engine.

All prices are USD per 1M tokens, taken from official pricing pages on 2026-09-28
(Anthropic, OpenAI, Google). Token counts are ASSUMPTIONS; replace them with measured
values once the M1 bot logs real usage. Run: python tools/cost_model.py
"""

PRICES = {  # model: (input $/1M, output $/1M)
    "claude-opus-5.5":       (4.00, 20.00),
    "claude-sonnet-5.5":     (2.00, 10.00),
    "claude-haiku-4.5":      (1.00, 5.00),
    "gpt-5.6-sol":           (5.00, 30.00),
    "gpt-5.6-terra":         (2.00, 12.00),
    "gpt-5.6-luna":          (0.20, 1.20),
    "gemini-3.1-pro":        (2.00, 12.00),
    "gemini-3.8-flash":      (0.75, 3.75),   # doubles from 2027-01-01
    "gemini-3.1-flash-lite": (0.25, 1.50),
    "local-4090":            (0.00, 0.00),   # electricity only
}
SEARCH_PER_CALL = 0.010  # $10/1k (Claude & OpenAI web search); Gemini grounding: 5k/month free, then $14/1k

# Token assumptions per question
RESEARCH = dict(searches=6, tok_in=40_000, tok_out=4_000)   # agentic search + summarising
FORECAST = dict(tok_in=12_000, tok_out=6_000)                # per ensemble member run (incl. reasoning tokens)
AGGREGATE = dict(tok_in=8_000, tok_out=1_500)                # optional synthesis step


def cost(model, tin, tout, batch=False):
    pi, po = PRICES[model]
    c = (tin * pi + tout * po) / 1e6
    return c * (0.5 if batch else 1.0)


def per_question(research_model, forecasters, aggregator=None, search_price=SEARCH_PER_CALL, batch=False):
    # Agentic research is multi-turn, so no batch discount; batch applies to forecast + aggregation only.
    r = cost(research_model, RESEARCH["tok_in"], RESEARCH["tok_out"]) + RESEARCH["searches"] * search_price
    f = sum(cost(m, FORECAST["tok_in"], FORECAST["tok_out"], batch) for m in forecasters)
    a = cost(aggregator, AGGREGATE["tok_in"], AGGREGATE["tok_out"], batch) if aggregator else 0.0
    return r + f + a


SCENARIOS = {
    "A  Baseline: 1 cheap call, no search": dict(research_model="local-4090", forecasters=["gpt-5.6-luna"], search_price=0.0),
    "B  Cheap pipeline: Flash-Lite research + 3x Luna": dict(research_model="gemini-3.1-flash-lite", forecasters=["gpt-5.6-luna"] * 3),
    "C  Mid ensemble: Flash research + Sonnet/Terra/Gemini Pro": dict(research_model="gemini-3.8-flash", forecasters=["claude-sonnet-5.5", "gpt-5.6-terra", "gemini-3.1-pro"]),
    "D  Frontier ensemble: + Opus/Sol, 5 members + aggregator": dict(research_model="gemini-3.8-flash", forecasters=["claude-opus-5.5", "gpt-5.6-sol", "gemini-3.1-pro", "claude-sonnet-5.5", "gpt-5.6-terra"], aggregator="claude-sonnet-5.5"),
    "E  Hybrid: local research on 4090 + mid ensemble": dict(research_model="local-4090", forecasters=["claude-sonnet-5.5", "gpt-5.6-terra", "gemini-3.1-pro"]),
}

if __name__ == "__main__":
    print(f"{'Scenario':62} {'live $/q':>9} {'batch $/q':>10} {'Qs per $100 (batch)':>20}")
    for name, kw in SCENARIOS.items():
        live = per_question(**kw)
        b = per_question(**kw, batch=True)
        print(f"{name:62} {live:9.3f} {b:10.3f} {100 / b if b else float('inf'):20.0f}")
