"""Focused tests for WinrmExecutorPypsrp's opt-in session pooling
(session_pooling_enabled=True), added alongside the follow-up to the
concurrency-limiting fix -- see docs/WINRM_PROXY_CONCURRENCY.md
(librenms-fork) for the design tradeoffs this is testing against:
staleness handling (optimistic reuse + evict-and-retry-once),
thread-safety (per-host lock), and sizing/eviction (LRU cap + idle
timeout).

Same approach as test_winrm_executor_pypsrp_concurrency.py: mock just
enough of pypsrp's WSMan/RunspacePool/PowerShell to control call counts
and timing precisely, real threading where the property under test is
genuinely about concurrent access.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from app.winrm_executor import ExecutorError
from app.winrm_executor_pypsrp import WinrmExecutorPypsrp


def _patch_pypsrp(invoke_side_effect=None, had_errors=False):
    mock_ps = MagicMock()
    mock_ps.__enter__ = MagicMock(return_value=mock_ps)
    mock_ps.__exit__ = MagicMock(return_value=False)
    mock_ps.had_errors = had_errors
    mock_ps.streams.error = []
    if invoke_side_effect is not None:
        mock_ps.invoke.side_effect = invoke_side_effect
    else:
        mock_ps.invoke.return_value = ["ok"]

    # RunspacePool instances are used both as a context manager
    # (_invoke_fresh) and via explicit __enter__/__exit__ (pooled path)
    # -- each call to RunspacePool(...) returns a fresh MagicMock so
    # call-count assertions can tell distinct pool instances apart.
    def make_pool(*args, **kwargs):
        pool = MagicMock()
        pool.__enter__ = MagicMock(return_value=pool)
        pool.__exit__ = MagicMock(return_value=False)
        return pool

    def make_wsman(*args, **kwargs):
        wsman = MagicMock()
        wsman.__enter__ = MagicMock(return_value=wsman)
        wsman.__exit__ = MagicMock(return_value=False)
        return wsman

    return (
        patch("app.winrm_executor_pypsrp.WSMan", side_effect=make_wsman),
        patch("app.winrm_executor_pypsrp.RunspacePool", side_effect=make_pool),
        patch("app.winrm_executor_pypsrp.PowerShell", return_value=mock_ps),
        mock_ps,
    )


def test_pooling_disabled_by_default_builds_fresh_session_every_call():
    p1, p2, p3, mock_ps = _patch_pypsrp()
    with p1 as mock_wsman, p2, p3:
        executor = WinrmExecutorPypsrp()
        executor.invoke_function("host-a", "Get-Something")
        executor.invoke_function("host-a", "Get-Something")

        assert mock_wsman.call_count == 2


def test_pooling_enabled_reuses_session_for_same_host():
    p1, p2, p3, mock_ps = _patch_pypsrp()
    with p1 as mock_wsman, p2, p3:
        executor = WinrmExecutorPypsrp(session_pooling_enabled=True)
        executor.invoke_function("host-a", "Get-Something")
        executor.invoke_function("host-a", "Get-Something")
        executor.invoke_function("host-a", "Get-Something")

        assert mock_wsman.call_count == 1


def test_pooling_enabled_uses_separate_sessions_per_host():
    p1, p2, p3, mock_ps = _patch_pypsrp()
    with p1 as mock_wsman, p2, p3:
        executor = WinrmExecutorPypsrp(session_pooling_enabled=True)
        executor.invoke_function("host-a", "Get-Something")
        executor.invoke_function("host-b", "Get-Something")
        executor.invoke_function("host-a", "Get-Something")

        assert mock_wsman.call_count == 2


def test_stale_pooled_session_is_evicted_and_retried_once():
    # First call succeeds and pools the session. Second call fails
    # through that pooled session (simulating a dead connection) --
    # must be evicted and transparently retried with a fresh session,
    # which succeeds.
    call_count = 0

    def flaky_invoke(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("connection reset (simulated staleness)")
        return ["ok"]

    p1, p2, p3, mock_ps = _patch_pypsrp(invoke_side_effect=flaky_invoke)
    with p1 as mock_wsman, p2, p3:
        executor = WinrmExecutorPypsrp(session_pooling_enabled=True)

        result1 = executor.invoke_function("host-a", "Get-Something")
        assert result1 == "ok"
        assert mock_wsman.call_count == 1

        # This call's underlying invoke() raises -> pooled session
        # evicted -> retried once with a brand-new WSMan/session ->
        # succeeds (call_count is now 3, which returns ["ok"]).
        result2 = executor.invoke_function("host-a", "Get-Something")
        assert result2 == "ok"
        assert mock_wsman.call_count == 2  # evicted + rebuilt


def test_genuine_failure_propagates_after_one_retry():
    p1, p2, p3, mock_ps = _patch_pypsrp(invoke_side_effect=RuntimeError("always broken"))
    with p1, p2, p3:
        executor = WinrmExecutorPypsrp(session_pooling_enabled=True)

        with pytest.raises(ExecutorError, match="always broken"):
            executor.invoke_function("host-a", "Get-Something")


def test_idle_pooled_session_evicted_and_rebuilt():
    p1, p2, p3, mock_ps = _patch_pypsrp()
    with p1 as mock_wsman, p2, p3:
        executor = WinrmExecutorPypsrp(
            session_pooling_enabled=True,
            pooled_session_idle_timeout_seconds=1,
        )
        executor.invoke_function("host-a", "Get-Something")
        assert mock_wsman.call_count == 1

        # Directly age the pooled session past the idle timeout rather
        # than sleeping -- last_used is a plain monotonic timestamp.
        session = executor._pooled_sessions["host-a"]
        session.last_used = time.monotonic() - 10

        executor.invoke_function("host-a", "Get-Something")
        assert mock_wsman.call_count == 2


def test_lru_eviction_when_over_max_pooled_sessions():
    p1, p2, p3, mock_ps = _patch_pypsrp()
    with p1 as mock_wsman, p2, p3:
        executor = WinrmExecutorPypsrp(session_pooling_enabled=True, max_pooled_sessions=1)

        executor.invoke_function("host-a", "Get-Something")
        executor.invoke_function("host-b", "Get-Something")  # evicts host-a's session
        assert mock_wsman.call_count == 2
        assert "host-a" not in executor._pooled_sessions
        assert "host-b" in executor._pooled_sessions

        # host-a must be rebuilt from scratch now.
        executor.invoke_function("host-a", "Get-Something")
        assert mock_wsman.call_count == 3


def test_per_host_lock_serializes_concurrent_calls_to_same_host():
    # Two threads calling the same host concurrently through the
    # pooled path must not run PowerShell.invoke() overlapping --
    # proven with a real Event-based rendezvous, not just trusting the
    # lock exists.
    in_flight = threading.Event()
    overlap_detected = threading.Event()
    release = threading.Event()

    def serialized_invoke(*args, **kwargs):
        if in_flight.is_set():
            overlap_detected.set()
        in_flight.set()
        release.wait(timeout=5)
        in_flight.clear()
        return ["ok"]

    p1, p2, p3, mock_ps = _patch_pypsrp(invoke_side_effect=serialized_invoke)
    with p1, p2, p3:
        executor = WinrmExecutorPypsrp(session_pooling_enabled=True, max_concurrent_connections=10)

        results = []
        t1 = threading.Thread(target=lambda: results.append(executor.invoke_function("host-a", "Fn")))
        t1.start()

        assert in_flight.wait(timeout=2), "first call never reached invoke()"

        t2 = threading.Thread(target=lambda: results.append(executor.invoke_function("host-a", "Fn")))
        t2.start()

        # Give t2 a real chance to (wrongly) race in before releasing
        # the first call.
        time.sleep(0.2)
        release.set()
        t1.join(timeout=5)

        release.set()  # in case t2 is the one now blocked in invoke()
        t2.join(timeout=5)

        assert not overlap_detected.is_set()
        assert results == ["ok", "ok"]
