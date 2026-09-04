from app.checks import cpu_usage
from app.winrm_executor_stub import WinrmExecutorStub


def test_multiple_cores():
    executor = WinrmExecutorStub(
        responses={
            ("host-a", cpu_usage.CHECK_FUNCTION): (
                '[{"CoreIndex": "0", "BusyPercent": 3.25}, {"CoreIndex": "1", "BusyPercent": 6.32}]'
            ),
        }
    )
    result = cpu_usage.run(executor, "host-a")
    assert result.ok is True
    assert result.value == {
        "cores": [
            {"core_index": "0", "busy_percent": 3.25},
            {"core_index": "1", "busy_percent": 6.32},
        ]
    }


def test_single_core():
    executor = WinrmExecutorStub(
        responses={("host-b", cpu_usage.CHECK_FUNCTION): '[{"CoreIndex": "0", "BusyPercent": 0.0}]'},
    )
    result = cpu_usage.run(executor, "host-b")
    assert result.ok is True
    assert result.value == {"cores": [{"core_index": "0", "busy_percent": 0.0}]}


def test_integer_busy_percent_accepted():
    # CookedValue can come back as a whole number -- ConvertTo-Json
    # then serializes it without a decimal point, still valid.
    executor = WinrmExecutorStub(
        responses={("host-c", cpu_usage.CHECK_FUNCTION): '[{"CoreIndex": "0", "BusyPercent": 0}]'},
    )
    result = cpu_usage.run(executor, "host-c")
    assert result.ok is True
    assert result.value == {"cores": [{"core_index": "0", "busy_percent": 0}]}


def test_bare_object_not_array_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={("host-d", cpu_usage.CHECK_FUNCTION): '{"CoreIndex": "0", "BusyPercent": 3.25}'},
    )
    result = cpu_usage.run(executor, "host-d")
    assert result.ok is False
    assert "expected a JSON array" in result.error


def test_executor_failure_becomes_ok_false():
    executor = WinrmExecutorStub(failures={("host-e", cpu_usage.CHECK_FUNCTION): "kerberos auth failed"})
    result = cpu_usage.run(executor, "host-e")
    assert result.ok is False
    assert "kerberos auth failed" in result.error


def test_unparseable_output_becomes_ok_false():
    executor = WinrmExecutorStub(responses={("host-f", cpu_usage.CHECK_FUNCTION): "not json"})
    result = cpu_usage.run(executor, "host-f")
    assert result.ok is False
    assert result.error is not None


def test_non_object_core_entry_becomes_ok_false():
    executor = WinrmExecutorStub(responses={("host-g", cpu_usage.CHECK_FUNCTION): "[1, 2]"})
    result = cpu_usage.run(executor, "host-g")
    assert result.ok is False
    assert "not an object" in result.error


def test_missing_key_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={("host-h", cpu_usage.CHECK_FUNCTION): '[{"CoreIndex": "0"}]'},
    )
    result = cpu_usage.run(executor, "host-h")
    assert result.ok is False
    assert "BusyPercent" in result.error


def test_non_numeric_busy_percent_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={("host-i", cpu_usage.CHECK_FUNCTION): '[{"CoreIndex": "0", "BusyPercent": "3.25"}]'},
    )
    result = cpu_usage.run(executor, "host-i")
    assert result.ok is False
    assert "non-numeric" in result.error


def test_empty_core_index_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={("host-j", cpu_usage.CHECK_FUNCTION): '[{"CoreIndex": "", "BusyPercent": 3.25}]'},
    )
    result = cpu_usage.run(executor, "host-j")
    assert result.ok is False
    assert "CoreIndex" in result.error


def test_no_cores_empty_array():
    # Shouldn't happen for a real host, but a well-formed empty array
    # isn't an error.
    executor = WinrmExecutorStub(responses={("host-k", cpu_usage.CHECK_FUNCTION): "[]"})
    result = cpu_usage.run(executor, "host-k")
    assert result.ok is True
    assert result.value == {"cores": []}


def test_invokes_only_the_whitelisted_function_name():
    executor = WinrmExecutorStub(responses={("host-l", cpu_usage.CHECK_FUNCTION): "[]"})
    cpu_usage.run(executor, "host-l")

    assert executor.calls == [("host-l", "Get-CpuUsageStatus")]
