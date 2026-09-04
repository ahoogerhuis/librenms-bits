import json

from app.checks import hardware_inventory
from app.winrm_executor_stub import WinrmExecutorStub

_FULL_RAW = {
    "Bios": {"SerialNumber": "SN123", "Version": "1.0.0", "Manufacturer": "Dell Inc."},
    "BaseBoard": {"Manufacturer": "Dell Inc.", "Product": "0ABC123", "SerialNumber": "BB456"},
    "Chassis": {"Manufacturer": "Dell Inc.", "SerialNumber": "CH789", "AssetTag": "ASSET1"},
    "OperatingSystem": {
        "Caption": "Microsoft Windows Server 2019 Standard",
        "Version": "10.0.17763",
        "ReleaseId": "1809",
        "NumberOfLogicalProcessors": 1,
    },
    "Processors": [{"DeviceId": "CPU0", "Name": "Intel Xeon", "Manufacturer": "GenuineIntel"}],
    "Memory": [
        {
            "DeviceLocator": "DIMM 0",
            "CapacityBytes": 8589934592,
            "Manufacturer": "Samsung",
            "PartNumber": "M393A2K",
            "SerialNumber": "MEM1",
        }
    ],
    "Disks": [
        {
            "DeviceId": "\\\\.\\PHYSICALDRIVE0",
            "Model": "Samsung SSD 970",
            "SerialNumber": "DISK1",
            "InterfaceType": "SCSI",
            "FirmwareRevision": "2B2QEXM7",
        }
    ],
    "NetworkAdapters": [
        {"MacAddress": "AA:BB:CC:DD:EE:FF", "Name": "Intel I350", "Manufacturer": "Intel Corporation"}
    ],
    "ProcessorIdentifier": "Intel64 Family 6 Model 85 Stepping 4",
    "Contact": "it-support@vpp.local",
    "Location": "VPP HQ - Server Room 2",
}

# Real observed shape on the QEMU/Proxmox test VM -- BaseBoard fields
# all null, Chassis SerialNumber/AssetTag empty strings (not null),
# Bios.SerialNumber null. A legitimate real response, not malformed.
_SPARSE_RAW = {
    "Bios": {"SerialNumber": None, "Version": "4.2025.05-2", "Manufacturer": "Proxmox distribution of EDK II"},
    "BaseBoard": {"Manufacturer": None, "Product": None, "SerialNumber": None},
    "Chassis": {"Manufacturer": "QEMU", "SerialNumber": "", "AssetTag": ""},
    "OperatingSystem": {
        "Caption": "Microsoft Windows Server 2019 Standard",
        "Version": "10.0.17763",
        "ReleaseId": "1809",
        "NumberOfLogicalProcessors": 1,
    },
    "Processors": [{"DeviceId": "CPU0", "Name": "QEMU Virtual CPU version 2.5+", "Manufacturer": "GenuineIntel"}],
    "Memory": [
        {"DeviceLocator": "DIMM 0", "CapacityBytes": 8589934592, "Manufacturer": "QEMU", "PartNumber": None, "SerialNumber": None}
    ],
    "Disks": [
        {
            "DeviceId": "\\\\.\\PHYSICALDRIVE0",
            "Model": "Red Hat VirtIO SCSI Disk Device",
            "SerialNumber": None,
            "InterfaceType": "SCSI",
            "FirmwareRevision": "0001",
        }
    ],
    "NetworkAdapters": [
        {"MacAddress": "BC:24:11:15:C8:37", "Name": "Red Hat VirtIO Ethernet Adapter", "Manufacturer": "Red Hat, Inc."}
    ],
}


def test_full_data():
    executor = WinrmExecutorStub(
        responses={("host-a", hardware_inventory.CHECK_FUNCTION): json.dumps(_FULL_RAW)},
    )
    result = hardware_inventory.run(executor, "host-a")
    assert result.ok is True
    assert result.value["bios"] == {"serial_number": "SN123", "version": "1.0.0", "manufacturer": "Dell Inc."}
    assert result.value["operating_system"] == {
        "caption": "Microsoft Windows Server 2019 Standard",
        "version": "10.0.17763",
        "release_id": "1809",
        "number_of_logical_processors": 1,
    }
    assert result.value["processors"] == [{"device_id": "CPU0", "name": "Intel Xeon", "manufacturer": "GenuineIntel"}]
    assert result.value["memory"][0]["capacity_bytes"] == 8589934592
    assert result.value["disks"][0]["device_id"] == "\\\\.\\PHYSICALDRIVE0"
    assert result.value["network_adapters"][0]["mac_address"] == "AA:BB:CC:DD:EE:FF"
    assert result.value["processor_identifier"] == "Intel64 Family 6 Model 85 Stepping 4"
    assert result.value["contact"] == "it-support@vpp.local"
    assert result.value["location"] == "VPP HQ - Server Room 2"


