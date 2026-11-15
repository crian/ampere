# SPDX-License-Identifier: GPL-2.0-or-later
"""One screen with the things that decide whether Ampere works."""

import json
import os
import sys

from . import __version__, install
from .names import BUS_NAME, IFACE, OBJECT_PATH

STATE_NAMES = {0: "discharging", 1: "charging", 2: "full"}


def program_path():
    """The file that is running, so a stale copy becomes visible."""
    return os.path.abspath(sys.argv[0])


def service_state():
    """``active (running)`` and the like, or ``None`` without PyGObject."""
    try:
        from gi.repository import Gio, GLib
    except (ImportError, ValueError):
        return None
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        path = bus.call_sync(
            "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager",
            "GetUnit",
            GLib.Variant("(s)", [install.UNIT_NAME]),
            GLib.VariantType("(o)"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        ).unpack()[0]
        props = bus.call_sync(
            "org.freedesktop.systemd1",
            path,
            "org.freedesktop.DBus.Properties",
            "GetAll",
            GLib.Variant("(s)", ["org.freedesktop.systemd1.Unit"]),
            GLib.VariantType("(a{sv})"),
            Gio.DBusCallFlags.NONE,
            2000,
            None,
        ).unpack()[0]
        return f"{props['ActiveState']} ({props['SubState']})"
    except GLib.Error as e:
        return "not installed" if "NoSuchUnit" in e.message else "unknown"
    except (AttributeError, KeyError):
        return "unknown"


def daemon_devices():
    """The service's device list, or ``None`` when it cannot be asked."""
    try:
        from gi.repository import Gio, GLib
    except (ImportError, ValueError):
        return None
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        reply = bus.call_sync(
            BUS_NAME,
            OBJECT_PATH,
            IFACE,
            "GetDevices",
            None,
            GLib.VariantType("(s)"),
            Gio.DBusCallFlags.NONE,
            8000,
            None,
        )
        return json.loads(reply.unpack()[0])["devices"]
    except (GLib.Error, KeyError, ValueError):
        return None


def render(version, program, service, rule, rule_path, devices):
    """Format the summary; kept pure so it can be tested without a bus."""
    lines = [
        f"version     {version}",
        f"program     {program}",
        f"service     {service or 'not available (PyGObject missing)'}",
        f"udev rule   {rule}  ({rule_path})",
    ]
    if devices is None:
        lines.append("devices     not available")
    elif not devices:
        lines.append("devices     none")
    else:
        lines.append(f"devices     {len(devices)}")
        for device in devices:
            state = STATE_NAMES.get(device["state"], "unknown")
            lines.append(
                f"            {device['name']:<38} {device['pct']:3} %  {state}"
            )
    return "\n".join(lines)


def status_text():
    return render(
        version=__version__,
        program=program_path(),
        service=service_state(),
        rule=install.rules_state(),
        rule_path=install.RULES_PATH,
        devices=daemon_devices(),
    )
