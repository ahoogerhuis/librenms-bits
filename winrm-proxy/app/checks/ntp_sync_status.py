r"""ntp_sync_status check.

Invokes the Get-NtpSyncStatus JEA function (see the WinrmProbeJEA
module, WINRM_JEA_WINDOWS.md) -- the first check in this project
invoking an external executable (w32tm.exe) rather than a cmdlet or a
direct registry/WMI read. VisibleExternalCommands is deliberately NOT
used for this -- confirmed by real testing to be unreliable on this
platform (new functions whitelisted alongside a populated
VisibleExternalCommands entry failed to resolve, surviving a WinRM
restart -- see docs/WINRM_NTP_SYNC_STATUS_CHECK.md). Instead, w32tm.exe
is invoked via the & call operator from inside the already-whitelisted
function body -- the same internal-call principle already established
for winupdate-pending's COM interop (JEA gates what the *session* can
invoke directly, not what a whitelisted function's own body calls
internally).

Confirmed JSON shape (PascalCase, matching every other JEA function's
convention):
  {
    "Stratum": <int|null>, "RootDelay": <float|null>,
    "RootDispersion": <float|null>, "ReferenceIp": <str|null>,
    "Offset": <float|null>, "Source": <str|null>,
    "PhaseOffset": <float|null>, "FrequencyPpb": <float|null>,
    "Reachability": <int|null>
  }

Reachability added 2026-08-13, from a real investigation into whether
w32tm /query /peers's extra fields (Reachability, ValidDataCounter)
were worth capturing at all. Confirmed via real live testing (forced
resync, sampled repeatedly) to be the classic NTP 8-bit "reach"
shift-register (0-255, climbs 1 bit per successful poll, 255 = last 8
polls all succeeded) -- not assumed from the field name. Stored as a
plain Component attribute in WinrmPoller.php (like `peerref`), not an
RRD dataset -- a raw 0-255 bitmask isn't a naturally graphable
continuous value the way stratum/offset/frequency_ppb are.
ValidDataCounter, the sibling field, was investigated the same real
way and NOT added -- confirmed empirically redundant with
Reachability's own bit-population count once both reach steady state
(both cap out together at 8/255 in the same real test), no
independent signal. Full writeup: docs/WINRM_NTP_SYNC_STATUS_CHECK.md
(alexh/librenms-fork), "Investigated: Reachability/ValidDataCounter".

PhaseOffset/FrequencyPpb added 2026-08-12
(docs/WINRM_CLOCK_DISCIPLINE_RESEARCH.md, alexh/librenms-fork) --
genuine clock-DISCIPLINE data (how well the correction process itself
is working), not more sync-status detail. FrequencyPpb is
`\Windows Time Service\Clock Frequency Adjustment (PPB)`
(Get-Counter, not text parsing -- a real, separately-tested JEA
feasibility question, confirmed reachable from inside a whitelisted
function), the one source Microsoft's own real event-log text (Event
ID 262) names as the recommended way to track small clock-rate
corrections; the event log itself only logs adjustments >=800 PPM,
useless for routine monitoring. Native PPB, not converted to any
other unit -- kept as Windows actually reports it. PhaseOffset comes
from the same `w32tm /query /status /verbose` call now used for the
base fields (switched from the bare `/query /status` used before this
addition -- same first 9 lines in the same positions, confirmed
directly, so this is additive, not a re-parse of anything) -- a
genuinely different quantity from Offset above, confirmed empirically
by sampling both at the same moment and finding different magnitude
and sign; the system's own internally-tracked phase-correction
residual, not a second copy of the network-measured offset.

Source added 2026-08-11 (docs/WINRM_NTP_SYNC_STATUS_CHECK.md's "Peer
Reference column empty" investigation): w32tm's own "Source:" line --
the human-readable name of who this box is actually synced with (a
hostname, an IP, or a non-NTP-provider name like "Local CMOS Clock")
-- was never parsed at all. Threaded through to WinrmPoller.php as the
Component's `peerref` field, which shipped hardcoded to '' and drives
the (previously always-empty) "Peer Reference" UI column on the NTP
apps page.

Deliberately NOT used to replace ReferenceIp as `peer`, and NOT used
to retarget the /stripchart offset query, despite Source being (per
Microsoft's own docs) a more conventionally-named "this is who I sync
with" field than ReferenceIp is -- on the one real target this was
verified against, ReferenceIp and Source resolved to the same host
(confirmed via DNS), so there was no live evidence either way on
whether they can genuinely diverge (e.g. a deeper NTP hierarchy where
ReferenceId reflects the immediate peer's own upstream reference,
distinct from Source) -- and `peer` already drives the RRD filename
(changing its source value would orphan the already-shipped,
already-tested RRD file the same way past model migrations in this
project have needed explicit cleanup) and the already-tested offset
query. Not worth destabilizing a working, verified path to satisfy an
empty *cosmetic* column -- Source is added purely alongside the
existing fields, not in place of any of them.

All six fields are independently nullable -- a real, common state,
not an error:
  - W32Time not running at all: the JEA function's own "fewer than 9
    lines" guard catches this (w32tm prints a single error line
    instead of the normal field dump) and returns all five as null.
  - Running but not yet synced (e.g. just after service start, before
    the first successful poll): Stratum/RootDelay/RootDispersion come
    back as real zero values (w32tm's own "unspecified" state),
    ReferenceIp and Offset come back null (no reference source to
    report or query) -- confirmed against the real target, not assumed.
  - Offset specifically can be null even when a reference IS
    established: it comes from a *live*, on-demand w32tm /stripchart
    query against that reference (a genuine second network exchange,
    separate from /query /status), which can transiently fail even
    when the device's sync state itself is otherwise healthy -- proxy
    validation treats this the same "legitimate don't know" way as
    reboot-pending's PendingFileRenameOperations, not as a reason to
    fail the whole check.

Locale note (worth being explicit about, not silently assumed):
Stratum/RootDelay/RootDispersion are parsed by fixed LINE POSITION out
of w32tm's text output, not by matching English label text -- w32tm's
field *labels* are known to be localized on non-English Windows
installs, but the field *order* is not expected to change. This has
only been verified against an English-locale target (confirmed via
Get-Culture/Get-UICulture -- no non-English target available to test
against) -- a real, documented residual risk for a fleet running a
different display language, not a guarantee. ReferenceIp is extracted
by IP-address *shape* (digits and dots), which is genuinely
locale-independent regardless of this caveat.
"""

