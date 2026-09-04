from app.checks import memory_usage
from app.winrm_executor_stub import WinrmExecutorStub


def test_single_page_file():
    executor = WinrmExecutorStub(
        responses={
            ("host-a", memory_usage.CHECK_FUNCTION): (
                '{"PhysicalTotalBytes": 17179869184, "PhysicalFreeBytes": 8589934592,'
                ' "VirtualTotalBytes": 21474836480, "VirtualFreeBytes": 12884901888,'
                ' "PageFiles": [{"Name": "C:\\\\pagefile.sys", "AllocatedBytes": 4294967296, "UsedBytes": 1073741824}],'
                ' "LastBootUpTime": "2026-08-08T14:18:51.9044430Z"}'
            ),
        }
    )
    result = memory_usage.run(executor, "host-a")
    assert result.ok is True
    assert result.value == {
        "physical": {"total_bytes": 17179869184, "free_bytes": 8589934592},
        "virtual": {"total_bytes": 21474836480, "free_bytes": 12884901888},
        "page_files": [{"name": "C:\\pagefile.sys", "allocated_bytes": 4294967296, "used_bytes": 1073741824}],
        "last_boot_up_time": "2026-08-08T14:18:51.9044430Z",
    }


def test_multiple_page_files():
    executor = WinrmExecutorStub(
        responses={
            ("host-b", memory_usage.CHECK_FUNCTION): (
                '{"PhysicalTotalBytes": 100, "PhysicalFreeBytes": 40,'
                ' "VirtualTotalBytes": 200, "VirtualFreeBytes": 150,'
                ' "PageFiles": ['
                '{"Name": "C:\\\\pagefile.sys", "AllocatedBytes": 10, "UsedBytes": 2},'
                '{"Name": "D:\\\\swapfile.sys", "AllocatedBytes": 20, "UsedBytes": 5}'
                ']}'
            ),
        }
    )
    result = memory_usage.run(executor, "host-b")
    assert result.ok is True
    assert result.value["page_files"] == [
        {"name": "C:\\pagefile.sys", "allocated_bytes": 10, "used_bytes": 2},
        {"name": "D:\\swapfile.sys", "allocated_bytes": 20, "used_bytes": 5},
    ]


def test_no_page_files_empty_array():
    # A real, if unusual, configuration -- Windows doesn't guarantee a
    # page file exists.
    executor = WinrmExecutorStub(
        responses={
            ("host-c", memory_usage.CHECK_FUNCTION): (
                '{"PhysicalTotalBytes": 100, "PhysicalFreeBytes": 40,'
                ' "VirtualTotalBytes": 100, "VirtualFreeBytes": 40, "PageFiles": []}'
            ),
        }
    )
    result = memory_usage.run(executor, "host-c")
    assert result.ok is True
    assert result.value["page_files"] == []


def test_page_files_bare_object_not_array_becomes_ok_false():
    # The same ConvertTo-Json single-element-collapse gotcha disk_space
    # guards against, applied to the nested PageFiles property.
    executor = WinrmExecutorStub(
        responses={
            ("host-d", memory_usage.CHECK_FUNCTION): (
                '{"PhysicalTotalBytes": 100, "PhysicalFreeBytes": 40,'
                ' "VirtualTotalBytes": 100, "VirtualFreeBytes": 40,'
                ' "PageFiles": {"Name": "C:\\\\pagefile.sys", "AllocatedBytes": 10, "UsedBytes": 2}}'
            ),
        }
    )
    result = memory_usage.run(executor, "host-d")
    assert result.ok is False
    assert "PageFiles is not a JSON array" in result.error


def test_missing_scalar_key_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={
            ("host-e", memory_usage.CHECK_FUNCTION): (
                '{"PhysicalTotalBytes": 100, "VirtualTotalBytes": 100, "VirtualFreeBytes": 40, "PageFiles": []}'
            ),
        }
    )
    result = memory_usage.run(executor, "host-e")
    assert result.ok is False
    assert "PhysicalFreeBytes" in result.error


def test_missing_page_files_key_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={
            ("host-f", memory_usage.CHECK_FUNCTION): (
                '{"PhysicalTotalBytes": 100, "PhysicalFreeBytes": 40,'
                ' "VirtualTotalBytes": 100, "VirtualFreeBytes": 40}'
            ),
        }
    )
    result = memory_usage.run(executor, "host-f")
    assert result.ok is False
    assert "PageFiles" in result.error


def test_non_integer_scalar_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={
            ("host-g", memory_usage.CHECK_FUNCTION): (
                '{"PhysicalTotalBytes": "100", "PhysicalFreeBytes": 40,'
                ' "VirtualTotalBytes": 100, "VirtualFreeBytes": 40, "PageFiles": []}'
            ),
        }
    )
    result = memory_usage.run(executor, "host-g")
    assert result.ok is False
    assert "PhysicalTotalBytes" in result.error


