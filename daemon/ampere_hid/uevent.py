# SPDX-License-Identifier: GPL-2.0-or-later
"""Watch kernel uevents so device changes do not wait for the next poll."""

import os
import socket

# Linux: NETLINK_KOBJECT_UEVENT, group 1 carries the kernel's own events.
NETLINK_KOBJECT_UEVENT = 15
KERNEL_GROUP = 1


def open_monitor():
    """Return a non-blocking socket that receives kernel uevents."""
    sock = socket.socket(socket.AF_NETLINK, socket.SOCK_DGRAM, NETLINK_KOBJECT_UEVENT)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
        sock.bind((os.getpid(), KERNEL_GROUP))
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
    return sock


def mentions_hidraw(message):
    """True when a uevent payload is about a hidraw device."""
    return b"SUBSYSTEM=hidraw" in message
