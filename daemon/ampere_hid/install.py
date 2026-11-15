# SPDX-License-Identifier: GPL-2.0-or-later
"""Install and remove the companion service for the current user.

``install`` puts the program into ``~/.local/bin`` and writes a systemd user
unit and a D-Bus activation file next to it. The udev rule needs root and is
only written on request. Nothing here needs PyGObject except the check that the
service will be able to start.
"""

import contextlib
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import NamedTuple

from . import __version__
from .archive import program_bytes, running_archive
from .names import BUS_NAME
from .udev import render_rules

APP = "ampere-hid"
UNIT_NAME = "ampere-hid.service"
RULES_PATH = "/etc/udev/rules.d/70-ampere-hid.rules"

UNIT_TEMPLATE = """\
# Written by `{app} install`; changes are overwritten by the next install.

[Unit]
Description=Ampere vendor HID battery daemon

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=5

# The daemon only reads /dev/hidraw nodes and answers D-Bus calls.
NoNewPrivileges=yes
ProtectSystem=strict
ProtectHome=read-only
PrivateTmp=yes
RestrictAddressFamilies=AF_UNIX

[Install]
WantedBy=default.target
"""

DBUS_TEMPLATE = """\
# Written by `{app} install`; changes are overwritten by the next install.

[D-BUS Service]
Name={bus_name}
Exec={exec_line}
SystemdService={unit}
"""

PYGOBJECT_HELP = """\
PyGObject (the "gi" module) is missing, so the service cannot start.
Install it first:
  Fedora:         sudo dnf install python3-gobject
  Debian/Ubuntu:  sudo apt install python3-gi
  Arch:           sudo pacman -S python-gobject
"""

RELOAD_UDEV = (
    "udevadm control --reload-rules"
    " && udevadm trigger --subsystem-match=hidraw"
)
INSTALL_RULES_SCRIPT = 'install -m644 "$1" "$2" && ' + RELOAD_UDEV
REMOVE_RULES_SCRIPT = 'rm -f "$1" && ' + RELOAD_UDEV


class Paths(NamedTuple):
    program: Path
    unit: Path
    dbus_service: Path
    legacy_libdir: Path


def paths(env=None):
    env = os.environ if env is None else env
    home = Path(env.get("HOME") or Path.home())
    config = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    data = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share")
    return Paths(
        program=home / ".local" / "bin" / APP,
        unit=config / "systemd" / "user" / UNIT_NAME,
        dbus_service=data / "dbus-1" / "services" / f"{BUS_NAME}.service",
        # Where the shell-script installer used to put the package.
        legacy_libdir=home / ".local" / "lib" / "ampere",
    )


# --- file contents ----------------------------------------------------------

_SAFE = re.compile(r"[A-Za-z0-9_@+=:,./-]+")


def systemd_quote(arg):
    """Quote one ExecStart= word; ``%`` and ``$`` would be expanded otherwise."""
    arg = arg.replace("%", "%%").replace("$", "$$")
    if _SAFE.fullmatch(arg):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_unit(python, program):
    words = [str(python), str(program), "run"]
    return UNIT_TEMPLATE.format(
        app=APP, exec_start=" ".join(systemd_quote(w) for w in words)
    )


def render_dbus_service(python, program):
    words = [str(python), str(program), "run"]
    return DBUS_TEMPLATE.format(
        app=APP,
        bus_name=BUS_NAME,
        exec_line=" ".join(shlex.quote(w) for w in words),
        unit=UNIT_NAME,
    )


# --- helpers ----------------------------------------------------------------


def default_run(cmd, quiet=False):
    kwargs = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL} if quiet else {}
    try:
        return subprocess.run(cmd, check=False, **kwargs).returncode
    except OSError:
        return 127


