import time

import pytest


def test_each_test_has_a_time_limit(request):
    # getini only knows "timeout" while pytest-timeout is loaded: without it, a hanging test
    # would hold CI for hours again.
    assert float(request.config.getini("timeout")) == 60


@pytest.mark.timeout(1)
@pytest.mark.xfail(strict=True, raises=BaseException, reason="stopped by its one-second limit")
def test_a_test_past_its_limit_is_stopped():
    time.sleep(5)
