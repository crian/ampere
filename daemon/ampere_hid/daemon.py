# SPDX-License-Identifier: GPL-2.0-or-later
"""Ampere vendor HID battery daemon.

Reads the battery of mice that UPower does not report and publishes it on the
session bus:

  Bus name:  org.gnome.shell.extensions.ampere.HidBattery
  Object:    /org/gnome/shell/extensions/ampere/HidBattery
  Interface: org.gnome.shell.extensions.ampere.HidBattery
  Method:    GetDevices() -> s   JSON: {"devices": [...]}
  Signal:    Changed()

The extension never spawns this; D-Bus activation or the systemd user unit
starts it on demand.

Without D-Bus, see ``ampere-hid --help`` (once, probe, diagnose, sniff).
"""

import json
import os
import signal
import threading
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

try:
    gi.require_version("GLibUnix", "2.0")
    from gi.repository import GLibUnix  # noqa: E402
except (ImportError, ValueError):
    GLibUnix = None

from . import __version__, grace  # noqa: E402
from .hidraw import can_access, log, wait_for_activity  # noqa: E402
from .names import BUS_NAME, IFACE, OBJECT_PATH  # noqa: E402
from .sweep import read_devices  # noqa: E402

POLL_SECONDS = 30
# While a supported receiver is present but does not answer (the mouse is
# asleep), the daemon waits for input reports instead of polling: a sleeping
# mouse sends nothing, so this blocks without using CPU and returns the instant
# the mouse is moved.
ACTIVITY_WAIT_SECONDS = 25
GRACE_POLLS = 3
# The service is started on demand over D-Bus, so it does not need to keep
# running once nothing asks for data. After this long without a GetDevices call
# it exits; the next call starts it again. 0 disables the exit.
DEFAULT_IDLE_EXIT_SECONDS = 300


def idle_exit_seconds():
    raw = os.environ.get("AMPERE_HID_IDLE_EXIT")
    if raw is None:
        return DEFAULT_IDLE_EXIT_SECONDS
    try:
        seconds = int(raw)
    except ValueError:
        log(f"AMPERE_HID_IDLE_EXIT={raw!r} is not a number, using {DEFAULT_IDLE_EXIT_SECONDS}")
        return DEFAULT_IDLE_EXIT_SECONDS
    if seconds < 0:
        # A negative timeout would end the service on its first timer.
        log(f"AMPERE_HID_IDLE_EXIT={raw!r} is negative, disabling the idle exit")
        return 0
    return seconds


IDLE_EXIT_SECONDS = idle_exit_seconds()

INTROSPECTION_XML = """
<node>
  <interface name="org.gnome.shell.extensions.ampere.HidBattery">
    <method name="GetDevices">
      <arg type="s" direction="out" name="devices"/>
    </method>
    <signal name="Changed"/>
  </interface>
</node>
"""


class Daemon:
    """Serves cached readings over D-Bus. The sweep runs in a thread.

    GetDevices must not wait for a device query. A sweep takes a few hundred
    milliseconds with an awake mouse and several seconds with sleeping ones,
    which delays the panel icon after login and can hit the caller's timeout.
    """

    def __init__(self, loop):
        self._loop = loop
        self._connection = None
        self._payload = json.dumps({"devices": []}, separators=(",", ":"))
        self._last = {}
        self._misses = {}
        self._polling = False
        self._timer = 0
        self._last_call = time.monotonic()

    def _set_payload(self, items):
        payload = json.dumps({"devices": items}, separators=(",", ":"))
        if payload == self._payload:
            return
        self._payload = payload
        if self._connection is not None:
            self._connection.emit_signal(None, OBJECT_PATH, IFACE, "Changed", None)

    def _schedule(self):
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add_seconds(POLL_SECONDS, self._on_timeout)

    def _on_timeout(self):
        self._timer = 0
        if IDLE_EXIT_SECONDS and time.monotonic() - self._last_call > IDLE_EXIT_SECONDS:
            log(f"no request for {IDLE_EXIT_SECONDS}s, exiting")
            self._loop.quit()
            return GLib.SOURCE_REMOVE
        self.request_poll()
        return GLib.SOURCE_REMOVE

    def request_poll(self):
        """Start a sweep in the background, unless one is already running."""
        if self._polling:
            return
        self._polling = True
        threading.Thread(target=self._worker, daemon=True, name="ampere-hid-poll").start()

    def _worker(self):
        try:
            matched, fresh = read_devices()
            if matched and not fresh:
                # The receiver answered nothing: the mouse is asleep. Wait for
                # input instead of polling, then read again right away.
                paths = [
                    node.path
                    for device in matched
                    for node in device.nodes
                    if can_access(node)
                ]
                if wait_for_activity(paths, ACTIVITY_WAIT_SECONDS):
                    matched, fresh = read_devices()
        except Exception as e:  # a broken driver must not kill the daemon
            log(f"poll failed: {e}")
            GLib.idle_add(self._apply_failed)
            return
        GLib.idle_add(self._apply, matched, fresh)

    def _apply_failed(self):
        self._polling = False
        self._schedule()
        return GLib.SOURCE_REMOVE

    def _apply(self, matched, fresh):
        """Main loop: grace period, payload update and next sweep."""
        self._polling = False

        items = grace.apply(
            [device.key for device in matched], fresh, self._last, self._misses,
            GRACE_POLLS,
        )

        self._set_payload(items)
        self._schedule()
        return GLib.SOURCE_REMOVE

    def on_method_call(self, connection, sender, path, iface, method, params, invocation):
        if method == "GetDevices":
            self._last_call = time.monotonic()
            invocation.return_value(GLib.Variant("(s)", (self._payload,)))
            return
        invocation.return_dbus_error(
            "org.freedesktop.DBus.Error.UnknownMethod", f"Unknown method {method}"
        )

    def on_bus_acquired(self, connection, name):
        node = Gio.DBusNodeInfo.new_for_xml(INTROSPECTION_XML)
        connection.register_object(
            OBJECT_PATH, node.interfaces[0], self.on_method_call, None, None
        )
        self._connection = connection
        # Answer immediately from the (empty) cache; the first sweep follows.
        self._last_call = time.monotonic()
        self.request_poll()

    def on_name_lost(self, connection, name):
        log(f"could not own bus name {name}")
        self._loop.quit()


def quit_handler(loop):
    def handler():
        loop.quit()
        return GLib.SOURCE_REMOVE
    return handler


def add_unix_signal(signum, handler):
    # GLibUnix.signal_add replaces the deprecated GLib.unix_signal_add, but
    # older GLib versions ship GLibUnix without it.
    if GLibUnix is not None and hasattr(GLibUnix, "signal_add"):
        return GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signum, handler)
    return GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, handler)


def run():
    """Own the bus name and serve until asked to quit or idle."""
    log(f"{__version__} starting")
    loop = GLib.MainLoop()
    daemon = Daemon(loop)
    Gio.bus_own_name(
        Gio.BusType.SESSION,
        BUS_NAME,
        Gio.BusNameOwnerFlags.NONE,
        daemon.on_bus_acquired,
        None,
        daemon.on_name_lost,
    )
    for signum in (signal.SIGINT, signal.SIGTERM):
        add_unix_signal(signum, quit_handler(loop))
    loop.run()
    return 0
