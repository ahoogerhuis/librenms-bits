"""disk_space check.

Invokes the Get-LocalDiskSpace JEA function (see the WinrmProbeJEA
module, WINRM_JEA_WINDOWS.md) -- a zero-parameter function that queries
Win32_LogicalDisk filtered to DriveType=3 (local fixed disks only,
excludes removable/network/CD-ROM/RAM disks) and returns a JSON array,
one entry per disk:
  [{"DriveLetter": "C:", "SizeBytes": <int>, "FreeBytes": <int>}, ...]

Unlike every other check so far, the result is a *list* of readings,
not a single scalar/dict -- one physical device can have any number of
fixed disks. Wrapped in {"disks": [...]} to keep CheckResult.value a
dict, matching the wire shape every other check uses.

Path-mounted volumes (NTFS mount points with no drive letter) and
Cluster Shared Volumes are explicitly out of scope -- Win32_LogicalDisk
doesn't enumerate either. See docs/WINRM_DISK_SPACE_CHECK.md for why
that's a deliberate v2 gap, not an oversight.
"""

from __future__ import annotations

import json

from app.models import CheckResult
from app.winrm_executor import ExecutorError, WinrmExecutor

CHECK_FUNCTION = "Get-LocalDiskSpace"

_DISK_KEYS = ("DriveLetter", "SizeBytes", "FreeBytes")


def run(executor: WinrmExecutor, host: str) -> CheckResult:
    try:
        raw = executor.invoke_function(host, CHECK_FUNCTION)
    except ExecutorError as e:
        return CheckResult(ok=False, error=str(e))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return CheckResult(ok=False, error=f"unparseable check output: {e}")

    # ConvertTo-Json without -AsArray collapses a single-element pipeline
    # to a bare object, not a one-element array -- the JEA function uses
    # -AsArray specifically to avoid this, but don't trust that blindly
    # here: a bare dict would otherwise silently iterate over its own
    # keys below instead of failing loud.
    if not isinstance(data, list):
        return CheckResult(ok=False, error=f"unexpected check output shape (expected a JSON array): {raw!r}")

    disks = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            return CheckResult(ok=False, error=f"disk entry {i} is not an object: {entry!r}")

        missing = [k for k in _DISK_KEYS if k not in entry]
        if missing:
            return CheckResult(ok=False, error=f"disk entry {i} missing key(s) {missing}: {entry!r}")

        if not isinstance(entry["DriveLetter"], str) or not entry["DriveLetter"]:
            return CheckResult(ok=False, error=f"disk entry {i} has non-string/empty DriveLetter: {entry!r}")

        size_ok = isinstance(entry["SizeBytes"], int) and not isinstance(entry["SizeBytes"], bool)
        free_ok = isinstance(entry["FreeBytes"], int) and not isinstance(entry["FreeBytes"], bool)
        if not size_ok or not free_ok:
            return CheckResult(ok=False, error=f"disk entry {i} has non-integer SizeBytes/FreeBytes: {entry!r}")

        disks.append(
            {
                "drive_letter": entry["DriveLetter"],
                "size_bytes": entry["SizeBytes"],
                "free_bytes": entry["FreeBytes"],
            }
        )

    return CheckResult(ok=True, value={"disks": disks})
