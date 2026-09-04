"""network_traffic check.

Invokes the Get-NetworkInterfaceStats JEA function (see the
WinrmProbeJEA module, WINRM_JEA_WINDOWS.md) -- a zero-parameter
function that queries Win32_PerfRawData_Tcpip_NetworkInterface and
returns a JSON array, one entry per interface:
  [{"InterfaceName": "...", "BytesSent": <int>, "BytesReceived": <int>,
    "NetEnabled": <bool|null>, "NetConnectionStatus": <int|null>}, ...]

Despite the "PerSec" suffix on the underlying WMI properties
(BytesSentPersec/BytesReceivedPersec), these are raw PerfLib counter
values -- cumulative, monotonically increasing (barring rollover), the
same shape as SNMP's ifInOctets/ifOutOctets. This check does NOT
compute a rate; it reports the raw counters and lets
WinrmPoller.php/RRD's own DERIVE dataset type compute throughput
between samples, matching exactly how disk_space.py reports raw
size/free bytes rather than a computed percentage.

Like disk_space, the result is a *list*, not a single scalar/dict --
one host can have any number of network interfaces. Wrapped in
{"interfaces": [...]} to keep CheckResult.value a dict.

NetEnabled/NetConnectionStatus (2026-08-10 fix): sourced from
Win32_NetworkAdapter, a genuinely different WMI class than the
perf-counter one above -- correlated by exact Name-string match inside
the JEA function itself, not here. Both null are the check function's
own explicit result whenever no adapter correlates for a given
interface (this check never fabricates NetEnabled/NetConnectionStatus
when correlation fails) -- passed through as-is, not defaulted to a
guessed value. Raw values only: the up/down semantic mapping happens
in WinrmPoller.php (same "proxy validates and passes through, the PHP
module owns domain-specific mapping" split as service-status's raw
string -> LibreNMS state-index mapping), not here.
"""

from __future__ import annotations

import json

from app.models import CheckResult
from app.winrm_executor import ExecutorError, WinrmExecutor

CHECK_FUNCTION = "Get-NetworkInterfaceStats"

_INTERFACE_KEYS = ("InterfaceName", "BytesSent", "BytesReceived")


def run(executor: WinrmExecutor, host: str) -> CheckResult:
    try:
        raw = executor.invoke_function(host, CHECK_FUNCTION)
    except ExecutorError as e:
        return CheckResult(ok=False, error=str(e))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return CheckResult(ok=False, error=f"unparseable check output: {e}")

    if not isinstance(data, list):
        return CheckResult(ok=False, error=f"unexpected check output shape (expected a JSON array): {raw!r}")

    interfaces = []
    for i, entry in enumerate(data):
        if not isinstance(entry, dict):
            return CheckResult(ok=False, error=f"interface entry {i} is not an object: {entry!r}")

        missing = [k for k in _INTERFACE_KEYS if k not in entry]
        if missing:
            return CheckResult(ok=False, error=f"interface entry {i} missing key(s) {missing}: {entry!r}")

        if not isinstance(entry["InterfaceName"], str) or not entry["InterfaceName"]:
            return CheckResult(ok=False, error=f"interface entry {i} has non-string/empty InterfaceName: {entry!r}")

        sent_ok = isinstance(entry["BytesSent"], int) and not isinstance(entry["BytesSent"], bool)
        recv_ok = isinstance(entry["BytesReceived"], int) and not isinstance(entry["BytesReceived"], bool)
        if not sent_ok or not recv_ok:
            return CheckResult(ok=False, error=f"interface entry {i} has non-integer BytesSent/BytesReceived: {entry!r}")

        # Both optional -- absent entirely (older cached output shape)
        # or present-but-null (no Win32_NetworkAdapter correlated for
        # this interface) are both legitimate "we don't know" states,
        # not errors.
        net_enabled = entry.get("NetEnabled")
        if net_enabled is not None and not isinstance(net_enabled, bool):
            return CheckResult(ok=False, error=f"interface entry {i} has a non-bool/non-null NetEnabled: {entry!r}")

        net_connection_status = entry.get("NetConnectionStatus")
        conn_status_ok = net_connection_status is None or (
            isinstance(net_connection_status, int) and not isinstance(net_connection_status, bool)
        )
        if not conn_status_ok:
            return CheckResult(
                ok=False, error=f"interface entry {i} has a non-int/non-null NetConnectionStatus: {entry!r}"
            )

        interfaces.append(
            {
                "interface_name": entry["InterfaceName"],
                "bytes_sent": entry["BytesSent"],
                "bytes_received": entry["BytesReceived"],
                "net_enabled": net_enabled,
                "net_connection_status": net_connection_status,
            }
        )

    return CheckResult(ok=True, value={"interfaces": interfaces})
