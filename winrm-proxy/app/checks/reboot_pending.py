"""reboot_pending check.

Invokes the Get-RebootPendingStatus JEA function (see the
WinrmProbeJEA module, WINRM_JEA_WINDOWS.md) -- a zero-parameter
function whose body checks the two registry locations from the design
doc and returns compact JSON:
  - HKLM\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired
  - HKLM\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\PendingFileRenameOperations

All of that logic lives in the whitelisted PowerShell function, not
here -- this module just invokes it by name (the only thing the JEA
role capability allows) and interprets the JSON it returns.
"""

from __future__ import annotations

import json

from app.models import CheckResult
from app.winrm_executor import ExecutorError, WinrmExecutor

CHECK_FUNCTION = "Get-RebootPendingStatus"


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

    reboot_pending = bool(data.get("RebootRequired")) or bool(data.get("PendingFileRenameOperations"))

    return CheckResult(ok=True, value={"reboot_pending": reboot_pending})
