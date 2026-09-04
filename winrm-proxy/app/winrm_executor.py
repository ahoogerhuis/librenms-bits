"""Executor boundary: the only place that talks WinRM/Kerberos/JEA.

Everything above this interface (auth, protocol, whitelist, the check
implementations in checks/) is fully testable without a real Windows
target or a domain-joined proxy host, by swapping in
winrm_executor_stub.WinrmExecutorStub via FastAPI's dependency
injection. winrm_executor_pypsrp.WinrmExecutorPypsrp is the only
implementation that actually requires the AD service-account keytab
and a target host with a registered JEA endpoint.

Deliberately a single constrained primitive: invoke a named,
zero-argument PowerShell function -- nothing else. This mirrors the
Windows-side JEA design directly (see the WinrmProbeJEA module):
each check is a purpose-built function with the actual target
(registry path etc.) hardcoded in its body, and the JEA Role
Capability whitelists the function *name* with VisibleCmdlets = @()
(nothing built-in reachable at all, not even Get-ItemProperty).
Deliberately not a "run this cmdlet with these parameters" primitive
-- that would still require the JEA whitelist to validate parameter
values via ValidatePattern/ValidateSet, which is fiddlier and easier
to accidentally widen than "only these exact function names exist in
this session, and none of them take arguments." There is no
parameter surface here for a caller (or a bug in this codebase) to
manipulate at all.
"""

from __future__ import annotations

from typing import Protocol


class ExecutorError(Exception):
    """Raised when a WinRM/Kerberos/JEA call fails for any reason."""


class WinrmExecutor(Protocol):
    def invoke_function(self, host: str, function_name: str) -> str:
        """Invoke a whitelisted, zero-parameter PowerShell function in
        the host's JEA-constrained PSSession and return its raw string
        output. The function itself decides what it returns -- see
        checks/reboot_pending.py, which expects compact JSON (the
        function calls ConvertTo-Json internally; that's fine even
        under JEA, since VisibleFunctions/VisibleCmdlets only
        constrain what the *caller* can invoke directly, not what a
        whitelisted function does inside its own body).

        Raises ExecutorError on any failure (unreachable host,
        Kerberos auth failure, function not found/not whitelisted,
        etc).
        """
        ...
