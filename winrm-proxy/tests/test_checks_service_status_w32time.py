from app.checks import service_status_w32time
from app.winrm_executor_stub import WinrmExecutorStub


def test_running():
    executor = WinrmExecutorStub(
        responses={("host-a", service_status_w32time.CHECK_FUNCTION): '"Running"'}
    )
    result = service_status_w32time.run(executor, "host-a")
    assert result.ok is True
    assert result.value == {"status": "Running"}


def test_stopped():
    executor = WinrmExecutorStub(
        responses={("host-b", service_status_w32time.CHECK_FUNCTION): '"Stopped"'}
    )
    result = service_status_w32time.run(executor, "host-b")
    assert result.ok is True
    assert result.value == {"status": "Stopped"}


def test_executor_failure_becomes_ok_false():
    executor = WinrmExecutorStub(
        failures={("host-c", service_status_w32time.CHECK_FUNCTION): "kerberos auth failed"}
    )
    result = service_status_w32time.run(executor, "host-c")
    assert result.ok is False
    assert "kerberos auth failed" in result.error


def test_unparseable_output_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={("host-d", service_status_w32time.CHECK_FUNCTION): "not json at all"}
    )
    result = service_status_w32time.run(executor, "host-d")
    assert result.ok is False
    assert result.error is not None


def test_non_string_json_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={("host-e", service_status_w32time.CHECK_FUNCTION): '{"Status": "Running"}'}
    )
    result = service_status_w32time.run(executor, "host-e")
    assert result.ok is False


def test_empty_string_becomes_ok_false():
    executor = WinrmExecutorStub(responses={("host-f", service_status_w32time.CHECK_FUNCTION): '""'})
    result = service_status_w32time.run(executor, "host-f")
    assert result.ok is False


def test_invokes_only_the_whitelisted_function_name():
    # Whitelist-alignment sanity check: this must be the exact function
    # name the JEA Role Capability whitelists in VisibleFunctions --
    # not a cmdlet, not anything parameterized, and not shared with
    # the wuauserv check (one function per service, decided).
    executor = WinrmExecutorStub(
        responses={("host-g", service_status_w32time.CHECK_FUNCTION): '"Running"'}
    )
    service_status_w32time.run(executor, "host-g")

    assert executor.calls == [("host-g", "Get-W32timeStatus")]
