"""service_status_w32time check.

Invokes the Get-W32timeStatus JEA function (see the WinrmProbeJEA
module, WINRM_JEA_WINDOWS.md) -- a zero-parameter function whose body
is hardcoded to (Get-Service -Name W32Time).Status and returns
compact JSON.

Deliberately its own check module, not a parameterized "service
status" check -- see WINRM_JEA_WINDOWS.md's "one function per
service" decision. All service-selection logic lives in which
whitelisted PowerShell function gets invoked, not in any argument
passed to it.
"""

from __future__ import annotations

import json

from app.models import CheckResult
from app.winrm_executor import ExecutorError, WinrmExecutor

CHECK_FUNCTION = "Get-W32timeStatus"


def run(executor: WinrmExecutor, host: str) -> CheckResult:
    try:
        raw = executor.invoke_function(host, CHECK_FUNCTION)
    except ExecutorError as e:
        return CheckResult(ok=False, error=str(e))

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        return CheckResult(ok=False, error=f"unparseable check output: {e}")

    if not isinstance(data, str) or not data:
        return CheckResult(ok=False, error=f"unexpected check output shape: {raw!r}")

    return CheckResult(ok=True, value={"status": data})
