from app.checks import network_traffic
from app.winrm_executor_stub import WinrmExecutorStub


def test_single_interface():
    executor = WinrmExecutorStub(
        responses={
            ("host-a", network_traffic.CHECK_FUNCTION): (
                '[{"InterfaceName": "Ethernet0", "BytesSent": 1000000, "BytesReceived": 2000000,'
                ' "NetEnabled": true, "NetConnectionStatus": 2}]'
            ),
        }
    )
    result = network_traffic.run(executor, "host-a")
    assert result.ok is True
    assert result.value == {
        "interfaces": [
            {
                "interface_name": "Ethernet0",
                "bytes_sent": 1000000,
                "bytes_received": 2000000,
                "net_enabled": True,
                "net_connection_status": 2,
            }
        ]
    }


def test_multiple_interfaces():
    executor = WinrmExecutorStub(
        responses={
            ("host-b", network_traffic.CHECK_FUNCTION): (
                '[{"InterfaceName": "Ethernet0", "BytesSent": 100, "BytesReceived": 200,'
                ' "NetEnabled": true, "NetConnectionStatus": 2},'
                ' {"InterfaceName": "Ethernet1", "BytesSent": 300, "BytesReceived": 400,'
                ' "NetEnabled": false, "NetConnectionStatus": 0}]'
            ),
        }
    )
    result = network_traffic.run(executor, "host-b")
    assert result.ok is True
    assert result.value == {
        "interfaces": [
            {
                "interface_name": "Ethernet0",
                "bytes_sent": 100,
                "bytes_received": 200,
                "net_enabled": True,
                "net_connection_status": 2,
            },
            {
                "interface_name": "Ethernet1",
                "bytes_sent": 300,
                "bytes_received": 400,
                "net_enabled": False,
                "net_connection_status": 0,
            },
        ]
    }


def test_correlation_miss_gives_null_status_not_fabricated():
    # No Win32_NetworkAdapter correlated for this interface -- the JEA
    # function's own explicit "we don't know" result, must pass through
    # as null, not default to some guessed enabled/connected state.
    executor = WinrmExecutorStub(
        responses={
            ("host-p", network_traffic.CHECK_FUNCTION): (
                '[{"InterfaceName": "Ethernet0", "BytesSent": 100, "BytesReceived": 200,'
                ' "NetEnabled": null, "NetConnectionStatus": null}]'
            ),
        }
    )
    result = network_traffic.run(executor, "host-p")
    assert result.ok is True
    assert result.value["interfaces"][0]["net_enabled"] is None
    assert result.value["interfaces"][0]["net_connection_status"] is None


def test_missing_status_keys_entirely_treated_as_null():
    # Older/cached output shape without the new keys at all -- same
    # "don't know" outcome as present-but-null, not an error.
    executor = WinrmExecutorStub(
        responses={
            ("host-q", network_traffic.CHECK_FUNCTION): (
                '[{"InterfaceName": "Ethernet0", "BytesSent": 100, "BytesReceived": 200}]'
            ),
        }
    )
    result = network_traffic.run(executor, "host-q")
    assert result.ok is True
    assert result.value["interfaces"][0]["net_enabled"] is None
    assert result.value["interfaces"][0]["net_connection_status"] is None


def test_non_bool_net_enabled_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={
            ("host-r", network_traffic.CHECK_FUNCTION): (
                '[{"InterfaceName": "Ethernet0", "BytesSent": 100, "BytesReceived": 200,'
                ' "NetEnabled": "yes", "NetConnectionStatus": 2}]'
            ),
        }
    )
    result = network_traffic.run(executor, "host-r")
    assert result.ok is False
    assert "NetEnabled" in result.error


def test_non_int_net_connection_status_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={
            ("host-s", network_traffic.CHECK_FUNCTION): (
                '[{"InterfaceName": "Ethernet0", "BytesSent": 100, "BytesReceived": 200,'
                ' "NetEnabled": true, "NetConnectionStatus": "Connected"}]'
            ),
        }
    )
    result = network_traffic.run(executor, "host-s")
    assert result.ok is False
    assert "NetConnectionStatus" in result.error


def test_no_interfaces_empty_array():
    executor = WinrmExecutorStub(responses={("host-c", network_traffic.CHECK_FUNCTION): "[]"})
    result = network_traffic.run(executor, "host-c")
    assert result.ok is True
    assert result.value == {"interfaces": []}


def test_bare_object_not_array_becomes_ok_false():
    # Same -AsArray/-InputObject-class gotcha as disk_space -- a single-
    # interface host whose JEA function collapsed to a bare object.
    executor = WinrmExecutorStub(
        responses={
            ("host-d", network_traffic.CHECK_FUNCTION): (
                '{"InterfaceName": "Ethernet0", "BytesSent": 100, "BytesReceived": 200}'
            ),
        }
    )
    result = network_traffic.run(executor, "host-d")
    assert result.ok is False
    assert "expected a JSON array" in result.error


def test_executor_failure_becomes_ok_false():
    executor = WinrmExecutorStub(
        failures={("host-e", network_traffic.CHECK_FUNCTION): "kerberos auth failed"}
    )
    result = network_traffic.run(executor, "host-e")
    assert result.ok is False
    assert "kerberos auth failed" in result.error


def test_unparseable_output_becomes_ok_false():
    executor = WinrmExecutorStub(responses={("host-f", network_traffic.CHECK_FUNCTION): "not json"})
    result = network_traffic.run(executor, "host-f")
    assert result.ok is False
    assert result.error is not None


def test_non_object_interface_entry_becomes_ok_false():
    executor = WinrmExecutorStub(responses={("host-g", network_traffic.CHECK_FUNCTION): "[1, 2]"})
    result = network_traffic.run(executor, "host-g")
    assert result.ok is False
    assert "not an object" in result.error


def test_missing_key_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={
            ("host-h", network_traffic.CHECK_FUNCTION): '[{"InterfaceName": "Ethernet0", "BytesSent": 100}]',
        }
    )
    result = network_traffic.run(executor, "host-h")
    assert result.ok is False
    assert "BytesReceived" in result.error


def test_non_integer_bytes_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={
            ("host-i", network_traffic.CHECK_FUNCTION): (
                '[{"InterfaceName": "Ethernet0", "BytesSent": "100", "BytesReceived": 200}]'
            ),
        }
    )
    result = network_traffic.run(executor, "host-i")
    assert result.ok is False
    assert "non-integer" in result.error


def test_empty_interface_name_becomes_ok_false():
    executor = WinrmExecutorStub(
        responses={
            ("host-j", network_traffic.CHECK_FUNCTION): (
                '[{"InterfaceName": "", "BytesSent": 100, "BytesReceived": 200}]'
            ),
        }
    )
    result = network_traffic.run(executor, "host-j")
    assert result.ok is False
    assert "InterfaceName" in result.error


def test_invokes_only_the_whitelisted_function_name():
    executor = WinrmExecutorStub(responses={("host-k", network_traffic.CHECK_FUNCTION): "[]"})
    network_traffic.run(executor, "host-k")

    assert executor.calls == [("host-k", "Get-NetworkInterfaceStats")]
