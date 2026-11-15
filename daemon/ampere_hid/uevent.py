# SPDX-License-Identifier: GPL-2.0-or-later
"""Watch kernel uevents so device changes do not wait for the next poll."""

import errno
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


def read_events(sock):
    """Drain ``sock`` and report whether hidraw nodes were added or removed.

    Returns ``(added, removed)``. When the kernel dropped events because the
    receive buffer overflowed (``ENOBUFS``), what happened is unknown, so both
    are reported and the caller looks again. Any other socket error is raised.
    """
    added = removed = False
    while True:
        try:
            message = sock.recv(8192)
        except BlockingIOError:
            break
        except OSError as e:
            if e.errno == errno.ENOBUFS:
                return True, True
            raise
        if not mentions_hidraw(message):
            continue
        added = added or b"ACTION=add" in message
        removed = removed or b"ACTION=remove" in message
    return added, removed
