"""The check whitelist.

This is the enforcement point for "JSON only, never free-form script":
check_name from a request is looked up here, and only here. There is
no code path anywhere in this daemon that runs a caller-supplied
command string. Deliberately code (not runtime config) -- adding a
check means a reviewed code change, not a config edit.
"""

from __future__ import annotations

from collections.abc import Callable

from app.checks import (
    cpu_usage,
    disk_space,
    hardware_inventory,
    memory_usage,
    network_traffic,
    ntp_sync_status,
    reboot_pending,
    service_status_w32time,
    service_status_wuauserv,
    winupdate_pending,
)
from app.models import CheckResult
from app.winrm_executor import WinrmExecutor

CheckFn = Callable[[WinrmExecutor, str], CheckResult]

CHECKS: dict[str, CheckFn] = {
    "reboot-pending": reboot_pending.run,
    "service-status-wuauserv": service_status_wuauserv.run,
    "service-status-w32time": service_status_w32time.run,
    "winupdate-pending": winupdate_pending.run,
    "disk-space": disk_space.run,
    "network-traffic": network_traffic.run,
    "memory-usage": memory_usage.run,
    "cpu-usage": cpu_usage.run,
    "hardware-inventory": hardware_inventory.run,
    "ntp-sync-status": ntp_sync_status.run,
}


def get_check(check_name: str) -> CheckFn | None:
    return CHECKS.get(check_name)
