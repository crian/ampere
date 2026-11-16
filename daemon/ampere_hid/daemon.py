# SPDX-License-Identifier: GPL-2.0-or-later
"""Ampere vendor HID battery daemon.

Reads the battery of mice that UPower does not report and publishes it on the
session bus:

  Bus name:  org.gnome.shell.extensions.ampere.HidBattery
  Object:    /org/gnome/shell/extensions/ampere/HidBattery
  Interface: org.gnome.shell.extensions.ampere.HidBattery
  Method:    GetDevices() -> s   JSON: {"devices": [...]}
  Property:  Version -> s        the service's own version
  Signal:    Changed()

The extension never spawns this; D-Bus activation or the systemd user unit
starts it on demand.

Without D-Bus, see ``ampere-hid --help`` (once, probe, diagnose, sniff).
"""

import contextlib
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
from .hidraw import log, wait_for_activity  # noqa: E402
from .names import BUS_NAME, IFACE, OBJECT_PATH  # noqa: E402
from .sweep import read_devices, silent_paths  # noqa: E402
from .uevent import open_monitor, read_events  # noqa: E402

POLL_SECONDS = 30
# How long the worker waits for input from devices that answered nothing.
# A sleeping mouse sends nothing, so this uses no CPU.
ACTIVITY_WAIT_SECONDS = 25
UEVENT_RETRY_DELAYS_MS = (300, 1200)
GRACE_POLLS = 3
# D-Bus starts the service on demand, so it exits again after this long without
# a GetDevices call. 0 disables the exit.
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
    <property name="Version" type="s" access="read"/>
  </interface>
</node>
"""

class Daemon:
    """Serve cached readings over D-Bus; the sweep runs in a thread."""

    def __init__(self, loop):
        self._loop = loop
        self._connection = None
        self._payload = json.dumps({"devices": []}, separators=(",", ":"))
        self._last = {}
        self._misses = {}
        self._polling = False
        self._poll_again = False
        self._timer = 0
        self._monitor = None
        self._monitor_id = 0
        self._retry_id = 0
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        os.set_blocking(self._wake_w, False)
        self._last_call = time.monotonic()

    def _set_payload(self, items):
        payload = json.dumps({"devices": items}, separators=(",", ":"))
        if payload == self._payload:
            return
        self._payload = payload
        if self._connection is not None:
            self._connection.emit_signal(None, OBJECT_PATH, IFACE, "Changed", None)

    def _schedule(self):
        """Start the repeating poll timer."""
        if self._timer:
            GLib.source_remove(self._timer)
        self._timer = GLib.timeout_add_seconds(POLL_SECONDS, self._on_timeout)

    def _on_timeout(self):
        if IDLE_EXIT_SECONDS and time.monotonic() - self._last_call > IDLE_EXIT_SECONDS:
            log(f"no request for {IDLE_EXIT_SECONDS}s, exiting")
            self._timer = 0  # this source is being removed
            self._loop.quit()
            return GLib.SOURCE_REMOVE
        self.request_poll()
        return GLib.SOURCE_CONTINUE

    def request_poll(self):
        """Start a sweep in the background, unless one is already running."""
        if self._polling:
            self._poll_again = True
            self._wake()
            return
        self._polling = True
        threading.Thread(target=self._worker, daemon=True, name="ampere-hid-poll").start()

    def _wake(self):
        with contextlib.suppress(OSError):
            os.write(self._wake_w, b"x")

    def _drain_wake(self):
        with contextlib.suppress(OSError):  # BlockingIOError once it is empty
            while os.read(self._wake_r, 64):
                pass

    def _worker(self):
        self._drain_wake()
        try:
            matched, fresh = read_devices()
            # Nothing answered from these: wait for input instead of polling.
            paths = silent_paths(matched, fresh)
            if paths:
                # Do not hold back the devices that did answer: show them now.
                GLib.idle_add(self._publish_partial, matched, fresh)
                if wait_for_activity(paths, ACTIVITY_WAIT_SECONDS, self._wake_r):
                    matched, fresh = read_devices()
        except Exception as e:  # a broken driver must not kill the daemon
            log(f"poll failed: {e}")
            GLib.idle_add(self._apply_failed)
            return
        GLib.idle_add(self._apply, matched, fresh)

    def _publish_partial(self, matched, fresh):
        """Main loop: show a sweep's result before it waits for a silent device."""
        keys = [device.key for device in matched]
        self._set_payload(grace.peek(keys, fresh, self._last))
        return GLib.SOURCE_REMOVE

    def _apply_failed(self):
        self._polling = False
        if self._poll_again:
            self._poll_again = False
            self.request_poll()
        return GLib.SOURCE_REMOVE

    def _apply(self, matched, fresh):
        """Main loop: grace period, payload update and next sweep."""
        self._polling = False

        items = grace.apply(
            [device.key for device in matched], fresh, self._last, self._misses,
            GRACE_POLLS,
        )

        self._set_payload(items)
        if self._poll_again:
            self._poll_again = False
            self.request_poll()
        return GLib.SOURCE_REMOVE

    def _watch_uevents(self):
        """Read hidraw add/remove events instead of waiting for the timer."""
        try:
            self._monitor = open_monitor()
        except OSError as e:
            log(f"cannot watch udev events, polling only: {e}")
            return
        self._monitor_id = GLib.io_add_watch(
            self._monitor.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN, self._on_uevent
        )

    def _on_uevent(self, fd, condition):
        try:
            added, removed = read_events(self._monitor)
        except OSError as e:
            log(f"stopped watching udev events: {e}")
            self._monitor_id = 0
            self._monitor.close()
            self._monitor = None
            return GLib.SOURCE_REMOVE

        if removed:
            self.request_poll()
        if added:
            # udev applies the access rule after the kernel event, so the node
            # is not readable yet; look again shortly instead of after 30 s.
            self._retry_soon()
        return GLib.SOURCE_CONTINUE

    def _retry_soon(self, attempt=0):
        """Sweep again after a new device had time to become readable."""
        if self._retry_id or attempt >= len(UEVENT_RETRY_DELAYS_MS):
            return
        self._retry_id = GLib.timeout_add(
            UEVENT_RETRY_DELAYS_MS[attempt], self._on_retry, attempt
        )

    def _on_retry(self, attempt):
        self._retry_id = 0
        self.request_poll()
        self._retry_soon(attempt + 1)
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
            OBJECT_PATH,
            node.interfaces[0],
            self.on_method_call,
            self.on_get_property,
            None,
        )
        self._connection = connection
        # Answer immediately from the (empty) cache; the first sweep follows.
        self._last_call = time.monotonic()
        self._watch_uevents()
        self.request_poll()
        self._schedule()

    def on_get_property(self, connection, sender, path, iface, name):
        """The extension compares this with the version it needs."""
        if name == "Version":
            return GLib.Variant("s", __version__)
        return None

    def on_name_lost(self, connection, name):
        log(f"could not own bus name {name}")
        self._loop.quit()

    def close(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if self._retry_id:
            GLib.source_remove(self._retry_id)
            self._retry_id = 0
        if self._monitor_id:
            GLib.source_remove(self._monitor_id)
            self._monitor_id = 0
        if self._monitor is not None:
            with contextlib.suppress(OSError):
                self._monitor.close()
            self._monitor = None


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
    try:
        loop.run()
    finally:
        daemon.close()
    return 0
