import json

from app.checks import ntp_sync_status
from app.winrm_executor_stub import WinrmExecutorStub

_SYNCED_RAW = {
    "Stratum": 4,
    "RootDelay": 0.0231335,
    "RootDispersion": 1.7840817,
    "ReferenceIp": "192.0.2.10",
    "Offset": 0.0003277,
    "Source": "dc01.vpp.local",
    "PhaseOffset": 0.000008,
    "FrequencyPpb": 2514.0,
    "Reachability": 255,
}

# Real state confirmed against the target: service running, hasn't
# completed its first sync cycle yet -- Stratum/RootDelay/
# RootDispersion are real zero values (w32tm's own "unspecified"
# state), ReferenceIp/Offset/Source/PhaseOffset/FrequencyPpb are null
# (no reference source yet).
_NOT_YET_SYNCED_RAW = {
    "Stratum": 0,
    "RootDelay": 0,
    "RootDispersion": 0,
    "ReferenceIp": None,
    "Offset": None,
    "Source": None,
    "PhaseOffset": None,
    "FrequencyPpb": None,
    "Reachability": None,
}

# Real state confirmed against the target: W32Time service stopped
# entirely -- the JEA function's own "fewer than 9 lines" guard
# catches this and returns all nine fields null.
_SERVICE_STOPPED_RAW = {
    "Stratum": None,
    "RootDelay": None,
    "RootDispersion": None,
    "ReferenceIp": None,
    "Offset": None,
    "Source": None,
    "PhaseOffset": None,
    "FrequencyPpb": None,
    "Reachability": None,
}


def test_synced_state():
    executor = WinrmExecutorStub(
        responses={("host-a", ntp_sync_status.CHECK_FUNCTION): json.dumps(_SYNCED_RAW)},
    )
    result = ntp_sync_status.run(executor, "host-a")
    assert result.ok is True
    assert result.value == {
        "stratum": 4,
        "root_delay": 0.0231335,
        "root_dispersion": 1.7840817,
        "reference_ip": "192.0.2.10",
        "offset": 0.0003277,
        "source": "dc01.vpp.local",
        "phase_offset": 0.000008,
        "frequency_ppb": 2514.0,
        "reachability": 255,
    }


def test_not_yet_synced_state_is_ok_not_an_error():
    executor = WinrmExecutorStub(
        responses={("host-b", ntp_sync_status.CHECK_FUNCTION): json.dumps(_NOT_YET_SYNCED_RAW)},
    )
    result = ntp_sync_status.run(executor, "host-b")
    assert result.ok is True
    assert result.value["stratum"] == 0
    assert result.value["reference_ip"] is None
    assert result.value["offset"] is None


def test_service_stopped_state_is_ok_not_an_error():
    executor = WinrmExecutorStub(
        responses={("host-c", ntp_sync_status.CHECK_FUNCTION): json.dumps(_SERVICE_STOPPED_RAW)},
    )
    result = ntp_sync_status.run(executor, "host-c")
    assert result.ok is True
    assert result.value == {
        "stratum": None,
        "root_delay": None,
        "root_dispersion": None,
        "reference_ip": None,
        "offset": None,
        "source": None,
        "phase_offset": None,
        "frequency_ppb": None,
        "reachability": None,
    }


def test_offset_null_while_reference_established_is_ok():
    # A real, distinct state confirmed possible: reference established
    # (Stratum/ReferenceIp populated) but the live /stripchart query
    # for Offset transiently failed -- a separate network exchange
    # from /query /status, can fail independently.
    raw = dict(_SYNCED_RAW)
    raw["Offset"] = None
    executor = WinrmExecutorStub(responses={("host-d", ntp_sync_status.CHECK_FUNCTION): json.dumps(raw)})
    result = ntp_sync_status.run(executor, "host-d")
    assert result.ok is True
    assert result.value["reference_ip"] == "192.0.2.10"
    assert result.value["offset"] is None


