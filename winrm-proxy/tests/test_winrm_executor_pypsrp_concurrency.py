"""Focused tests for WinrmExecutorPypsrp's concurrency limiting and
explicit-timeout wiring, added alongside the fix for the cascading-
failure risk described in docs/WINRM_PROXY_CONCURRENCY.md.

Deliberately NOT a full test suite for WinrmExecutorPypsrp as a whole
-- that class has only ever been validated against the real target
(see its own module docstring: "confirmed against its actual installed
source", "confirmed by direct port check"), consistent with this
project's established pattern of testing pypsrp/Kerberos integration
for real rather than mocking it. The semaphore/timeout logic added
here, though, is genuine pure-Python control flow independent of any
real WinRM connection -- worth real automated coverage on its own,
via mocking just enough of pypsrp's WSMan/RunspacePool/PowerShell to
control timing precisely.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from app.winrm_executor import ExecutorError
from app.winrm_executor_pypsrp import WinrmExecutorPypsrp


def _patch_pypsrp(invoke_side_effect=None, had_errors=False):
    """Patches WSMan/RunspacePool/PowerShell so invoke_function() runs
    its real control flow (semaphore, timeout kwargs passed to WSMan,
    exception translation) without touching a real network connection.
    """
    mock_ps = MagicMock()
    mock_ps.__enter__ = MagicMock(return_value=mock_ps)
    mock_ps.__exit__ = MagicMock(return_value=False)
    mock_ps.had_errors = had_errors
    mock_ps.streams.error = []
    if invoke_side_effect is not None:
        mock_ps.invoke.side_effect = invoke_side_effect
    else:
        mock_ps.invoke.return_value = ["ok"]

    mock_pool_cm = MagicMock()
    mock_pool_cm.__enter__ = MagicMock(return_value=MagicMock())
    mock_pool_cm.__exit__ = MagicMock(return_value=False)

    mock_wsman_instance = MagicMock()
    mock_wsman_instance.__enter__ = MagicMock(return_value=mock_wsman_instance)
    mock_wsman_instance.__exit__ = MagicMock(return_value=False)

    return (
        patch("app.winrm_executor_pypsrp.WSMan", return_value=mock_wsman_instance),
        patch("app.winrm_executor_pypsrp.RunspacePool", return_value=mock_pool_cm),
        patch("app.winrm_executor_pypsrp.PowerShell", return_value=mock_ps),
        mock_ps,
    )


def test_explicit_timeouts_passed_to_wsman():
    p1, p2, p3, mock_ps = _patch_pypsrp()
    with p1 as mock_wsman_cls, p2, p3:
        executor = WinrmExecutorPypsrp(
            operation_timeout_seconds=7,
            connection_timeout_seconds=11,
            read_timeout_seconds=13,
        )
        executor.invoke_function("host-a", "Get-Something")

        _, kwargs = mock_wsman_cls.call_args
        assert kwargs["operation_timeout"] == 7
        assert kwargs["connection_timeout"] == 11
        assert kwargs["read_timeout"] == 13


def test_semaphore_bounds_concurrent_connections():
    # max_concurrent_connections=1: a second call must block until the
    # first releases its slot. Uses real threads + a real blocking
    # invoke() (via an Event) to prove actual blocking, not just that
    # the semaphore object exists.
    first_call_holding = threading.Event()
    release_first_call = threading.Event()

    def slow_invoke(*args, **kwargs):
        first_call_holding.set()
        release_first_call.wait(timeout=5)
        return ["ok"]

    p1, p2, p3, mock_ps = _patch_pypsrp(invoke_side_effect=slow_invoke)
    with p1, p2, p3:
        executor = WinrmExecutorPypsrp(max_concurrent_connections=1, connection_queue_timeout_seconds=5)

        results = []
        t1 = threading.Thread(target=lambda: results.append(executor.invoke_function("host-a", "Fn")))
        t1.start()

        assert first_call_holding.wait(timeout=2), "first call never reached invoke()"

        # Second call should NOT be able to acquire the semaphore yet --
        # prove it by checking the slot is genuinely held, not just
        # trusting timing.
        assert executor._connection_semaphore.acquire(timeout=0.2) is False

        release_first_call.set()
        t1.join(timeout=5)
        assert results == ["ok"]

        # Now that the first call released its slot, a fresh acquire
        # succeeds immediately.
        assert executor._connection_semaphore.acquire(timeout=0.2) is True
        executor._connection_semaphore.release()


def test_connection_queue_timeout_fails_loud_not_hangs_forever():
    # Hold the only slot, then confirm a second real invoke_function()
    # call gives up with a clear ExecutorError within roughly the
    # configured queue timeout, rather than blocking indefinitely.
    p1, p2, p3, mock_ps = _patch_pypsrp()
    with p1, p2, p3:
        executor = WinrmExecutorPypsrp(max_concurrent_connections=1, connection_queue_timeout_seconds=1)
        executor._connection_semaphore.acquire()  # simulate a slot already held

        t0 = time.monotonic()
        with pytest.raises(ExecutorError, match="maximum concurrent connections"):
            executor.invoke_function("host-b", "Fn")
        elapsed = time.monotonic() - t0

        assert 0.9 <= elapsed <= 3.0, f"expected to fail loud around 1s, took {elapsed:.2f}s"


def test_semaphore_released_after_error_so_next_call_succeeds():
    p1, p2, p3, mock_ps = _patch_pypsrp(had_errors=True)
    with p1, p2, p3:
        mock_ps.streams.error = ["boom"]
        executor = WinrmExecutorPypsrp(max_concurrent_connections=1)

        with pytest.raises(ExecutorError, match="boom"):
            executor.invoke_function("host-c", "Fn")

        # The slot must have been released despite the failure, or
        # every subsequent call would wrongly queue/timeout forever.
        assert executor._connection_semaphore.acquire(timeout=0.2) is True
        executor._connection_semaphore.release()
