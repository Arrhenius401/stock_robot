"""真实进程超时边界测试，无网络请求。"""
import time

import pytest

from radar.provider_request import ProviderRequestError, bounded_provider_call


def test_bounded_request_returns_result_from_child():
    result = bounded_provider_call("builtins", "sum", args=([1, 2],), timeout=10)
    assert result == 3


def test_hung_sdk_is_terminated_instead_of_leaving_a_background_request():
    import multiprocessing

    before = {process.pid for process in multiprocessing.active_children()}
    started = time.monotonic()
    with pytest.raises(ProviderRequestError, match="超时"):
        bounded_provider_call("time", "sleep", args=(20,), timeout=0.2)
    assert time.monotonic() - started < 5
    assert {process.pid for process in multiprocessing.active_children()} == before


def test_child_exception_is_diagnostic_and_bounded():
    with pytest.raises(ProviderRequestError, match="ValueError"):
        bounded_provider_call("builtins", "int", args=("invalid",), timeout=10)