def test_missing_key_becomes_ok_false():
    raw = dict(_SYNCED_RAW)
    del raw["RootDispersion"]
    executor = WinrmExecutorStub(responses={("host-e", ntp_sync_status.CHECK_FUNCTION): json.dumps(raw)})
    result = ntp_sync_status.run(executor, "host-e")
    assert result.ok is False
    assert "RootDispersion" in result.error


def test_non_integer_stratum_becomes_ok_false():
    raw = dict(_SYNCED_RAW)
    raw["Stratum"] = "4"
    executor = WinrmExecutorStub(responses={("host-f", ntp_sync_status.CHECK_FUNCTION): json.dumps(raw)})
    result = ntp_sync_status.run(executor, "host-f")
    assert result.ok is False
    assert "Stratum" in result.error


def test_non_number_root_delay_becomes_ok_false():
    raw = dict(_SYNCED_RAW)
    raw["RootDelay"] = "not a number"
    executor = WinrmExecutorStub(responses={("host-g", ntp_sync_status.CHECK_FUNCTION): json.dumps(raw)})
    result = ntp_sync_status.run(executor, "host-g")
    assert result.ok is False
    assert "RootDelay" in result.error


def test_non_string_reference_ip_becomes_ok_false():
    raw = dict(_SYNCED_RAW)
    raw["ReferenceIp"] = 12345
    executor = WinrmExecutorStub(responses={("host-h", ntp_sync_status.CHECK_FUNCTION): json.dumps(raw)})
    result = ntp_sync_status.run(executor, "host-h")
    assert result.ok is False
    assert "ReferenceIp" in result.error


def test_source_differing_from_reference_ip_is_ok():
    # Real, not-yet-observed-but-possible state: Source (who this box
    # syncs with) and ReferenceIp (decoded from the ReferenceId line)
    # need not be the same host -- this check only ever passes both
    # through independently, it doesn't assert they match.
    raw = dict(_SYNCED_RAW)
    raw["Source"] = "some-other-upstream.vpp.local"
    executor = WinrmExecutorStub(responses={("host-m", ntp_sync_status.CHECK_FUNCTION): json.dumps(raw)})
    result = ntp_sync_status.run(executor, "host-m")
    assert result.ok is True
    assert result.value["reference_ip"] == "192.0.2.10"
    assert result.value["source"] == "some-other-upstream.vpp.local"


def test_non_string_source_becomes_ok_false():
    raw = dict(_SYNCED_RAW)
    raw["Source"] = 12345
    executor = WinrmExecutorStub(responses={("host-n", ntp_sync_status.CHECK_FUNCTION): json.dumps(raw)})
    result = ntp_sync_status.run(executor, "host-n")
    assert result.ok is False
    assert "Source" in result.error


def test_frequency_ppb_zero_at_steady_state_is_ok():
    # Real state confirmed against the target: no active correction
    # underway -- a confirmed 0, not a missing/failed reading.
    raw = dict(_SYNCED_RAW)
    raw["FrequencyPpb"] = 0
    executor = WinrmExecutorStub(responses={("host-o", ntp_sync_status.CHECK_FUNCTION): json.dumps(raw)})
    result = ntp_sync_status.run(executor, "host-o")
    assert result.ok is True
    assert result.value["frequency_ppb"] == 0


def test_frequency_ppb_null_while_otherwise_synced_is_ok():
    # Real, distinct state: Get-Counter can fail independently of
    # everything else (a separate mechanism from w32tm text parsing,
    # wrapped in its own try/catch) -- a legitimate "don't know", not
    # a reason to fail the whole check.
    raw = dict(_SYNCED_RAW)
    raw["FrequencyPpb"] = None
    executor = WinrmExecutorStub(responses={("host-p", ntp_sync_status.CHECK_FUNCTION): json.dumps(raw)})
    result = ntp_sync_status.run(executor, "host-p")
    assert result.ok is True
    assert result.value["stratum"] == 4
    assert result.value["frequency_ppb"] is None


