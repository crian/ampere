# SPDX-License-Identifier: GPL-2.0-or-later
"""Collect the battery readings of all devices that have a driver."""

from .drivers import find_driver
from .hidraw import can_access, enumerate_nodes, group_nodes, log


def read_devices():
    """Return the matched devices and the fresh readings, keyed by device."""
    devices = group_nodes(enumerate_nodes())
    matched = []
    fresh = {}
    for device in devices:
        driver = find_driver(device)
        if driver is None:
            continue
        matched.append(device)
        try:
            battery = driver.read(device)
        except OSError as e:
            # Unplugged between enumerating and opening, EIO and the like: the
            # other devices must still be reported.
            log(f"{device.name}: read failed: {e}")
            continue
        if not battery:
            continue
        level, state = battery
        fresh[device.key] = {
            "key": device.key,
            "name": device.name,
            "kind": driver.kind,
            "pct": level,
            "state": state,
        }
    return matched, fresh


def query_all():
    matched, fresh = read_devices()
    return list(fresh.values()), len(matched)


def silent_paths(matched, fresh):
    """Nodes of matching devices that gave no reading in this sweep."""
    return [
        node.path
        for device in matched
        if device.key not in fresh
        for node in device.nodes
        if can_access(node)
    ]
