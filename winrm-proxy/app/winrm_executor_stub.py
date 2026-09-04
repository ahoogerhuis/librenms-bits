"""Fake WinrmExecutor for testing without a real Windows target.

Used for all testing in Phase A (no JEA target existed yet). Swapped
in via FastAPI's dependency_overrides in tests, and can also be run
for real (see main.py's --executor stub flag) to validate the full
auth/protocol/whitelist stack end-to-end against a real network hop,
with canned responses standing in for pypsrp.
"""

from __future__ import annotations

from app.winrm_executor import ExecutorError


class WinrmExecutorStub:
    def __init__(
        self,
        responses: dict[tuple[str, str], str] | None = None,
        failures: dict[tuple[str, str], str] | None = None,
        default_response: str = '{"RebootRequired": false, "PendingFileRenameOperations": false}',
    ) -> None:
        """responses/failures are keyed by (host, function_name)."""
        self.responses = responses or {}
        self.failures = failures or {}
        self.default_response = default_response
        self.calls: list[tuple[str, str]] = []

    def invoke_function(self, host: str, function_name: str) -> str:
        key = (host, function_name)
        self.calls.append(key)

        if key in self.failures:
            raise ExecutorError(self.failures[key])

        return self.responses.get(key, self.default_response)