def test_missing_processor_identifier_key_is_ok_treated_as_null():
    raw = dict(_FULL_RAW)
    del raw["ProcessorIdentifier"]
    executor = WinrmExecutorStub(responses={("host-w", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-w")
    assert result.ok is True
    assert result.value["processor_identifier"] is None


def test_null_processor_identifier_is_ok():
    raw = dict(_FULL_RAW)
    raw["ProcessorIdentifier"] = None
    executor = WinrmExecutorStub(responses={("host-x", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-x")
    assert result.ok is True
    assert result.value["processor_identifier"] is None


def test_non_string_processor_identifier_becomes_ok_false():
    raw = dict(_FULL_RAW)
    raw["ProcessorIdentifier"] = 12345
    executor = WinrmExecutorStub(responses={("host-y", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-y")
    assert result.ok is False
    assert "ProcessorIdentifier" in result.error


def test_missing_contact_and_location_keys_are_ok_treated_as_null():
    # The expected common case -- most hosts never have the
    # HKLM:\SOFTWARE\LibreNMS\ registry key configured at all.
    raw = dict(_FULL_RAW)
    del raw["Contact"]
    del raw["Location"]
    executor = WinrmExecutorStub(responses={("host-s", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-s")
    assert result.ok is True
    assert result.value["contact"] is None
    assert result.value["location"] is None


def test_null_contact_and_location_are_ok():
    raw = dict(_FULL_RAW)
    raw["Contact"] = None
    raw["Location"] = None
    executor = WinrmExecutorStub(responses={("host-t", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-t")
    assert result.ok is True
    assert result.value["contact"] is None
    assert result.value["location"] is None


def test_non_string_contact_becomes_ok_false():
    raw = dict(_FULL_RAW)
    raw["Contact"] = 12345
    executor = WinrmExecutorStub(responses={("host-u", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-u")
    assert result.ok is False
    assert "Contact" in result.error


def test_non_string_location_becomes_ok_false():
    raw = dict(_FULL_RAW)
    raw["Location"] = 12345
    executor = WinrmExecutorStub(responses={("host-v", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-v")
    assert result.ok is False
    assert "Location" in result.error


def test_sparse_real_world_data_is_ok_not_malformed():
    executor = WinrmExecutorStub(
        responses={("host-b", hardware_inventory.CHECK_FUNCTION): json.dumps(_SPARSE_RAW)},
    )
    result = hardware_inventory.run(executor, "host-b")
    assert result.ok is True
    assert result.value["base_board"] == {"manufacturer": None, "product": None, "serial_number": None}
    assert result.value["chassis"]["serial_number"] == ""
    assert result.value["bios"]["serial_number"] is None


def test_multiple_processors_memory_disks_nics():
    raw = dict(_FULL_RAW)
    raw["Processors"] = [
        {"DeviceId": "CPU0", "Name": "Intel Xeon", "Manufacturer": "GenuineIntel"},
        {"DeviceId": "CPU1", "Name": "Intel Xeon", "Manufacturer": "GenuineIntel"},
    ]
    executor = WinrmExecutorStub(responses={("host-c", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-c")
    assert result.ok is True
    assert len(result.value["processors"]) == 2


def test_missing_top_level_key_becomes_ok_false():
    raw = dict(_FULL_RAW)
    del raw["Chassis"]
    executor = WinrmExecutorStub(responses={("host-d", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-d")
    assert result.ok is False
    assert "Chassis" in result.error


def test_bios_not_object_becomes_ok_false():
    raw = dict(_FULL_RAW)
    raw["Bios"] = "not an object"
    executor = WinrmExecutorStub(responses={("host-e", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-e")
    assert result.ok is False
    assert "Bios" in result.error


def test_operating_system_missing_version_becomes_ok_false():
    raw = dict(_FULL_RAW)
    raw["OperatingSystem"] = {
        "Caption": "Microsoft Windows Server 2019 Standard",
        "ReleaseId": "1809",
        "NumberOfLogicalProcessors": 1,
    }
    executor = WinrmExecutorStub(responses={("host-p", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-p")
    assert result.ok is False
    assert "Version" in result.error


def test_operating_system_null_release_id_and_number_of_logical_processors_is_ok():
    # A host where the registry key or Win32_ComputerSystem query didn't
    # resolve -- a legitimate "don't know" state, not malformed output.
    raw = dict(_FULL_RAW)
    raw["OperatingSystem"] = {
        "Caption": "Microsoft Windows Server 2019 Standard",
        "Version": "10.0.17763",
        "ReleaseId": None,
        "NumberOfLogicalProcessors": None,
    }
    executor = WinrmExecutorStub(responses={("host-t", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-t")
    assert result.ok is True
    assert result.value["operating_system"]["release_id"] is None
    assert result.value["operating_system"]["number_of_logical_processors"] is None


def test_operating_system_missing_number_of_logical_processors_key_becomes_ok_false():
    raw = dict(_FULL_RAW)
    raw["OperatingSystem"] = {
        "Caption": "Microsoft Windows Server 2019 Standard",
        "Version": "10.0.17763",
        "ReleaseId": "1809",
    }
    executor = WinrmExecutorStub(responses={("host-u", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-u")
    assert result.ok is False
    assert "NumberOfLogicalProcessors" in result.error


def test_operating_system_non_int_number_of_logical_processors_becomes_ok_false():
    raw = dict(_FULL_RAW)
    raw["OperatingSystem"] = {
        "Caption": "Microsoft Windows Server 2019 Standard",
        "Version": "10.0.17763",
        "ReleaseId": "1809",
        "NumberOfLogicalProcessors": "1",
    }
    executor = WinrmExecutorStub(responses={("host-v", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-v")
    assert result.ok is False
    assert "NumberOfLogicalProcessors" in result.error


def test_processors_bare_object_not_array_becomes_ok_false():
    # Same ConvertTo-Json single-element-collapse gotcha as every prior
    # list check -- Processors included even though usually one entry.
    raw = dict(_FULL_RAW)
    raw["Processors"] = {"DeviceId": "CPU0", "Name": "Intel Xeon", "Manufacturer": "GenuineIntel"}
    executor = WinrmExecutorStub(responses={("host-f", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-f")
    assert result.ok is False
    assert "Processors is not a JSON array" in result.error


def test_processor_missing_device_id_becomes_ok_false():
    raw = dict(_FULL_RAW)
    raw["Processors"] = [{"Name": "Intel Xeon", "Manufacturer": "GenuineIntel"}]
    executor = WinrmExecutorStub(responses={("host-g", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-g")
    assert result.ok is False
    assert "DeviceId" in result.error


def test_memory_missing_device_locator_becomes_ok_false():
    raw = dict(_FULL_RAW)
    raw["Memory"] = [{"CapacityBytes": 100, "Manufacturer": None, "PartNumber": None, "SerialNumber": None}]
    executor = WinrmExecutorStub(responses={("host-h", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-h")
    assert result.ok is False
    assert "DeviceLocator" in result.error


def test_disk_missing_device_id_becomes_ok_false():
    raw = dict(_FULL_RAW)
    raw["Disks"] = [{"Model": "x", "SerialNumber": None, "InterfaceType": None, "FirmwareRevision": None}]
    executor = WinrmExecutorStub(responses={("host-i", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-i")
    assert result.ok is False
    assert "DeviceId" in result.error


def test_network_adapter_missing_mac_becomes_ok_false():
    raw = dict(_FULL_RAW)
    raw["NetworkAdapters"] = [{"Name": "x", "Manufacturer": None}]
    executor = WinrmExecutorStub(responses={("host-j", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-j")
    assert result.ok is False
    assert "MacAddress" in result.error


def test_no_disks_empty_array():
    raw = dict(_FULL_RAW)
    raw["Disks"] = []
    executor = WinrmExecutorStub(responses={("host-k", hardware_inventory.CHECK_FUNCTION): json.dumps(raw)})
    result = hardware_inventory.run(executor, "host-k")
    assert result.ok is True
    assert result.value["disks"] == []


def test_executor_failure_becomes_ok_false():
    executor = WinrmExecutorStub(failures={("host-l", hardware_inventory.CHECK_FUNCTION): "kerberos auth failed"})
    result = hardware_inventory.run(executor, "host-l")
    assert result.ok is False
    assert "kerberos auth failed" in result.error


def test_unparseable_output_becomes_ok_false():
    executor = WinrmExecutorStub(responses={("host-m", hardware_inventory.CHECK_FUNCTION): "not json"})
    result = hardware_inventory.run(executor, "host-m")
    assert result.ok is False
    assert result.error is not None


def test_bare_array_not_object_becomes_ok_false():
    executor = WinrmExecutorStub(responses={("host-n", hardware_inventory.CHECK_FUNCTION): "[]"})
    result = hardware_inventory.run(executor, "host-n")
    assert result.ok is False
    assert result.error is not None


def test_invokes_only_the_whitelisted_function_name():
    executor = WinrmExecutorStub(
        responses={("host-o", hardware_inventory.CHECK_FUNCTION): json.dumps(_FULL_RAW)},
    )
    hardware_inventory.run(executor, "host-o")

    assert executor.calls == [("host-o", "Get-HardwareInventoryStatus")]
