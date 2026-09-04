from app.checks import reboot_pending
from app.winrm_executor_stub import WinrmExecutorStub


def test_no_reboot_pending():
    executor = WinrmExecutorStub()  # default: both flags false
    result = reboot_pending.run(executor, "host-a")
    assert result.ok is True
    assert result.value == {"reboot_pending": False}


def test_reboot_pending_true_via_reboot_required_flag():
    executor = WinrmExecutorStub(
        responses={
            ("host-b", reboot_pending.CHECK_FUNCTION): (
                '{"RebootRequired": true, "PendingFileRenameOperations": false}'
            ),
        }
    )
    result = reboot_pending.run(executor, "host-b")
    assert result.ok is True
    assert result.value == {"reboot_pending": True}


def test_reboot_pending_true_via_pending_rename_flag():
    executor = WinrmExecutorStub(
        responses={
            ("host-c", reboot_pending.CHECK_FUNCTION): (
                '{"RebootRequired": false, "PendingFileRenameOperations": true}'
            ),
        }
    )
    result = reboot_pending.run(executor, "host-c")
    assert result.ok is True
    assert result.value == {"reboot_pending": True}


def test_executor_failure_becomes_ok_false():
    executor = WinrmExecutorStub(
        failures={("host-d", reboot_pending.CHECK_FUNCTION): "kerberos auth failed"}
    )
    result = reboot_pending.run(executor, "host-d")
    assert result.ok is False
    assert "kerberos auth failed" in result.error


def test_unparseable_output_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={("host-e", reboot_pending.CHECK_FUNCTION): "not json at all"}
    )
    result = reboot_pending.run(executor, "host-e")
    assert result.ok is False
    assert result.error is not None


def test_non_object_json_becomes_ok_false():
    executor = WinrmExecutorStub(responses={("host-f", reboot_pending.CHECK_FUNCTION): "[1, 2, 3]"})
    result = reboot_pending.run(executor, "host-f")
    assert result.ok is False


def test_missing_keys_default_to_false():
    executor = WinrmExecutorStub(responses={("host-g", reboot_pending.CHECK_FUNCTION): "{}"})
    result = reboot_pending.run(executor, "host-g")
    assert result.ok is True
    assert result.value == {"reboot_pending": False}


def test_invokes_only_the_whitelisted_function_name():
    # Whitelist-alignment sanity check: this must be the exact function
    # name the JEA Role Capability whitelists in VisibleFunctions --
    # not a cmdlet, not anything parameterized.
    executor = WinrmExecutorStub()
    reboot_pending.run(executor, "host-h")

    assert executor.calls == [("host-h", "Get-RebootPendingStatus")]
