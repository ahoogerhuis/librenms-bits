"""cpu_usage check.

Invokes the Get-CpuUsageStatus JEA function (see the WinrmProbeJEA
module, WINRM_JEA_WINDOWS.md) -- per-core busy percentage, no
user/kernel split needed.

Real complexity here, worth recording: Win32_Processor.LoadPercentage
(one aggregate number per socket) can't be decomposed into per-core
data after the fact, so per-core data has to come from a different
source: Win32_PerfRawData_PerfOS_Processor (one instance per logical
core, plus a "_Total" aggregate). That WMI class is a
PERF_100NSEC_TIMER counter -- a raw cumulative time-based counter, NOT
a ready-to-use percentage. Computing a correct value from it requires
two samples and the elapsed time between them; there's no way to get a
correct single-poll percentage from one raw read.

Three real options existed for where that two-sample delta math
happens:
  A. Sample twice inside the JEA function itself, return computed
     percentages. Self-contained, ~1s added latency.
  B. Return raw counters, do delta math in WinrmPoller.php between
     polls. Avoids latency, but introduces cross-poll state (a new
     kind of statefulness no other check needs) and real risk of
     getting the PERF_100NSEC_TIMER formula subtly wrong.
  C. Get-Counter's cooked \\Processor(*)\\% Processor Time, which
     computes the percentage internally.

Chose C after real testing (not assumed) -- confirmed reachable inside
JEA (Get-Counter lives in Microsoft.PowerShell.Diagnostics, the same
always-loaded-adjacent module Get-WinEvent turned out to live in for
winupdate-pending v2), and specifically confirmed NOT to return a
since-boot cumulative average with -SampleInterval 1 -MaxSamples 1 --
two real samples 3 seconds apart returned genuinely different per-core
values, ruling out the exact trap that bit the zpool iostat check
(missing -y 2 1, returning since-boot averages). C avoids both A's
"reimplementing PERF_100NSEC_TIMER by hand" risk and B's cross-poll
state entirely, at the same ~1s latency cost as A -- strictly better
once confirmed to work, not a coin flip.

Returns compact JSON, one entry per logical core ("_total" filtered
out in the JEA function itself, not here):
  [{"CoreIndex": "0", "BusyPercent": 3.25}, ...]
"""

from __future__ import annotations

import json

from app.models import CheckResult
from app.winrm_executor import ExecutorError, WinrmExecutor

CHECK_FUNCTION = "Get-CpuUsageStatus"

_CORE_KEYS = ("CoreIndex", "BusyPercent")


def run(executor: WinrmExecutor, host: str) -> CheckResult:
    try:
        raw = executor.invoke_function(host, CHECK_FUNCTION)
    except ExecutorError as e:
        return CheckResult(ok=False, error=str(e))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return CheckResult(ok=False, error=f"unparseable check output: {e}")

    # Same ConvertTo-Json single-element-collapse risk disk_space.py
    # guards against -- the JEA function wraps in @(...) specifically to
    # avoid it, but this layer doesn't trust that blindly either.
    if not isinstance(data, list):
        return CheckResult(ok=False, error=f"unexpected check output shape (expected a JSON array): {raw!r}")

    cores = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            return CheckResult(ok=False, error=f"core entry {i} is not an object: {entry!r}")

        missing = [k for k in _CORE_KEYS if k not in entry]
        if missing:
            return CheckResult(ok=False, error=f"core entry {i} missing key(s) {missing}: {entry!r}")

        if not isinstance(entry["CoreIndex"], str) or not entry["CoreIndex"]:
            return CheckResult(ok=False, error=f"core entry {i} has non-string/empty CoreIndex: {entry!r}")

        busy_percent = entry["BusyPercent"]
        is_number = (isinstance(busy_percent, (int, float)) and not isinstance(busy_percent, bool))
        if not is_number:
            return CheckResult(ok=False, error=f"core entry {i} has non-numeric BusyPercent: {entry!r}")

        cores.append({"core_index": entry["CoreIndex"], "busy_percent": busy_percent})

    return CheckResult(ok=True, value={"cores": cores})
