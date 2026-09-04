"""Wire protocol models for the LibreNMS WinRM proxy.

Poller <-> proxy protocol is deliberately narrow: {host, check_name} in,
a structured result out. Never free-form script. Pydantic's default
extra="forbid" (v2) rejects any request carrying fields outside this
shape at the transport layer, before any application code runs.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str
    check_name: str


class CheckResult(BaseModel):
    """Result of running a whitelisted check against a target host.

    ok=False + error set means the check itself failed to run (auth,
    connectivity, JEA rejection, etc). ok=True means it ran; `value`
    carries the check-specific result (e.g. {"reboot_pending": true}).
    """

    ok: bool
    value: dict | None = None
    message: str | None = None
    error: str | None = None
