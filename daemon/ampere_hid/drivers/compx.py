# SPDX-License-Identifier: GPL-2.0-or-later
"""Compx / Nordic 52840 mice: ATK, VXE, VGN, Zaopin, Scyrox, Dareu, ...

Shared silicon, not a single brand: many receivers enumerate as "Compx" and
every rebrand speaks this. Reports are 17 bytes with report ID 8, the second
byte selects the sub-command. The battery query is sub-command 4:

    write   08 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 49
    read    08 04 00 00 00 02 23 00 0e 94 00 00 00 00 00 00 82
            ^id ^sub          ^st ^%  ^v(voltage mV)         ^checksum

Percent is payload byte 5 (overall byte 6), byte 7 is the wired/charging flag.
All 17 bytes sum to 0x55 mod 256, hence the fixed 0x49 in the request.

Reference: Fan4Metal/ATK_tray and the Linux ``hid-vxe-r1`` module. Tested with
an ATK A9 Max Ultra 2.0 (``373b:11fe``).
"""

import time

from .base import Driver
from ..hidraw import HidHandle, can_access, warn_permission

# VIDs seen for the Compx/Nordic 52840 silicon. The Lamzu driver claims the
# receivers it shares with Lamzu devices (3554, 37b0) and tries the Compx
# transport itself, so this driver only needs the remaining VIDs.
VENDOR_IDS = {0x373B, 0x260D, 0x33E4}

REPORT_ID = 0x08
BATTERY_CMD = 0x04
TIMEOUT = 0.3


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


def read_node(node):
    """Query the battery over the Compx transport, or return ``None``."""
    with HidHandle(node.path) as hid:
        hid.flush()
        # Send as output report and as feature report, like the vendor tool.
        if not hid.send(build_battery_packet()):
            return None

        deadline = time.time() + TIMEOUT
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

    def read(self, device):
        for node in device.sorted_nodes(self.preferred_interfaces):
            if not can_access(node):
                warn_permission(
                    node.path,
                    "install daemon/70-ampere-hid.rules and reload udev",
                )
                continue
            battery = read_node(node)
            if battery:
                return battery
        return None