def write_if_changed(path, data, mode):
    """Write atomically. Returns ``True`` when the content or mode changed."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    with contextlib.suppress(OSError):
        if path.read_bytes() == data and (path.stat().st_mode & 0o777) == mode:
            return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.chmod(mode)
    os.replace(tmp, path)
    return True


def pygobject_missing():
    try:
        import gi

        gi.require_version("Gio", "2.0")
        gi.require_version("GLib", "2.0")
        from gi.repository import Gio, GLib  # noqa: F401
    except (ImportError, ValueError):
        return True
    return False


def rules_state(rules_path=RULES_PATH):
    """``missing``, ``outdated`` (differs from this version) or ``current``."""
    try:
        existing = Path(rules_path).read_text(encoding="utf-8")
    except OSError:
        return "missing"
    return "current" if existing == render_rules() else "outdated"


def privilege_prefix(interactive):
    """Command prefix to run something as root, ``[]`` if already root."""
    if os.geteuid() == 0:
        return []
    # sudo asks on the terminal; pkexec shows a dialog and works without one.
    order = ("sudo", "pkexec") if interactive else ("pkexec", "sudo")
    for tool in order:
        if shutil.which(tool):
            return [tool]
    return None


def manual_rules_hint(rules_path):
    return (
        "Install the udev rule yourself:\n"
        f"  {APP} rules | sudo tee {rules_path} >/dev/null\n"
        "  sudo udevadm control --reload-rules\n"
        "  sudo udevadm trigger --subsystem-match=hidraw"
    )


def install_rules(rules_path, run, interactive):
    prefix = privilege_prefix(interactive)
    if prefix is None:
        print("Neither sudo nor pkexec was found.\n" + manual_rules_hint(rules_path))
        return False
    fd, tmp = tempfile.mkstemp(prefix="ampere-hid-", suffix=".rules")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(render_rules())
        cmd = prefix + ["sh", "-c", INSTALL_RULES_SCRIPT, "sh", tmp, str(rules_path)]
        return run(cmd) == 0
    finally:
        Path(tmp).unlink()


def remove_rules(rules_path, run, interactive):
    prefix = privilege_prefix(interactive)
    if prefix is None:
        print(f"Neither sudo nor pkexec was found. Remove {rules_path} as root.")
        return False
    cmd = prefix + ["sh", "-c", REMOVE_RULES_SCRIPT, "sh", str(rules_path)]
    return run(cmd) == 0


def remove_legacy(legacy_libdir):
    """Remove what the old shell-script installer left behind."""
    removed = False
    package = legacy_libdir / "ampere_hid"
    launcher = legacy_libdir / APP
    if package.is_dir():
        shutil.rmtree(package)
        removed = True
    if launcher.exists():
        launcher.unlink()
        removed = True
    if removed:
        with contextlib.suppress(OSError):
            legacy_libdir.rmdir()
    return removed


def command_name(program):
    """How the user can call the program: the bare name only if it is on PATH."""
    found = shutil.which(APP)
    if found and Path(found).resolve() == program.resolve():
        return APP
    return shlex.quote(str(program))


# --- commands ---------------------------------------------------------------


def install(
    udev=None,
    env=None,
    run=default_run,
    interactive=None,
    rules_path=RULES_PATH,
    check_pygobject=pygobject_missing,
):
    """Install the service. ``udev``: True = install the rule, False = skip it,
    None = ask when a terminal is attached. Returns the exit status."""
    interactive = sys.stdin.isatty() if interactive is None else interactive
    target = paths(env)

    if check_pygobject():
        print(PYGOBJECT_HELP, file=sys.stderr, end="")
        return 1

    python = sys.executable or shutil.which("python3")
    if not python:
        print("Could not determine the Python interpreter.", file=sys.stderr)
        return 1

    state = rules_state(rules_path)
    if udev is None and state != "current" and interactive:
        what = "Install" if state == "missing" else "Update"
        answer = input(f"{what} the udev rule now (needs root)? [y/N] ").strip().lower()
        udev = answer in ("y", "yes")

    changed = False

    # The program. Skip the copy when this already is the installed file.
    running = running_archive()
    same = (
        running is not None
        and target.program.exists()
        and running.samefile(target.program)
    )
    if not same:
        changed |= write_if_changed(target.program, program_bytes(), 0o755)
    changed |= write_if_changed(target.unit, render_unit(python, target.program), 0o644)
    changed |= write_if_changed(
        target.dbus_service, render_dbus_service(python, target.program), 0o644
    )
    if remove_legacy(target.legacy_libdir):
        print(f"Removed the old installation in {target.legacy_libdir}.")
        changed = True

    if udev and state != "current" and not install_rules(rules_path, run, interactive):
        print("The udev rule was not installed.", file=sys.stderr)
    status = 0

    if shutil.which("systemctl"):
        run(["systemctl", "--user", "daemon-reload"], quiet=True)
        # The running session bus only learns about new activation files on reload.
        if run(["systemctl", "--user", "reload", "dbus-broker.service"], quiet=True):
            run(["systemctl", "--user", "reload", "dbus.service"], quiet=True)
        # Activation on demand is enough: no autostart at login. Older installs
        # may have enabled the unit, so drop that.
        run(["systemctl", "--user", "disable", UNIT_NAME], quiet=True)
        action = "restart" if changed else "start"
        if run(["systemctl", "--user", action, UNIT_NAME], quiet=True) != 0:
            print(
                "The service did not start. See: "
                f"journalctl --user -u {UNIT_NAME}",
                file=sys.stderr,
            )
            status = 1
    else:
        print(f"systemd not found; start the daemon manually:\n  {python} {target.program} run")

    name = command_name(target.program)
    print(
        f"Ampere vendor HID battery daemon {__version__} installed.\n"
        f"\n  {target.program}\n"
        "\nInspect devices and their answers:\n"
        f"  {name} probe      list HID devices and their driver\n"
        f"  {name} diagnose   query the battery, print the raw answers\n"
        f"  {name} sniff 3    dump input reports, no queries"
    )
    if rules_state(rules_path) != "current":
        print(
            "\nIf a device is not detected, install the udev rule once with\n"
            f"  {name} install --udev"
        )
    return status


def uninstall(
    udev=False,
    env=None,
    run=default_run,
    interactive=None,
    rules_path=RULES_PATH,
):
    interactive = sys.stdin.isatty() if interactive is None else interactive
    target = paths(env)

    if shutil.which("systemctl"):
        run(["systemctl", "--user", "disable", "--now", UNIT_NAME], quiet=True)

    for path in (target.unit, target.dbus_service, target.program):
        path.unlink(missing_ok=True)
    remove_legacy(target.legacy_libdir)

    if shutil.which("systemctl"):
        run(["systemctl", "--user", "daemon-reload"], quiet=True)
        # The running bus keeps the removed activation file until it is reloaded.
        if run(["systemctl", "--user", "reload", "dbus-broker.service"], quiet=True):
            run(["systemctl", "--user", "reload", "dbus.service"], quiet=True)

    print("Ampere vendor HID battery daemon removed.")
    if Path(rules_path).exists():
        if udev:
            if remove_rules(rules_path, run, interactive):
                print("The udev rule was removed.")
        else:
            print(
                f"The udev rule {rules_path} belongs to root and was kept;\n"
                f"remove it with `{APP} uninstall --udev` or as root."
            )
    return 0
