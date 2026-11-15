# SPDX-License-Identifier: GPL-2.0-or-later
"""Lamzu wireless receivers: Maya X 8K, Inca, Paro, Maya Champion, ...

``3554`` receivers use the Compx protocol (report ID 8); ``373e`` and ``37b0``
use Aurora (65-byte feature report, ID 0). The split is not reliable, so this
driver claims all three VIDs and tries both transports, remembering whichever
answered last.

Tested with a Maya X 8K (``373e:001e``, Aurora) and a Thorn receiver
(``3554:f50d``, Compx). Layout reference: github.com/anany1702/lamzu-ctl
"""

from ..hidraw import can_access, query_feature, warn_permission
from . import compx
from .base import Driver

VENDOR_IDS = {0x3554, 0x373E, 0x37B0}

COMPX = "compx"
AURORA = "aurora"


def build_aurora_battery_packet(new_protocol=True):
    p = bytearray(64)
    if new_protocol:
        p[2] = 0x02  # Mouse
        p[3] = 0x02  # Length
        p[5] = 0x83  # Get battery/status
    else:
        p[1] = 0x02  # Mouse
        p[2] = 0x8F  # Get battery/status (legacy)
        p[3] = 0x01  # Wireless receiver target
    return bytes([0x00]) + bytes(p)


def parse_aurora_battery_response(resp):
    if not resp:
        return None

    # Some kernels include the leading report ID, some do not.
    r = resp
    if len(r) >= 2 and r[0] == 0x00 and r[1] == 0xA1:
        r = r[1:]
    if r[0] != 0xA1:
        return None

    # New protocol: A1 .. 02 .. 83 <state> <percent>
    if len(r) >= 8 and r[3] == 0x02 and r[5] == 0x83:
        state, level = r[6], r[7]
    # Legacy protocol: A1 02 8F ..
    elif len(r) >= 6 and r[1] == 0x02 and r[2] == 0x8F:
        # The Maya X keeps state/level in the new-protocol offsets; other
        # devices use 4/5. Never report the all-zero template as a real 0%.
        if len(r) >= 8 and (r[6] or r[7]):
            state, level = r[6], r[7]
        else:
            state, level = r[4], r[5]
            if state == 0 and level == 0:
                return None
    else:
        return None

    if level > 100:
        return None
    return level, state


def aurora_read_node(node):
    """Query the battery over the Aurora transport, or return ``None``."""
    # New protocol twice: a sleeping mouse occasionally drops the first
    # request. The legacy request is the last fallback.
    for new_protocol in (True, True, False):
        battery = query_feature(
            node, build_aurora_battery_packet(new_protocol), parse_aurora_battery_response
        )
        if battery:
            return battery
    return None


class LamzuDriver(Driver):
    name = "Lamzu"
    kind = "mouse"
    vids = frozenset(VENDOR_IDS)
    preferred_interfaces = (2,)

    def __init__(self):
        # Remember the transport that answered last, so polling stays fast.
        self._preferred = {}

    def _transports(self, key):
        order = [COMPX, AURORA]
        preferred = self._preferred.get(key)
        if preferred in order:
            order.remove(preferred)
            order.insert(0, preferred)
        return order

    def read(self, device):
        for node in device.sorted_nodes(self.preferred_interfaces):
            if not can_access(node):
                warn_permission(
                    node.path,
                    "run `ampere-hid install --udev`",
                )
                continue

            for transport in self._transports(device.key):
                battery = (
                    compx.read_node(node) if transport == COMPX else aurora_read_node(node)
                )
                if battery:
                    self._preferred[device.key] = transport
                    return battery
        return None
