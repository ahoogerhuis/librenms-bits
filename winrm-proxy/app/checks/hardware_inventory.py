r"""hardware_inventory check.

Invokes the Get-HardwareInventoryStatus JEA function (see the
WinrmProbeJEA module, WINRM_JEA_WINDOWS.md) -- the first check in this
project aggregating multiple WMI classes in one call, not one:
Win32_BIOS, Win32_BaseBoard, Win32_SystemEnclosure (each a singleton),
plus Win32_Processor, Win32_PhysicalMemory, Win32_DiskDrive, and
Win32_NetworkAdapter (each a list -- Win32_Processor included, despite
usually being one entry per host, since multi-socket systems exist and
treating it as scalar now would need a breaking change later).
Win32_DiskDrive is physical disks, distinct from disk-space's
Win32_LogicalDisk (logical volumes) -- a two-disk host can have only
one lettered volume, that's not a bug. Win32_NetworkAdapter is
filtered to PhysicalAdapter=True in the JEA function itself (excludes
loopback/tunnel/virtual adapters), same "filter at the source" pattern
disk-space's DriveType=3 uses.

Real-target testing (2026-08-10) found genuine WMI data-quality
variance worth expecting, not treating as a bug: on the QEMU/Proxmox
test VM, BaseBoard's fields were all null, Chassis SerialNumber/
AssetTag were empty strings (not null), and Bios.SerialNumber was
null. Real physical hardware from vendors that populate SMBIOS more
completely will likely return richer data -- this check tolerates
sparse/empty values throughout rather than requiring them, since
"WMI didn't have this" is a legitimate real state, not malformed
output.

Wire shape (PascalCase from PowerShell, matching every other check's
convention):
  {
    "Bios": {"SerialNumber": str|null, "Version": str|null, "Manufacturer": str|null},
    "BaseBoard": {"Manufacturer": ..., "Product": ..., "SerialNumber": ...},
    "Chassis": {"Manufacturer": ..., "SerialNumber": ..., "AssetTag": ...},
    "OperatingSystem": {"Caption": str|null, "Version": str|null},
    "Processors": [{"DeviceId": str, "Name": str|null, "Manufacturer": str|null}, ...],
    "Memory": [{"DeviceLocator": str, "CapacityBytes": int|null, "Manufacturer": ..., "PartNumber": ..., "SerialNumber": ...}, ...],
    "Disks": [{"DeviceId": str, "Model": ..., "SerialNumber": ..., "InterfaceType": ..., "FirmwareRevision": ...}, ...],
    "NetworkAdapters": [{"MacAddress": str, "Name": ..., "Manufacturer": ...}, ...]
  }

OperatingSystem (2026-08-10, revised same day): Win32_OperatingSystem's
Caption/Version, sourced from the exact same WMI class memory-usage
already queries successfully -- no new reachability question. Added
here rather than memory-usage or a new check because OS version/build
is static-ish identity data (matches hardware-inventory's existing
discover-only refresh cadence, not memory-usage's 5-minute poll path).

The first version of this section only had Caption/Version and got the
Device.version/Device.features mapping backwards -- fixed after finding
the real precedent, LibreNMS's real SNMP-based "Windows" OS driver
class's discoverOS() method (not just the generic "Os" module), which
this project had missed on the first pass despite the project's own
"match the SNMP shape" convention. That driver parses Windows' SNMP
sysDescr into: Device.version = "<edition> (<release>)" (e.g. "Server
2019 Datacenter (1809)"), Device.features = "Multiprocessor" or
"Uniprocessor" (an SMP-kernel-build descriptor, NOT the OS edition --
completely different from what every other OS type uses this field
for). Two more fields added to match that shape:
  - ReleaseId: the human release-codename ("1809", "22H2", etc.) --
    NOT available from Win32_OperatingSystem at all. Sourced from the
    registry (HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion,
    DisplayVersion preferred, falling back to the older ReleaseId
    value name -- DisplayVersion superseded ReleaseId starting around
    Windows 10 2004/20H2). Same registry-read mechanism reboot-pending
    already uses, no new reachability question.
  - NumberOfLogicalProcessors: from Win32_ComputerSystem (a new WMI
    class for this check). Originally used NumberOfProcessors
    (physical sockets) instead, reasoning the classic
    Multiprocessor/Uniprocessor label was about physical CPU count --
    wrong, per direct correction against this project's own real SNMP-
    monitored Windows devices, which show "Multiprocessor" for
    single-socket multi-core hosts. Windows' separate
    uniprocessor/multiprocessor kernel builds (and the HAL types that
    produced this sysDescr wording originally) were unified starting
    with Vista; in the modern single-kernel era the label reflects
    total logical processors the OS is scheduling across, not physical
    socket count -- confirmed by the user's own observation of real
    devices, not just reasoned from the field name.

Deliberately NOT copying LibreNMS\OS\Windows's build-number-keyed
lookup tables (getServerVersion()/getDatacenterVersion()/etc.) to
derive the edition name -- WMI's Caption already gives the exact
edition string directly and authoritatively, without needing a
hardcoded table that requires a code change for every future Windows
release. Matches the real convention's OUTPUT SHAPE (edition + release
codename in version; processor-mode in features) using a more robust
mechanism than SNMP's sysDescr-regex-plus-lookup-table approach needs
to.

WinrmPoller.php composes Device.version/Device.features from this raw
data; this check stays at raw-value pass-through (matching the project's
existing "proxy validates and passes through, PHP module owns
domain-specific mapping" split).

Identity fields (DeviceId/DeviceLocator/MacAddress) are validated
strictly (non-empty string) -- WinrmPoller.php needs these as stable
per-item keys for its own synthetic entPhysicalIndex, unlike every
other field here which is purely descriptive and safe to leave null.
Everything else is validated loosely: right type if present, or null.

No manufacture/release dates collected (Win32_BIOS.ReleaseDate) --
WMI datetime strings need explicit conversion
(ManagementDateTimeConverter) to be usable, and this check's v1 scope
decided dates aren't worth that parsing complexity. Simplest to not
request the field at all rather than carry an unused capability.

ProcessorIdentifier (2026-08-10 addition): a root-level optional
string, the registry value at
HKLM:\HARDWARE\DESCRIPTION\System\CentralProcessor\0\Identifier
(e.g. "Intel64 Family 15 Model 107 Stepping 1") -- confirmed by real
testing to be the exact source format LibreNMS's real SNMP-based
Windows OS driver (LibreNMS\\OS\\Windows::parseHardware()) parses out
of sysDescr for Device.hardware (CPU architecture, e.g. "Intel x64" --
found to be this field's real meaning while fixing the OS-version
check, not the machine-model guess this check originally assumed).
WinrmPoller.php ports that same regex+lookup-table logic directly
rather than reconstructing the token from separate WMI fields. Same
registry-read mechanism reboot-pending/the OS-version ReleaseId field
already use.

Contact/Location (2026-08-10 addition): two more root-level optional
strings, read from a generic HKLM:\SOFTWARE\LibreNMS\ registry key
(Contact/Location REG_SZ values) rather than anything reconstructed
from WMI -- this data has no WMI source at all, it's purely
admin-populated, matching how SNMP's sysContact/sysLocation are also
just whatever an admin configured on the agent, not derived data.
Deliberately a generic vendor/product key, not org-specific -- this
JEA function ships in the public-eligible librenms-fork module, and
every other org deploying this needs their own path to configure, not
one baked with this project's own naming. Same "absent or null is a
legitimate default state, not an error" handling as ProcessorIdentifier
-- most hosts will never have these values set at all; that's the
expected common case, not a degraded one. WinrmPoller.php is
responsible for only writing these into Device.sysContact/location
when the device's own override flags are off, matching the real
LibreNMS\Modules\Os::sysContact()/updateLocation() precedent (which
this check has no visibility into -- proxy-side stays pure data
pass-through, same split as everything else here).
"""

