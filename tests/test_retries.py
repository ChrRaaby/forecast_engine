from datetime import datetime, timedelta, timezone

from forecast_engine.retries import COOLDOWN, MAX_FAILURES, RetryLedger

T0 = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)


def test_blocks_after_max_failures_then_cools_down(tmp_path):
    path = tmp_path / "retries.json"
    led = RetryLedger("v1", path)
    for i in range(MAX_FAILURES):
        assert led.blocked(46123, T0) is None
        led.failed(46123, T0 + timedelta(minutes=10 * i))
    last = T0 + timedelta(minutes=10 * (MAX_FAILURES - 1))
    assert "failed 3 times" in led.blocked(46123, last + timedelta(minutes=10))
    assert led.blocked(99, last) is None  # other questions unaffected
    led.save()
    again = RetryLedger("v1", path)  # survives a new run
    assert again.blocked(46123, last + COOLDOWN - timedelta(seconds=1))
    assert again.blocked(46123, last + COOLDOWN) is None  # cooldown over
    again.failed(46123, last + COOLDOWN)
    assert again.entries["46123"]["failures"] == 1  # a fresh set of tries, not blocked forever


def test_success_clears_and_config_bump_resets(tmp_path):
    path = tmp_path / "retries.json"
    led = RetryLedger("v1", path)
    for _ in range(MAX_FAILURES):
        led.failed(1, T0)
        led.failed(2, T0)
    led.succeeded(1)
    assert led.blocked(1, T0) is None and led.blocked(2, T0)
    led.save()
    assert RetryLedger("v2", path).blocked(2, T0) is None  # a new CONFIG_VERSION gets fresh tries


def test_missing_or_corrupt_file_means_no_history(tmp_path):
    assert RetryLedger("v1", tmp_path / "nope.json").entries == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert RetryLedger("v1", bad).entries == {}
