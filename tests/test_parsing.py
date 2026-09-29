import pytest

from forecast_engine import parsing
from forecast_engine.parsing import ParseError


def test_binary_takes_last_probability_and_clips():
    text = "Earlier I thought Probability: 50%.\n...\nProbability: 99.6%"
    assert parsing.parse_binary(text) == 0.99
    assert parsing.parse_binary("**Probability: 37%**") == pytest.approx(0.37)
    assert parsing.parse_binary("Probability: 0%") == 0.01


def test_binary_missing_raises():
    with pytest.raises(ParseError):
        parsing.parse_binary("I think it is likely, around seventy percent.")


def test_multiple_choice_percentages_and_prefixes():
    text = "blah\nOption_A: 10%\n- Yes, fully: 60%\n**No**: 30%"
    out = parsing.parse_multiple_choice(text, ["Option_A", "Yes, fully", "No"])
    assert out == pytest.approx({"Option_A": 0.1, "Yes, fully": 0.6, "No": 0.3})


def test_multiple_choice_fractions_zero_floor_and_normalization():
    out = parsing.parse_multiple_choice("A: 0.7\nB: 0.3\nC: 0", ["A", "B", "C"])
    assert sum(out.values()) == pytest.approx(1.0)
    assert out["C"] > 0


def test_multiple_choice_placeholder_letters_map_positionally():
    out = parsing.parse_multiple_choice("Option_A: 0.80\nOption_B: 0.18\nOption_C: 0.02", ["Dem", "Rep", "Other"])
    assert out == pytest.approx({"Dem": 0.8, "Rep": 0.18, "Other": 0.02})
    with pytest.raises(ParseError):  # a missing letter is still a failure
        parsing.parse_multiple_choice("Option_A: 0.80\nOption_B: 0.20", ["Dem", "Rep", "Other"])


def test_multiple_choice_bad_sum_or_missing_option_raises():
    with pytest.raises(ParseError):
        parsing.parse_multiple_choice("A: 70%\nB: 70%", ["A", "B"])
    with pytest.raises(ParseError):
        parsing.parse_multiple_choice("A: 70%", ["A", "B"])


NUMERIC = """
Percentile 10: 1,200 (lowest number value)
Percentile 20: 1,500
Percentile 40: $1,800
Percentile 60: 2000
Percentile 80: 2,400.5
Percentile 90: 3000 (highest number value)
"""


def test_numeric_happy_path():
    out = parsing.parse_numeric(NUMERIC)
    assert out == [(0.1, 1200), (0.2, 1500), (0.4, 1800), (0.6, 2000), (0.8, 2400.5), (0.9, 3000)]


def test_numeric_allows_question_unit_but_rejects_magnitude_words():
    text = NUMERIC.replace("2000", "2000 GW")
    assert parsing.parse_numeric(text, unit="GW")[3] == (0.6, 2000)
    with pytest.raises(ParseError):
        parsing.parse_numeric(NUMERIC.replace("2000", "2 million"))
    with pytest.raises(ParseError):
        parsing.parse_numeric(NUMERIC.replace("2000", "2k"))


def test_numeric_ties_allowed_and_broken_for_submission():
    text = "Percentile 10: 0\nPercentile 20: 0\nPercentile 40: 1\nPercentile 60: 1\nPercentile 80: 2\nPercentile 90: 3"
    pts = parsing.parse_numeric(text)
    assert [v for _, v in pts] == [0, 0, 1, 1, 2, 3]
    strict = parsing.make_strictly_increasing(pts, scale=10)
    assert all(b[1] > a[1] for a, b in zip(strict, strict[1:]))
    assert strict[1][1] - strict[0][1] < 1e-3


def test_numeric_rejects_decreasing_and_missing():
    with pytest.raises(ParseError):
        parsing.parse_numeric(NUMERIC.replace("2000", "1700"))
    with pytest.raises(ParseError):
        parsing.parse_numeric(NUMERIC.replace("Percentile 90: 3000 (highest number value)", ""))


def test_medians():
    assert parsing.median_binary([0.2, 0.9, 0.4]) == 0.4
    mc = parsing.median_multiple_choice([{"a": 0.5, "b": 0.5}, {"a": 0.9, "b": 0.1}, {"a": 0.1, "b": 0.9}])
    assert mc == pytest.approx({"a": 0.5, "b": 0.5})
    num = parsing.median_numeric([[(0.1, 1), (0.9, 5)], [(0.1, 2), (0.9, 3)], [(0.1, 0), (0.9, 10)]])
    assert num == [(0.1, 1), (0.9, 5)]
    assert all(b[1] > a[1] for a, b in zip(num, num[1:]))
