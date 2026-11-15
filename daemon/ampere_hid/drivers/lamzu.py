# SPDX-License-Identifier: GPL-2.0-or-later
"""Lamzu wireless receivers: Maya X 8K, Inca, Paro, Maya Champion, ...

3554 receivers use Compx (report 8); 373e and 37b0 use Aurora (65-byte feature
report, ID 0). The split is unreliable, so all three VIDs are claimed and both
transports are tried.

Tested with a Maya X 8K (373e:001e) and a Thorn receiver (3554:f50d).
"""

import time

from ..hidraw import REPORT_SIZE, HidHandle, can_access, query_feature, warn_permission
from . import compx
from .base import Driver

VENDOR_IDS = {0x3554, 0x373E, 0x37B0}

COMPX = "compx"
AURORA = "aurora"
AURORA_ONLINE = 0xA1
AURORA_OFFLINE = 0xA0
# A silent receiver costs ~2 s per sweep; retry with shorter windows after
# misses and probe fully again every fifth sweep.
FAST_TIMEOUT = 0.12
FAST_REPEATS = 2
FAILURES_BEFORE_FAST = 2
FULL_PROBE_EVERY = 5


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


def aurora_query(node, repeats=6):
    """Ask with the new protocol; return its frame or ``None``.

    ``0xA1`` means the mouse is reachable and carries the reading, ``0xA0`` no
    link. The legacy answer repeats the last value either way.
    """
    packet = build_aurora_battery_packet(True)
    try:
        with HidHandle(node.path) as hid:
            hid.flush()
            if not hid.send(packet):
                return None
            for _ in range(repeats):
                time.sleep(0.02)
                raw = hid.read_feature(REPORT_SIZE, 0x00)
                if not raw:
                    continue
                if len(raw) >= 2 and raw[0] == 0x00:
                    raw = raw[1:]
                if raw[0] in (AURORA_ONLINE, AURORA_OFFLINE):
                    return raw
    except OSError:
        return None
    return None


def aurora_read_node(node, trust_offline=False, quick=False):
    """Query the battery over Aurora; return ``(reading, online)``.

    ``online`` is true when the new protocol answered ``0xA1``. With
    ``trust_offline`` an ``0xA0`` ends the query, because the legacy answer would
    only repeat the last value; without it the legacy query stays the fallback.
    """
    offline = False
    repeats = FAST_REPEATS if quick else 6
    # New protocol twice: a sleeping mouse occasionally drops the first request.
    for _ in range(1 if quick else 2):
        answer = aurora_query(node, repeats)
        if answer is None:
            continue
        if answer[0] == AURORA_OFFLINE:
            offline = True
            break
        battery = parse_aurora_battery_response(answer)
        if battery:
            return battery, True

    if offline and trust_offline:
        return None, False

    battery = query_feature(
        node,
        build_aurora_battery_packet(False),
        parse_aurora_battery_response,
        repeats=repeats,
    )
    return battery, False


class LamzuDriver(Driver):
    name = "Lamzu"
    kind = "mouse"
    vids = frozenset(VENDOR_IDS)
    preferred_interfaces = (2,)

    def __init__(self):
        # Transport that answered last, so polling stays fast.
        self._preferred = {}
        # Keys that answered 0xA1 at least once; only for those does 0xA0 mean
        # "switched off" instead of "use the legacy query".
        self._online = set()
        # Sweeps in a row without a reading.
        self._failures = {}

    def _transports(self, key):
        order = [COMPX, AURORA]
        preferred = self._preferred.get(key)
        if preferred in order:
            order.remove(preferred)
            order.insert(0, preferred)
        return order

    def read(self, device):
        key = device.key
        failures = self._failures.get(key, 0)
        quick = failures >= FAILURES_BEFORE_FAST and failures % FULL_PROBE_EVERY != 0

        for node in device.sorted_nodes(self.preferred_interfaces):
            if not can_access(node):
                warn_permission(node)
                continue

            for transport in self._transports(key):
                if transport == COMPX:
                    battery = compx.read_node(
                        node, timeout=FAST_TIMEOUT if quick else compx.TIMEOUT
                    )
                else:
                    battery, online = aurora_read_node(
                        node, key in self._online, quick
                    )
                    if online:
                        self._online.add(key)
                if battery:
                    self._preferred[key] = transport
                    self._failures[key] = 0
                    return battery

        self._failures[key] = failures + 1
        return None
