from app.checks import disk_space
from app.winrm_executor_stub import WinrmExecutorStub


def test_single_disk():
    executor = WinrmExecutorStub(
        responses={
            ("host-a", disk_space.CHECK_FUNCTION): (
                '[{"DriveLetter": "C:", "SizeBytes": 107374182400, "FreeBytes": 53687091200}]'
            ),
        }
    )
    result = disk_space.run(executor, "host-a")
    assert result.ok is True
    assert result.value == {
        "disks": [{"drive_letter": "C:", "size_bytes": 107374182400, "free_bytes": 53687091200}]
    }


def test_multiple_disks():
    executor = WinrmExecutorStub(
        responses={
            ("host-b", disk_space.CHECK_FUNCTION): (
                '[{"DriveLetter": "C:", "SizeBytes": 100, "FreeBytes": 40},'
                ' {"DriveLetter": "D:", "SizeBytes": 200, "FreeBytes": 150}]'
            ),
        }
    )
    result = disk_space.run(executor, "host-b")
    assert result.ok is True
    assert result.value == {
        "disks": [
            {"drive_letter": "C:", "size_bytes": 100, "free_bytes": 40},
            {"drive_letter": "D:", "size_bytes": 200, "free_bytes": 150},
        ]
    }


def test_no_disks_empty_array():
    # Shouldn't happen for a real fixed-disk host, but a genuinely empty
    # array is a well-formed (if unusual) response, not an error.
    executor = WinrmExecutorStub(responses={("host-c", disk_space.CHECK_FUNCTION): "[]"})
    result = disk_space.run(executor, "host-c")
    assert result.ok is True
    assert result.value == {"disks": []}


def test_bare_object_not_array_becomes_ok_false():
    # The exact ConvertTo-Json-without-AsArray gotcha this check guards
    # against: a single-disk host whose JEA function forgot -AsArray
    # would produce a bare object here, not a one-element array.
    executor = WinrmExecutorStub(
        responses={
            ("host-d", disk_space.CHECK_FUNCTION): (
                '{"DriveLetter": "C:", "SizeBytes": 100, "FreeBytes": 40}'
            ),
        }
    )
    result = disk_space.run(executor, "host-d")
    assert result.ok is False
    assert "expected a JSON array" in result.error


def test_executor_failure_becomes_ok_false():
    executor = WinrmExecutorStub(
        failures={("host-e", disk_space.CHECK_FUNCTION): "kerberos auth failed"}
    )
    result = disk_space.run(executor, "host-e")
    assert result.ok is False
    assert "kerberos auth failed" in result.error


def test_unparseable_output_becomes_ok_false():
    executor = WinrmExecutorStub(responses={("host-f", disk_space.CHECK_FUNCTION): "not json"})
    result = disk_space.run(executor, "host-f")
    assert result.ok is False
    assert result.error is not None


def test_non_object_disk_entry_becomes_ok_false():
    executor = WinrmExecutorStub(responses={("host-g", disk_space.CHECK_FUNCTION): "[1, 2]"})
    result = disk_space.run(executor, "host-g")
    assert result.ok is False
    assert "not an object" in result.error


def test_missing_key_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={("host-h", disk_space.CHECK_FUNCTION): '[{"DriveLetter": "C:", "SizeBytes": 100}]'}
    )
    result = disk_space.run(executor, "host-h")
    assert result.ok is False
    assert "FreeBytes" in result.error


def test_non_integer_bytes_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={
            ("host-i", disk_space.CHECK_FUNCTION): (
                '[{"DriveLetter": "C:", "SizeBytes": "100", "FreeBytes": 40}]'
            ),
        }
    )
    result = disk_space.run(executor, "host-i")
    assert result.ok is False
    assert "non-integer" in result.error


def test_empty_drive_letter_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={("host-j", disk_space.CHECK_FUNCTION): '[{"DriveLetter": "", "SizeBytes": 100, "FreeBytes": 40}]'}
    )
    result = disk_space.run(executor, "host-j")
    assert result.ok is False
    assert "DriveLetter" in result.error


def test_invokes_only_the_whitelisted_function_name():
    executor = WinrmExecutorStub(responses={("host-k", disk_space.CHECK_FUNCTION): "[]"})
    disk_space.run(executor, "host-k")

    assert executor.calls == [("host-k", "Get-LocalDiskSpace")]
