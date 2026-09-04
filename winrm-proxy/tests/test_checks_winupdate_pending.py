from app.checks import winupdate_pending
from app.winrm_executor_stub import WinrmExecutorStub


def test_zero_pending():
    executor = WinrmExecutorStub(
        responses={
            ("host-a", winupdate_pending.CHECK_FUNCTION): (
                '{"PendingCount": 0, "LastScanTime": "2026-08-10T00:55:04.0000000-07:00"}'
            ),
        }
    )
    result = winupdate_pending.run(executor, "host-a")
    assert result.ok is True
    assert result.value == {"pending_count": 0, "last_scan_time": "2026-08-10T00:55:04.0000000-07:00"}


def test_nonzero_pending():
    executor = WinrmExecutorStub(
        responses={
            ("host-b", winupdate_pending.CHECK_FUNCTION): (
                '{"PendingCount": 7, "LastScanTime": "2026-08-10T06:30:00.0000000-07:00"}'
            ),
        }
    )
    result = winupdate_pending.run(executor, "host-b")
    assert result.ok is True
    assert result.value == {"pending_count": 7, "last_scan_time": "2026-08-10T06:30:00.0000000-07:00"}


def test_fresh_host_never_scanned_both_null():
    # A real, valid state -- Windows hasn't logged a scan-completion
    # event on this host yet. ok=True with nulls, not an error.
    executor = WinrmExecutorStub(
        responses={("host-c", winupdate_pending.CHECK_FUNCTION): '{"PendingCount": null, "LastScanTime": null}'}
    )
    result = winupdate_pending.run(executor, "host-c")
    assert result.ok is True
    assert result.value == {"pending_count": None, "last_scan_time": None}


def test_last_scan_time_known_but_count_unparseable_independently_null():
    # LastScanTime set (an event exists) while PendingCount is null (the
    # JEA function's regex against the event message didn't match) --
    # a distinct, genuinely different state from "never scanned".
    executor = WinrmExecutorStub(
        responses={
            (
                "host-d",
                winupdate_pending.CHECK_FUNCTION,
            ): '{"PendingCount": null, "LastScanTime": "2026-08-10T06:30:00.0000000-07:00"}',
        }
    )
    result = winupdate_pending.run(executor, "host-d")
    assert result.ok is True
    assert result.value == {"pending_count": None, "last_scan_time": "2026-08-10T06:30:00.0000000-07:00"}


def test_executor_failure_becomes_ok_false():
    executor = WinrmExecutorStub(
        failures={("host-e", winupdate_pending.CHECK_FUNCTION): "kerberos auth failed"}
    )
    result = winupdate_pending.run(executor, "host-e")
    assert result.ok is False
    assert "kerberos auth failed" in result.error


def test_unparseable_output_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={("host-f", winupdate_pending.CHECK_FUNCTION): "not json at all"}
    )
    result = winupdate_pending.run(executor, "host-f")
    assert result.ok is False
    assert result.error is not None


def test_non_object_json_becomes_ok_false():
    executor = WinrmExecutorStub(responses={("host-g", winupdate_pending.CHECK_FUNCTION): "[1, 2, 3]"})
    result = winupdate_pending.run(executor, "host-g")
    assert result.ok is False


def test_missing_key_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={("host-h", winupdate_pending.CHECK_FUNCTION): '{"PendingCount": 3}'}
    )
    result = winupdate_pending.run(executor, "host-h")
    assert result.ok is False
    assert "LastScanTime" in result.error


def test_non_integer_non_null_pending_count_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={
            ("host-i", winupdate_pending.CHECK_FUNCTION): (
                '{"PendingCount": "3", "LastScanTime": "2026-08-10T06:30:00.0000000-07:00"}'
            ),
        }
    )
    result = winupdate_pending.run(executor, "host-i")
    assert result.ok is False
    assert "PendingCount" in result.error


def test_bool_pending_count_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={
            ("host-j", winupdate_pending.CHECK_FUNCTION): (
                '{"PendingCount": true, "LastScanTime": "2026-08-10T06:30:00.0000000-07:00"}'
            ),
        }
    )
    result = winupdate_pending.run(executor, "host-j")
    assert result.ok is False
    assert "PendingCount" in result.error


def test_non_string_non_null_last_scan_time_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={("host-k", winupdate_pending.CHECK_FUNCTION): '{"PendingCount": 1, "LastScanTime": 12345}'}
    )
    result = winupdate_pending.run(executor, "host-k")
    assert result.ok is False
    assert "LastScanTime" in result.error


def test_invokes_only_the_whitelisted_function_name():
    executor = WinrmExecutorStub(
        responses={
            ("host-l", winupdate_pending.CHECK_FUNCTION): '{"PendingCount": 0, "LastScanTime": null}',
        }
    )
    winupdate_pending.run(executor, "host-l")

    assert executor.calls == [("host-l", "Get-PendingUpdateStatus")]