from __future__ import annotations

import json

from app.models import CheckResult
from app.winrm_executor import ExecutorError, WinrmExecutor

CHECK_FUNCTION = "Get-HardwareInventoryStatus"

_SINGLETON_SECTIONS = ("Bios", "BaseBoard", "Chassis", "OperatingSystem")
_LIST_SECTIONS = ("Processors", "Memory", "Disks", "NetworkAdapters")


def _opt_str(value: object) -> str | bool:
    """True if value is a legitimate optional-string field: a string, or null."""
    return value is None or isinstance(value, str)


def _opt_int(value: object) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool))


def _validate_singleton(section_name: str, data: dict, fields: tuple[str, ...]) -> str | None:
    section = data.get(section_name)
    if not isinstance(section, dict):
        return f"{section_name} is not an object: {section!r}"

    for field in fields:
        if field not in section:
            return f"{section_name} missing key {field!r}: {section!r}"
        if not _opt_str(section[field]):
            return f"{section_name}.{field} is neither null nor a string: {section!r}"

    return None


def run(executor: WinrmExecutor, host: str) -> CheckResult:
    try:
        raw = executor.invoke_function(host, CHECK_FUNCTION)
    except ExecutorError as e:
        return CheckResult(ok=False, error=str(e))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return CheckResult(ok=False, error=f"unparseable check output: {e}")

    if not isinstance(data, dict):
        return CheckResult(ok=False, error=f"unexpected check output shape: {raw!r}")

    missing = [k for k in (*_SINGLETON_SECTIONS, *_LIST_SECTIONS) if k not in data]
    if missing:
        return CheckResult(ok=False, error=f"missing expected key(s) {missing} in check output: {raw!r}")

    err = _validate_singleton("Bios", data, ("SerialNumber", "Version", "Manufacturer"))
    if err is None:
        err = _validate_singleton("BaseBoard", data, ("Manufacturer", "Product", "SerialNumber"))
    if err is None:
        err = _validate_singleton("Chassis", data, ("Manufacturer", "SerialNumber", "AssetTag"))
    if err is None:
        err = _validate_singleton("OperatingSystem", data, ("Caption", "Version", "ReleaseId"))
    if err is not None:
        return CheckResult(ok=False, error=err)

    # Root-level, genuinely optional (absent entirely or present-but-
    # null are both a legitimate "registry read failed on the target"
    # state, not an error) -- not part of _SINGLETON_SECTIONS' required-
    # key check above.
    processor_identifier = data.get("ProcessorIdentifier")
    if processor_identifier is not None and not isinstance(processor_identifier, str):
        return CheckResult(ok=False, error=f"ProcessorIdentifier is neither null nor a string: {raw!r}")

    contact = data.get("Contact")
    if contact is not None and not isinstance(contact, str):
        return CheckResult(ok=False, error=f"Contact is neither null nor a string: {raw!r}")

    location = data.get("Location")
    if location is not None and not isinstance(location, str):
        return CheckResult(ok=False, error=f"Location is neither null nor a string: {raw!r}")

    # NumberOfLogicalProcessors is an int, not a string --
    # _validate_singleton only handles the uniform-string-or-null case
    # every other singleton field fits, so this one gets its own check.
    if "NumberOfLogicalProcessors" not in data["OperatingSystem"]:
        return CheckResult(ok=False, error=f"OperatingSystem missing key 'NumberOfLogicalProcessors': {raw!r}")
    if not _opt_int(data["OperatingSystem"]["NumberOfLogicalProcessors"]):
        return CheckResult(
            ok=False, error=f"OperatingSystem.NumberOfLogicalProcessors is neither null nor an integer: {raw!r}"
        )

    for section_name in _LIST_SECTIONS:
        # Same ConvertTo-Json single-element-collapse risk disk_space.py
        # guards against -- the JEA function wraps every list section in
        # @(...) specifically to avoid it, including Processors (usually
        # one entry, but multi-socket hosts exist), not trusted blindly
        # here either.
        if not isinstance(data[section_name], list):
            return CheckResult(ok=False, error=f"{section_name} is not a JSON array: {raw!r}")

    processors = []
    for i, entry in enumerate(data["Processors"]):
        if not isinstance(entry, dict):
            return CheckResult(ok=False, error=f"Processors entry {i} is not an object: {entry!r}")
        if not isinstance(entry.get("DeviceId"), str) or not entry["DeviceId"]:
            return CheckResult(ok=False, error=f"Processors entry {i} has non-string/empty DeviceId: {entry!r}")
        if not _opt_str(entry.get("Name")) or not _opt_str(entry.get("Manufacturer")):
            return CheckResult(ok=False, error=f"Processors entry {i} has a non-string/non-null field: {entry!r}")
        processors.append(
            {"device_id": entry["DeviceId"], "name": entry.get("Name"), "manufacturer": entry.get("Manufacturer")}
        )

    memory = []
    for i, entry in enumerate(data["Memory"]):
        if not isinstance(entry, dict):
            return CheckResult(ok=False, error=f"Memory entry {i} is not an object: {entry!r}")
        if not isinstance(entry.get("DeviceLocator"), str) or not entry["DeviceLocator"]:
            return CheckResult(ok=False, error=f"Memory entry {i} has non-string/empty DeviceLocator: {entry!r}")
        if (
            not _opt_int(entry.get("CapacityBytes"))
            or not _opt_str(entry.get("Manufacturer"))
            or not _opt_str(entry.get("PartNumber"))
            or not _opt_str(entry.get("SerialNumber"))
        ):
            return CheckResult(ok=False, error=f"Memory entry {i} has a malformed field: {entry!r}")
        memory.append(
            {
                "device_locator": entry["DeviceLocator"],
                "capacity_bytes": entry.get("CapacityBytes"),
                "manufacturer": entry.get("Manufacturer"),
                "part_number": entry.get("PartNumber"),
                "serial_number": entry.get("SerialNumber"),
            }
        )

    disks = []
    for i, entry in enumerate(data["Disks"]):
        if not isinstance(entry, dict):
            return CheckResult(ok=False, error=f"Disks entry {i} is not an object: {entry!r}")
        if not isinstance(entry.get("DeviceId"), str) or not entry["DeviceId"]:
            return CheckResult(ok=False, error=f"Disks entry {i} has non-string/empty DeviceId: {entry!r}")
        if (
            not _opt_str(entry.get("Model"))
            or not _opt_str(entry.get("SerialNumber"))
            or not _opt_str(entry.get("InterfaceType"))
            or not _opt_str(entry.get("FirmwareRevision"))
        ):
            return CheckResult(ok=False, error=f"Disks entry {i} has a malformed field: {entry!r}")
        disks.append(
            {
                "device_id": entry["DeviceId"],
                "model": entry.get("Model"),
                "serial_number": entry.get("SerialNumber"),
                "interface_type": entry.get("InterfaceType"),
                "firmware_revision": entry.get("FirmwareRevision"),
            }
        )

    network_adapters = []
    for i, entry in enumerate(data["NetworkAdapters"]):
        if not isinstance(entry, dict):
            return CheckResult(ok=False, error=f"NetworkAdapters entry {i} is not an object: {entry!r}")
        if not isinstance(entry.get("MacAddress"), str) or not entry["MacAddress"]:
            return CheckResult(ok=False, error=f"NetworkAdapters entry {i} has non-string/empty MacAddress: {entry!r}")
        if not _opt_str(entry.get("Name")) or not _opt_str(entry.get("Manufacturer")):
            return CheckResult(ok=False, error=f"NetworkAdapters entry {i} has a malformed field: {entry!r}")
        network_adapters.append(
            {
                "mac_address": entry["MacAddress"],
                "name": entry.get("Name"),
                "manufacturer": entry.get("Manufacturer"),
            }
        )

    return CheckResult(
        ok=True,
        value={
            "bios": {
                "serial_number": data["Bios"]["SerialNumber"],
                "version": data["Bios"]["Version"],
                "manufacturer": data["Bios"]["Manufacturer"],
            },
            "base_board": {
                "manufacturer": data["BaseBoard"]["Manufacturer"],
                "product": data["BaseBoard"]["Product"],
                "serial_number": data["BaseBoard"]["SerialNumber"],
            },
            "chassis": {
                "manufacturer": data["Chassis"]["Manufacturer"],
                "serial_number": data["Chassis"]["SerialNumber"],
                "asset_tag": data["Chassis"]["AssetTag"],
            },
            "operating_system": {
                "caption": data["OperatingSystem"]["Caption"],
                "version": data["OperatingSystem"]["Version"],
                "release_id": data["OperatingSystem"]["ReleaseId"],
                "number_of_logical_processors": data["OperatingSystem"]["NumberOfLogicalProcessors"],
            },
            "processors": processors,
            "memory": memory,
            "disks": disks,
            "network_adapters": network_adapters,
            "processor_identifier": processor_identifier,
            "contact": contact,
            "location": location,
        },
    )
