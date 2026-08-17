from http.client import RemoteDisconnected

import pytest

from utils.retry import retry_on_network_error


def test_retries_then_succeeds(mocker):
    mocker.patch("utils.retry.time.sleep")
    calls = {"n": 0}

    @retry_on_network_error(max_attempts=3, base_delay=0.01)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise RemoteDisconnected("boom")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_gives_up_after_max_attempts(mocker):
    mocker.patch("utils.retry.time.sleep")
    calls = {"n": 0}

    @retry_on_network_error(max_attempts=3, base_delay=0.01)
    def always_fail():
        calls["n"] += 1
        raise RemoteDisconnected("boom")

    with pytest.raises(RemoteDisconnected):
        always_fail()
    assert calls["n"] == 3


def test_non_network_error_not_retried(mocker):
    mocker.patch("utils.retry.time.sleep")
    calls = {"n": 0}

    @retry_on_network_error(max_attempts=3, base_delay=0.01)
    def bad():
        calls["n"] += 1
        raise ValueError("logic")

    with pytest.raises(ValueError):
        bad()
    assert calls["n"] == 1
