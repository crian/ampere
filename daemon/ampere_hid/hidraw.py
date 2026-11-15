# SPDX-License-Identifier: GPL-2.0-or-later
"""hidraw enumeration and HID report helpers.

Vendor mice expose their battery on a hidraw node. Everything platform specific
lives here, so a driver only implements ``read()``.
"""

import contextlib
import fcntl
import glob
import os
import re
import select
import sys
import time

from . import udev

REPORT_SIZE = 65


def _hidioctl(size, request):
    """HIDIOC{S,G}FEATURE(n) for an arbitrary report size."""
    return 0xC0004800 | request | (size << 16)


def sfeature(size):
    return _hidioctl(size, 0x06)


def gfeature(size):
    return _hidioctl(size, 0x07)


# Drivers may need to warn about permissions only once.
_warned = set()


def log(message):
    print(f"[ampere-hid] {message}", file=sys.stderr, flush=True)


def read_text(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def uevent_field(uevent, name):
    m = re.search(rf"^{name}=(.*)$", uevent, re.MULTILINE)
    return m.group(1).strip() if m else ""


def clean_name(name):
    parts = name.split()
    # "LAMZU LAMZU Maya X 8K Dongle" -> "LAMZU Maya X 8K Dongle"
    if len(parts) >= 2 and parts[0].lower() == parts[1].lower():
        parts = parts[1:]
    return " ".join(parts) or "HID device"


def access_help(node):
    """Return ``(reason, hint)`` for a node the user may not open."""
    if (node.vendor, node.product) not in udev.product_ids():
        return (
            f"{node.vendor:04x}:{node.product:04x} is not in the udev table",
            "add its product ID to ampere_hid/udev.py",
        )
    return (
        "the udev rule is missing or outdated",
        "run `ampere-hid install --udev`",
    )


def warn_permission(node):
    if node.path in _warned:
        return
    _warned.add(node.path)
    reason, hint = access_help(node)
    log(f"no access to {node.path}; {reason}; {hint}")


class HidNode:
    def __init__(self, path, vendor, product, name, uniq, interface, phys=""):
        self.path = path
        self.vendor = vendor
        self.product = product
        self.name = name
        self.uniq = uniq
        self.interface = interface
        self.phys = phys

    @property
    def key(self):
        """Identifier for the receiver a node belongs to.

        The serial if there is one, otherwise vendor, product and the USB path:
        two identical receivers share everything else.
        """
        if self.uniq:
            return self.uniq
        base = re.sub(r"/input\d+$", "", self.phys) or self.path
        return f"{self.vendor:04x}:{self.product:04x}:{base}"


class HidDevice:
    def __init__(self, key, name):
        self.key = key
        self.name = name
        self.nodes = []

    def add_node(self, node):
        self.nodes.append(node)
        if node.name and not self.name:
            self.name = node.name

    def sorted_nodes(self, preferred_interfaces=()):
        preferred = tuple(preferred_interfaces)
        return sorted(
            self.nodes,
            key=lambda n: (
                0 if n.interface in preferred else 1,
                n.interface if n.interface >= 0 else 99,
                n.path,
            ),
        )


def enumerate_nodes():
    nodes = []
    for sysfs in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent = read_text(os.path.join(sysfs, "device", "uevent"))
        m = re.search(
            r"^HID_ID=([0-9A-Fa-f]{4}):([0-9A-Fa-f]{8}):([0-9A-Fa-f]{8})$",
            uevent,
            re.MULTILINE,
        )
        if not m:
            continue

        phys = uevent_field(uevent, "HID_PHYS")
        mi = re.search(r"input(\d+)$", phys)
        nodes.append(
            HidNode(
                path="/dev/" + os.path.basename(sysfs),
                vendor=int(m.group(2), 16),
                product=int(m.group(3), 16),
                name=clean_name(uevent_field(uevent, "HID_NAME")),
                uniq=uevent_field(uevent, "HID_UNIQ"),
                interface=int(mi.group(1)) if mi else -1,
                phys=phys,
            )
        )
    return nodes


def group_nodes(nodes):
    """Group all hidraw nodes that belong to the same receiver."""
    by_key = {}
    for node in nodes:
        device = by_key.get(node.key)
        if device is None:
            device = HidDevice(node.key, node.name)
            by_key[node.key] = device
        device.add_node(node)
    return list(by_key.values())


def can_access(node):
    return os.access(node.path, os.R_OK | os.W_OK)


def wait_for_activity(paths, timeout, wake_fd=None):
    """Block until one of the hidraw nodes reports input, or the timeout expires.

    A sleeping mouse sends nothing, so this uses no CPU while idle and returns
    as soon as the mouse moves, which is when a reading becomes possible again.

    ``wake_fd`` is the read end of a pipe: when it becomes readable the wait
    ends early with ``True``, so the caller can react to something else, such as
    a newly plugged-in device, without waiting out the timeout.
    """
    fds = []
    try:
        for path in paths:
            try:
                fds.append(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
            except OSError:
                continue
        watch = list(fds)
        if wake_fd is not None:
            watch.append(wake_fd)
        if not fds:
            # Nothing to watch (e.g. missing permission): don't spin.
            timeout = min(timeout, 1.0)
            if not watch:
                time.sleep(timeout)
                return False
        readable, _, _ = select.select(watch, [], [], timeout)
        return bool(readable)
    finally:
        for fd in fds:
            with contextlib.suppress(OSError):
                os.close(fd)


def drain(fd):
    while select.select([fd], [], [], 0)[0]:
        try:
            os.read(fd, 4096)
        except OSError:
            break


class HidHandle:
    """An open hidraw node that can send and receive HID reports.

    Some vendors answer a query as a feature report, others as a normal input
    report; the vendor tool usually sends every packet as output *and* feature
    report. Drivers can therefore use both directions.
    """

    def __init__(self, path):
        self.path = path
        self._fd = None

    def __enter__(self):
        self._fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK)
        return self

    def __exit__(self, *_exc):
        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        return False

    def flush(self):
        drain(self._fd)

    def send_output(self, packet):
        """Send a packet as an output (interrupt) report."""
        try:
            os.write(self._fd, packet)
            return True
        except OSError:
            return False

    def send_feature(self, packet):
        """Send a packet as a feature report."""
        try:
            fcntl.ioctl(self._fd, sfeature(len(packet)), packet)
            return True
        except OSError:
            return False

    def send(self, packet):
        """Send a packet as output report and as feature report.

        Some devices answer only via one of the two; the vendor tools send
        both, so we do the same.
        """
        sent_output = self.send_output(packet)
        sent_feature = self.send_feature(packet)
        return sent_output or sent_feature

    def read_input(self, timeout=0.03):
        if not select.select([self._fd], [], [], timeout)[0]:
            return None
        try:
            return os.read(self._fd, 4096)
        except OSError:
            return None

    def read_feature(self, size, report_id=0x00):
        buf = bytearray(size)
        buf[0] = report_id
        try:
            fcntl.ioctl(self._fd, gfeature(size), buf)
        except OSError:
            return None
        return bytes(buf)


def query_feature(node, packet, parser, size=REPORT_SIZE, report_id=0x00, repeats=6):
    """Send a feature report and poll for its answer.

    ``parser`` receives the response and returns a value or ``None``.
    """
    try:
        with HidHandle(node.path) as hid:
            hid.flush()
            if not hid.send(packet):
                return None

            for _ in range(repeats):
                time.sleep(0.02)
                response = hid.read_feature(size, report_id)
                if response is None:
                    continue
                parsed = parser(response)
                if parsed:
                    return parsed
            return None
    except OSError:
        return None