def test_page_file_missing_key_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={
            ("host-h", memory_usage.CHECK_FUNCTION): (
                '{"PhysicalTotalBytes": 100, "PhysicalFreeBytes": 40,'
                ' "VirtualTotalBytes": 100, "VirtualFreeBytes": 40,'
                ' "PageFiles": [{"Name": "C:\\\\pagefile.sys", "AllocatedBytes": 10}]}'
            ),
        }
    )
    result = memory_usage.run(executor, "host-h")
    assert result.ok is False
    assert "UsedBytes" in result.error


def test_page_file_non_integer_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={
            ("host-i", memory_usage.CHECK_FUNCTION): (
                '{"PhysicalTotalBytes": 100, "PhysicalFreeBytes": 40,'
                ' "VirtualTotalBytes": 100, "VirtualFreeBytes": 40,'
                ' "PageFiles": [{"Name": "C:\\\\pagefile.sys", "AllocatedBytes": "10", "UsedBytes": 2}]}'
            ),
        }
    )
    result = memory_usage.run(executor, "host-i")
    assert result.ok is False
    assert "non-integer" in result.error


def test_page_file_empty_name_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={
            ("host-j", memory_usage.CHECK_FUNCTION): (
                '{"PhysicalTotalBytes": 100, "PhysicalFreeBytes": 40,'
                ' "VirtualTotalBytes": 100, "VirtualFreeBytes": 40,'
                ' "PageFiles": [{"Name": "", "AllocatedBytes": 10, "UsedBytes": 2}]}'
            ),
        }
    )
    result = memory_usage.run(executor, "host-j")
    assert result.ok is False
    assert "Name" in result.error


def test_missing_last_boot_up_time_key_is_ok_treated_as_null():
    # Older/cached output shape without the key at all -- same "don't
    # know" outcome as present-but-null, not an error.
    executor = WinrmExecutorStub(
        responses={
            ("host-p", memory_usage.CHECK_FUNCTION): (
                '{"PhysicalTotalBytes": 100, "PhysicalFreeBytes": 40,'
                ' "VirtualTotalBytes": 100, "VirtualFreeBytes": 40, "PageFiles": []}'
            ),
        }
    )
    result = memory_usage.run(executor, "host-p")
    assert result.ok is True
    assert result.value["last_boot_up_time"] is None


def test_null_last_boot_up_time_is_ok():
    # The JEA function's own explicit "registry/WMI read failed" result.
    executor = WinrmExecutorStub(
        responses={
            ("host-q", memory_usage.CHECK_FUNCTION): (
                '{"PhysicalTotalBytes": 100, "PhysicalFreeBytes": 40,'
                ' "VirtualTotalBytes": 100, "VirtualFreeBytes": 40, "PageFiles": [],'
                ' "LastBootUpTime": null}'
            ),
        }
    )
    result = memory_usage.run(executor, "host-q")
    assert result.ok is True
    assert result.value["last_boot_up_time"] is None


def test_non_string_last_boot_up_time_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={
            ("host-r", memory_usage.CHECK_FUNCTION): (
                '{"PhysicalTotalBytes": 100, "PhysicalFreeBytes": 40,'
                ' "VirtualTotalBytes": 100, "VirtualFreeBytes": 40, "PageFiles": [],'
                ' "LastBootUpTime": 12345}'
            ),
        }
    )
    result = memory_usage.run(executor, "host-r")
    assert result.ok is False
    assert "LastBootUpTime" in result.error


def test_bare_array_not_object_becomes_ok_false():
    executor = WinrmExecutorStub(responses={("host-k", memory_usage.CHECK_FUNCTION): "[]"})
    result = memory_usage.run(executor, "host-k")
    assert result.ok is False
    assert result.error is not None


def test_executor_failure_becomes_ok_false():
    executor = WinrmExecutorStub(failures={("host-l", memory_usage.CHECK_FUNCTION): "kerberos auth failed"})
    result = memory_usage.run(executor, "host-l")
    assert result.ok is False
    assert "kerberos auth failed" in result.error


def test_unparseable_output_becomes_ok_false():
    executor = WinrmExecutorStub(responses={("host-m", memory_usage.CHECK_FUNCTION): "not json"})
    result = memory_usage.run(executor, "host-m")
    assert result.ok is False
    assert result.error is not None


def test_invokes_only_the_whitelisted_function_name():
    executor = WinrmExecutorStub(
        responses={
            ("host-n", memory_usage.CHECK_FUNCTION): (
                '{"PhysicalTotalBytes": 100, "PhysicalFreeBytes": 40,'
                ' "VirtualTotalBytes": 100, "VirtualFreeBytes": 40, "PageFiles": []}'
            ),
        }
    )
    memory_usage.run(executor, "host-n")

    assert executor.calls == [("host-n", "Get-MemoryUsageStatus")]