def test_non_number_phase_offset_becomes_ok_false():
    raw = dict(_SYNCED_RAW)
    raw["PhaseOffset"] = "not a number"
    executor = WinrmExecutorStub(responses={("host-q", ntp_sync_status.CHECK_FUNCTION): json.dumps(raw)})
    result = ntp_sync_status.run(executor, "host-q")
    assert result.ok is False
    assert "PhaseOffset" in result.error


def test_non_number_frequency_ppb_becomes_ok_false():
    raw = dict(_SYNCED_RAW)
    raw["FrequencyPpb"] = "not a number"
    executor = WinrmExecutorStub(responses={("host-r", ntp_sync_status.CHECK_FUNCTION): json.dumps(raw)})
    result = ntp_sync_status.run(executor, "host-r")
    assert result.ok is False
    assert "FrequencyPpb" in result.error


def test_reachability_zero_after_a_forced_rediscover_is_ok():
    # Real state confirmed against the target: a freshly-reset "reach"
    # register (e.g. right after /resync /rediscover) genuinely starts
    # at 0, not a missing/failed reading.
    raw = dict(_SYNCED_RAW)
    raw["Reachability"] = 0
    executor = WinrmExecutorStub(responses={("host-s", ntp_sync_status.CHECK_FUNCTION): json.dumps(raw)})
    result = ntp_sync_status.run(executor, "host-s")
    assert result.ok is True
    assert result.value["reachability"] == 0


def test_reachability_null_while_otherwise_synced_is_ok():
    # Real, distinct state: /query /peers /verbose is a separate w32tm
    # invocation from /query /status /verbose, can fail or return an
    # unexpected shape independently -- a legitimate "don't know", not
    # a reason to fail the whole check.
    raw = dict(_SYNCED_RAW)
    raw["Reachability"] = None
    executor = WinrmExecutorStub(responses={("host-t", ntp_sync_status.CHECK_FUNCTION): json.dumps(raw)})
    result = ntp_sync_status.run(executor, "host-t")
    assert result.ok is True
    assert result.value["stratum"] == 4
    assert result.value["reachability"] is None


def test_non_integer_reachability_becomes_ok_false():
    raw = dict(_SYNCED_RAW)
    raw["Reachability"] = "not an int"
    executor = WinrmExecutorStub(responses={("host-u", ntp_sync_status.CHECK_FUNCTION): json.dumps(raw)})
    result = ntp_sync_status.run(executor, "host-u")
    assert result.ok is False
    assert "Reachability" in result.error


def test_bare_array_not_object_becomes_ok_false():
    executor = WinrmExecutorStub(responses={("host-i", ntp_sync_status.CHECK_FUNCTION): "[]"})
    result = ntp_sync_status.run(executor, "host-i")
    assert result.ok is False
    assert result.error is not None


def test_executor_failure_becomes_ok_false():
    executor = WinrmExecutorStub(failures={("host-j", ntp_sync_status.CHECK_FUNCTION): "kerberos auth failed"})
    result = ntp_sync_status.run(executor, "host-j")
    assert result.ok is False
    assert "kerberos auth failed" in result.error


def test_unparseable_output_becomes_ok_false():
    executor = WinrmExecutorStub(responses={("host-k", ntp_sync_status.CHECK_FUNCTION): "not json"})
    result = ntp_sync_status.run(executor, "host-k")
    assert result.ok is False
    assert result.error is not None


def test_invokes_only_the_whitelisted_function_name():
    executor = WinrmExecutorStub(
        responses={("host-l", ntp_sync_status.CHECK_FUNCTION): json.dumps(_SYNCED_RAW)},
    )
    ntp_sync_status.run(executor, "host-l")
    assert executor.calls == [("host-l", "Get-NtpSyncStatus")]
