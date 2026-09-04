"""winupdate_pending check.

v2 (2026-08-10). Invokes the Get-PendingUpdateStatus JEA function (see
the WinrmProbeJEA module, WINRM_JEA_WINDOWS.md), which now reads
Windows' own last Event ID 26 ("Windows Update successfully found N
updates") from Microsoft-Windows-WindowsUpdateClient/Operational,
instead of triggering a live Microsoft.Update.Session COM search.
Returns compact JSON:
  {"PendingCount": <int|null>, "LastScanTime": <str|null>}

Replaces v1, which called CreateUpdateSearcher().Search(...) on every
poll -- confirmed via a real controlled test (not theorized) to keep
wuauserv pinned continuously Running on any host where this check is
polled on a normal cadence, since the COM API is backed by wuauserv
regardless of the searcher's Online property. v2 reads from the Event
Log service instead, confirmed to never touch wuauserv at all. Full
incident writeup: docs/WINRM_SERVICE_MONITORING_PATTERNS.md's
"Pattern 2, revisited" section (librenms-fork).

Real, deliberate tradeoff, not glossed over: v1's CriticalCount/
ImportantCount (MsrcSeverity-based severity breakdown) are gone --
Event ID 26's message is a flat count, no per-severity detail exists
in this event log (checked the actual full ID range present, only 26
and 41 ever appear). Not stubbed with a fake 0, which would
misrepresent "not measured" as "confirmed zero" -- dropped entirely.
Nothing downstream used them anyway (WinrmPoller.php only ever read
pending_count). Gained LastScanTime in their place.

Both PendingCount and LastScanTime can legitimately be null -- a
fresh host Windows hasn't scanned yet has no Event ID 26 at all, which
is a real "don't know yet" state, not an error, and the JEA function
returns null for both in that case rather than failing. It's also
possible (regex-parse failure against an unexpected message format)
for LastScanTime to be set while PendingCount is null -- kept as two
independently-nullable fields rather than a single all-or-nothing
flag, since "we know when it last scanned but couldn't parse the
count" is a genuinely different, more actionable state than "no scan
has ever happened".
"""

from __future__ import annotations

import json

from app.models import CheckResult
from app.winrm_executor import ExecutorError, WinrmExecutor

CHECK_FUNCTION = "Get-PendingUpdateStatus"

_REQUIRED_KEYS = ("PendingCount", "LastScanTime")


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

    missing = [k for k in _REQUIRED_KEYS if k not in data]
    if missing:
        return CheckResult(ok=False, error=f"missing expected key(s) {missing} in check output: {raw!r}")

    pending_count = data["PendingCount"]
    if pending_count is not None and (not isinstance(pending_count, int) or isinstance(pending_count, bool)):
        return CheckResult(ok=False, error=f"PendingCount is neither null nor an integer: {raw!r}")

    last_scan_time = data["LastScanTime"]
    if last_scan_time is not None and not isinstance(last_scan_time, str):
        return CheckResult(ok=False, error=f"LastScanTime is neither null nor a string: {raw!r}")

    return CheckResult(
        ok=True,
        value={
            "pending_count": pending_count,
            "last_scan_time": last_scan_time,
        },
    )
