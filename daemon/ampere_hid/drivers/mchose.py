# SPDX-License-Identifier: GPL-2.0-or-later
"""MCHOSE wireless mice, RealTek chip (vendor IDs 0x5253 and 0x3837).

Both IDs speak the same protocol. 0x5253 covers the L7/M7 receivers, 0x3837 the
newer A7 V2 and K7. The battery is read with a feature report, ID 0x11 (20-byte
payload) or 0x12 (64-byte payload), command 0x06. Every payload byte is inverted
(^ 0xFF), padding included, so a request is

    report ID 0x12, then F9 FF FF FF ...   (0x06 inverted, zeros after it)

The reply echoes ``cmd ^ 0xFF`` in byte 1 and inverts its payload from byte 2.
Decoded, that payload is

    vid:u16  model:u16  firmware:u32  flags:u8  level:u8  charging:u8  ...

The receiver only relays a value while the mouse is awake and answers with
zeros while it sleeps. The request has to be re-sent before every read, because
a repeated read just returns the request itself.

Some receivers also push a device-info notice on input report 0x13, subtype
0x1d, whose byte 5 XOR 0xFF is the same level. That is used as a fallback.

Reference: HeyOkay/HaloBattery (confirmed on an M7 Ultra) and alexfrih/mchose-linux
(M HUB notes). Tested with an A7 V2 Pro (``3837:100a``).
"""

import time

from ..hidraw import HidHandle, can_access, warn_permission
from .base import Driver

VENDOR_IDS = {0x5253, 0x3837}

SHORT_REPORT = 0x11
LONG_REPORT = 0x12
SHORT_LEN = 20   # payload bytes behind the report id
LONG_LEN = 64
CMD_STATUS = 0x06
CHANNELS = ((SHORT_REPORT, SHORT_LEN), (LONG_REPORT, LONG_LEN))

# Re-ask before each read; the real answer normally arrives on a later exchange.
ATTEMPTS = 4
ATTEMPT_GAP = 0.12
PUSH_TIMEOUT = 1.5

PUSH_REPORT = 0x13
PUSH_SUBTYPE = 0x1D
PUSH_LEVEL_OFFSET = 5


def invert(data):
    return bytes(b ^ 0xFF for b in data)


def build_request(cmd, report=LONG_REPORT, length=LONG_LEN):
    """Report id, then the command and a zero tail, all payload bytes inverted."""
    body = bytes([cmd]).ljust(length, b"\x00")[:length]
    return bytes([report]) + invert(body)


def parse_status_response(resp):
    """Return ``(percentage, state)`` or ``None``.

    A reply only counts when it echoes ``cmd ^ 0xFF``, carries a MCHOSE vendor
    id and holds a level in range. The buffer also answers with zeros or with
    the request echoed back between real answers.
    """
    if not resp or len(resp) < 13:
        return None
    r = bytes(resp)
    if r[0] not in (SHORT_REPORT, LONG_REPORT) or r[1] != (CMD_STATUS ^ 0xFF):
        return None

    payload = invert(r[2:])
    if len(payload) < 11:
        return None
    if int.from_bytes(payload[0:2], "little") not in VENDOR_IDS:
        return None

    level, charge = payload[9], payload[10]
    if level > 100:
        return None
    if charge:
        return level, 2 if level >= 100 else 1
    return level, 0


def parse_push_report(data):
    """Decode the pushed 0x13 device-info notice, or return ``None``.

    Raw byte 5 XOR 0xFF is the same level the feature query reports.
    """
    if not data or len(data) <= PUSH_LEVEL_OFFSET:
        return None
    if data[0] != PUSH_REPORT or data[1] != PUSH_SUBTYPE:
        return None
    level = data[PUSH_LEVEL_OFFSET] ^ 0xFF
    if level > 100:
        return None
    return level, 0


def read_node(node):
    """Query the battery over the MCHOSE transport, or return ``None``."""
    with HidHandle(node.path) as hid:
        hid.flush()

        for report, length in CHANNELS:
            request = build_request(CMD_STATUS, report, length)
            for _ in range(ATTEMPTS):
                if not hid.send_feature(request):
                    break  # this collection does not take the report
                time.sleep(ATTEMPT_GAP)
                parsed = parse_status_response(hid.read_feature(length + 1, report))
                if parsed:
                    return parsed

        # Fallback: the receiver's periodic device-info push.
        deadline = time.time() + PUSH_TIMEOUT
        while time.time() < deadline:
            parsed = parse_push_report(hid.read_input(timeout=0.2))
            if parsed:
                return parsed
        return None


class MchoseDriver(Driver):
    name = "MCHOSE"
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
