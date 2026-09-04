r"""memory_usage check.

Invokes the Get-MemoryUsageStatus JEA function (see the WinrmProbeJEA
module, WINRM_JEA_WINDOWS.md) -- a zero-parameter function combining
two separate WMI classes:
  Win32_OperatingSystem: TotalVisibleMemorySize/FreePhysicalMemory
    (physical RAM) and TotalVirtualMemorySize/FreeVirtualMemory
    (virtual memory -- physical RAM + page file combined, a broader
    concept than page-file usage specifically, not "swap"). Both
    field pairs are in **KB**.
  Win32_PageFileUsage: one row per configured page file (Windows does
    not guarantee exactly one), AllocatedBaseSize/CurrentUsage in
    **MB**. This is the actual "swap" data, not derived from the
    Win32_OperatingSystem figures above -- a genuinely separate query.

Both unit conversions (KB->bytes, MB->bytes) happen inside the JEA
function itself, not here or in WinrmPoller.php -- one consistent unit
(bytes, matching disk-space's convention) decided once at the source,
not left for a downstream layer to guess at or get wrong. Confirmed
JSON shape (PascalCase, matching every other JEA function's
convention):
  {
    "PhysicalTotalBytes": <int>, "PhysicalFreeBytes": <int>,
    "VirtualTotalBytes": <int>, "VirtualFreeBytes": <int>,
    "PageFiles": [{"Name": <str>, "AllocatedBytes": <int>, "UsedBytes": <int>}, ...],
    "LastBootUpTime": <str|null>
  }

First check in this project needing both scalar and list validation in
the same function -- combines the missing/wrong-type-key guard pattern
(winupdate_pending.py) for the physical/virtual scalar pairs with the
isinstance(data, list)-style guard (disk_space.py) for PageFiles,
rather than inventing a third validation shape.

LastBootUpTime (2026-08-10 addition): sourced from
Win32_OperatingSystem, the same WMI class already queried for the
physical/virtual figures above -- zero new reachability question, zero
new WinRM round-trip. WinrmPoller.php uses this to compute
Device.uptime, refreshed every poll (this check's own cadence) rather
than hardware-inventory's discover-only cadence -- confirmed against
the real precedent, LibreNMS\Modules\Core::calculateUptime(), which
also runs uptime handling only in poll(), never discover(). Converted
to a UTC ISO-8601 string inside the JEA function itself
(.ToUniversalTime().ToString('o')) -- confirmed by real testing that
[System.Management.ManagementDateTimeConverter]::ToDateTime() returns
Kind=Unspecified, so a naive .ToString('o') omits the UTC offset and
would be silently misinterpreted as UTC by downstream parsing even
though it's really the target's local time. Can legitimately be null
if the registry/WMI read fails on the target -- not fabricated.
"""

from __future__ import annotations

import json

from app.models import CheckResult
from app.winrm_executor import ExecutorError, WinrmExecutor

CHECK_FUNCTION = "Get-MemoryUsageStatus"

_SCALAR_KEYS = (
    "PhysicalTotalBytes",
    "PhysicalFreeBytes",
    "VirtualTotalBytes",
    "VirtualFreeBytes",
)
_PAGE_FILE_KEYS = ("Name", "AllocatedBytes", "UsedBytes")


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


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

    missing = [k for k in _SCALAR_KEYS if k not in data]
    if "PageFiles" not in data:
        missing.append("PageFiles")
    if missing:
        return CheckResult(ok=False, error=f"missing expected key(s) {missing} in check output: {raw!r}")

    for key in _SCALAR_KEYS:
        if not _is_int(data[key]):
            return CheckResult(ok=False, error=f"{key} is not an integer: {raw!r}")

    # Optional: absent entirely (older cached output shape) or
    # present-but-null (the JEA function's own registry/WMI read
    # failed) are both a legitimate "don't know" state, not errors.
    last_boot_up_time = data.get("LastBootUpTime")
    if last_boot_up_time is not None and not isinstance(last_boot_up_time, str):
        return CheckResult(ok=False, error=f"LastBootUpTime is neither null nor a string: {raw!r}")

    page_files_raw = data["PageFiles"]
    # Same ConvertTo-Json single-element-collapse risk disk_space.py
    # guards against -- the JEA function wraps in @(...) to avoid it,
    # but this layer doesn't trust that blindly either.
    if not isinstance(page_files_raw, list):
        return CheckResult(ok=False, error=f"PageFiles is not a JSON array: {raw!r}")

    page_files = []
    for i, entry in enumerate(page_files_raw):
        if not isinstance(entry, dict):
            return CheckResult(ok=False, error=f"page file entry {i} is not an object: {entry!r}")

        entry_missing = [k for k in _PAGE_FILE_KEYS if k not in entry]
        if entry_missing:
            return CheckResult(ok=False, error=f"page file entry {i} missing key(s) {entry_missing}: {entry!r}")

        if not isinstance(entry["Name"], str) or not entry["Name"]:
            return CheckResult(ok=False, error=f"page file entry {i} has non-string/empty Name: {entry!r}")

        if not _is_int(entry["AllocatedBytes"]) or not _is_int(entry["UsedBytes"]):
            return CheckResult(
                ok=False, error=f"page file entry {i} has non-integer AllocatedBytes/UsedBytes: {entry!r}"
            )

        page_files.append(
            {
                "name": entry["Name"],
                "allocated_bytes": entry["AllocatedBytes"],
                "used_bytes": entry["UsedBytes"],
            }
        )

    return CheckResult(
        ok=True,
        value={
            "physical": {
                "total_bytes": data["PhysicalTotalBytes"],
                "free_bytes": data["PhysicalFreeBytes"],
            },
            "virtual": {
                "total_bytes": data["VirtualTotalBytes"],
                "free_bytes": data["VirtualFreeBytes"],
            },
            "page_files": page_files,
            "last_boot_up_time": last_boot_up_time,
        },
    )
