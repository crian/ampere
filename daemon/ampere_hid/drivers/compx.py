# SPDX-License-Identifier: GPL-2.0-or-later
"""Compx / Nordic 52840 mice: ATK, VXE, VGN, Zaopin, Scyrox, Dareu, ...

Shared silicon, not a single brand: many receivers enumerate as "Compx" and
every rebrand speaks this. Reports are 17 bytes with report ID 8, the second
byte selects the sub-command. The battery query is sub-command 4:

    write   08 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 49
    read    08 04 00 00 00 02 23 00 0e 94 00 00 00 00 00 00 82

    byte 0     report ID
    byte 1     sub-command (4 = battery)
    byte 5     status, unused
    byte 6     battery percent (0x23 = 35)
    byte 7     wired/charging flag
    bytes 8-9  voltage in mV (0x0e94 = 3732)
    byte 16    checksum; all 17 bytes add up to 0x55

Reference: Fan4Metal/mouse_tray (Compx/Nordic chipset); the same report layout
is used by the Linux ``hid-vxe-r1`` module. Tested with an ATK A9 Max Ultra 2.0
(``373b:11fe``).
"""

import time

from ..hidraw import HidHandle, can_access, warn_permission
from .base import Driver

# Compx/Nordic 52840 VIDs. The Lamzu driver claims the receivers it shares
# (3554, 37b0) and tries Compx itself, so only the rest is listed here.
VENDOR_IDS = {0x373B, 0x260D, 0x33E4}

REPORT_ID = 0x08
BATTERY_CMD = 0x04
TIMEOUT = 0.3
# A silent receiver costs about a second per sweep; after misses, use shorter
# windows and probe all nodes again every fifth sweep.
FAST_TIMEOUT = 0.12
FAILURES_BEFORE_FAST = 2
FULL_PROBE_EVERY = 5


def checksum(data):
    """The 17-byte frames sum to 0x55 mod 256."""
    return (85 - (sum(data[:15]) & 0xFF)) & 0xFF


def build_battery_packet():
    t = bytearray(16)
    t[0] = BATTERY_CMD
    t[15] = (checksum(t) - REPORT_ID) & 0xFF
    return bytes([REPORT_ID]) + bytes(t)


def parse_battery_response(resp):
    """Return ``(percentage, state)`` or ``None``.

    Percentage is payload byte 5, the power state payload byte 6
    (0 = discharging, 1 = charging, 2 = full).
    """
    if not resp:
        return None
    # Answers may arrive with or without the leading report ID.
    if resp[0] != REPORT_ID:
        if resp[0] != BATTERY_CMD:
            return None
        resp = bytes([REPORT_ID]) + resp
    if len(resp) < 8 or resp[1] != BATTERY_CMD:
        return None

    level, state = resp[6], resp[7]
    if level > 100:
        return None
    return level, state


def read_node(node, timeout=TIMEOUT):
    """Query the battery over the Compx transport, or return ``None``."""
    with HidHandle(node.path) as hid:
        hid.flush()
        # Send as output report and as feature report, like the vendor tool.
        if not hid.send(build_battery_packet()):
            return None

        deadline = time.time() + timeout
        while time.time() < deadline:
            parsed = parse_battery_response(hid.read_input(timeout=0.05))
            if parsed:
                return parsed
            time.sleep(0.01)

        return parse_battery_response(hid.read_feature(17, REPORT_ID))


class CompxDriver(Driver):
    name = "Compx"
    kind = "mouse"
    vids = frozenset(VENDOR_IDS)
    preferred_interfaces = (2, 1, 0)

    def __init__(self):
        # Remember the node that answered and how many sweeps in a row failed,
        # so a receiver without a mouse does not cost a full probe every time.
        self._node = {}
        self._failures = {}

    def read(self, device):
        key = device.key
        failures = self._failures.get(key, 0)
        quick = failures >= FAILURES_BEFORE_FAST and failures % FULL_PROBE_EVERY != 0

        nodes = device.sorted_nodes(self.preferred_interfaces)
        known = self._node.get(key)
        if known:
            nodes.sort(key=lambda node: node.path != known)

        for node in nodes:
            if not can_access(node):
                warn_permission(node)
                continue
            battery = read_node(node, timeout=FAST_TIMEOUT if quick else TIMEOUT)
            if battery:
                self._node[key] = node.path
                self._failures[key] = 0
                return battery

        self._failures[key] = failures + 1
        return None