from __future__ import annotations

import json

from app.models import CheckResult
from app.winrm_executor import ExecutorError, WinrmExecutor

CHECK_FUNCTION = "Get-NtpSyncStatus"


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return _is_int(value) or isinstance(value, float)


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

    required = ("Stratum", "RootDelay", "RootDispersion", "ReferenceIp", "Offset", "Source", "PhaseOffset", "FrequencyPpb", "Reachability")
    missing = [k for k in required if k not in data]
    if missing:
        return CheckResult(ok=False, error=f"missing expected key(s) {missing} in check output: {raw!r}")

    stratum = data["Stratum"]
    if stratum is not None and not _is_int(stratum):
        return CheckResult(ok=False, error=f"Stratum is neither null nor an integer: {raw!r}")

    root_delay = data["RootDelay"]
    if root_delay is not None and not _is_number(root_delay):
        return CheckResult(ok=False, error=f"RootDelay is neither null nor a number: {raw!r}")

    root_dispersion = data["RootDispersion"]
    if root_dispersion is not None and not _is_number(root_dispersion):
        return CheckResult(ok=False, error=f"RootDispersion is neither null nor a number: {raw!r}")

    reference_ip = data["ReferenceIp"]
    if reference_ip is not None and not isinstance(reference_ip, str):
        return CheckResult(ok=False, error=f"ReferenceIp is neither null nor a string: {raw!r}")

    offset = data["Offset"]
    if offset is not None and not _is_number(offset):
        return CheckResult(ok=False, error=f"Offset is neither null nor a number: {raw!r}")

    source = data["Source"]
    if source is not None and not isinstance(source, str):
        return CheckResult(ok=False, error=f"Source is neither null nor a string: {raw!r}")

    phase_offset = data["PhaseOffset"]
    if phase_offset is not None and not _is_number(phase_offset):
        return CheckResult(ok=False, error=f"PhaseOffset is neither null nor a number: {raw!r}")

    frequency_ppb = data["FrequencyPpb"]
    if frequency_ppb is not None and not _is_number(frequency_ppb):
        return CheckResult(ok=False, error=f"FrequencyPpb is neither null nor a number: {raw!r}")

    reachability = data["Reachability"]
    if reachability is not None and not _is_int(reachability):
        return CheckResult(ok=False, error=f"Reachability is neither null nor an integer: {raw!r}")

    return CheckResult(
        ok=True,
        value={
            "stratum": stratum,
            "root_delay": root_delay,
            "root_dispersion": root_dispersion,
            "reference_ip": reference_ip,
            "offset": offset,
            "source": source,
            "phase_offset": phase_offset,
            "frequency_ppb": frequency_ppb,
            "reachability": reachability,
        },
    )
