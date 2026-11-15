# SPDX-License-Identifier: GPL-2.0-or-later
"""Read-only inspection commands: probe, sniff, diagnose.

No PyGObject needed, and nothing here changes a device setting.
"""

import os
import time

from .drivers import find_driver, matching_drivers
from .hidraw import HidHandle, access_help, enumerate_nodes, group_nodes


def probe():
    """Describe all hidraw devices and their matching driver, without querying."""
    devices = group_nodes(enumerate_nodes())
    result = []
    for device in devices:
        driver = find_driver(device)
        result.append(
            {
                "key": device.key,
                "name": device.name,
                "driver": driver.name if driver else None,
                "kind": driver.kind if driver else None,
                "nodes": [
                    {
                        "path": node.path,
                        "vendor": f"{node.vendor:04x}",
                        "product": f"{node.product:04x}",
                        "interface": node.interface,
                    }
                    for node in device.nodes
                ],
            }
        )
    return result


def sniff(seconds=3.0):
    """Passively dump input reports from every accessible hidraw node.

    Read-only: nothing is written to any device. Useful for devices that push
    their status on their own.
    """
    result = []
    for device in group_nodes(enumerate_nodes()):
        for node in device.nodes:
            reports = []
            try:
                with HidHandle(node.path) as hid:
                    hid.flush()
                    deadline = time.time() + seconds
                    while time.time() < deadline:
                        data = hid.read_input(timeout=0.2)
                        if data:
                            reports.append(data.hex(" "))
            except OSError as e:
                reports.append(f"error: {e}")
            result.append(
                {
                    "name": device.name,
                    "path": node.path,
                    "interface": node.interface,
                    "reports": reports,
                }
            )
    return result


def node_entry(node):
    """Access state of one node, with the reason when it cannot be opened."""
    accessible = os.access(node.path, os.R_OK | os.W_OK)
    return {
        "path": node.path,
        "vendor": f"{node.vendor:04x}",
        "product": f"{node.product:04x}",
        "interface": node.interface,
        "accessible": accessible,
        "reason": None if accessible else access_help(node)[0],
    }


def diagnose():
    """Explain every hidraw device, its driver and the raw answers."""
    result = []
    for device in group_nodes(enumerate_nodes()):
        drivers = matching_drivers(device)
        entry = {
            "key": device.key,
            "name": device.name,
            "nodes": [node_entry(node) for node in device.nodes],
            "drivers": [driver.name for driver in drivers],
            "attempts": [],
        }
        for driver in drivers:
            try:
                battery = driver.read(device)
            except Exception as e:  # a broken driver must not abort the report
                battery = f"error: {e}"
            entry["attempts"].append(
                {
                    "driver": driver.name,
                    "result": list(battery) if isinstance(battery, tuple) else battery,
                }
            )
        result.append(entry)
    return result
