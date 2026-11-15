# SPDX-License-Identifier: GPL-2.0-or-later
"""Start the real daemon on a private session bus and ask it for its devices.

Needs PyGObject and ``dbus-run-session``; skipped where either is missing. It
covers what the unit tests cannot: the service owns its bus name, answers
``GetDevices`` and ends cleanly on SIGTERM. It runs the package and the built
single-file program.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ampere_hid import archive, install  # noqa: E402
from ampere_hid.names import BUS_NAME, IFACE, OBJECT_PATH  # noqa: E402

DAEMON_DIR = pathlib.Path(__file__).resolve().parent.parent

# Runs inside `dbus-run-session`: start the daemon, query it, stop it.
CLIENT = r"""
import subprocess
import sys
import time

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib

name, path, iface, cwd, *command = sys.argv[1:]
proc = subprocess.Popen(command, cwd=cwd)
payload = None
try:
    conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    deadline = time.time() + 20
    while payload is None and time.time() < deadline:
        if proc.poll() is not None:
            sys.exit("daemon exited early with code %s" % proc.returncode)
        try:
            reply = conn.call_sync(
                name, path, iface, "GetDevices", None, GLib.VariantType("(s)"),
                Gio.DBusCallFlags.NONE, 2000, None,
            )
            payload = reply.unpack()[0]
        except GLib.Error:
            time.sleep(0.2)
finally:
    proc.terminate()
    try:
        code = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        code = "timeout"
if payload is None:
    sys.exit("the daemon never answered GetDevices")
if code != 0:
    sys.exit("daemon ended with %s after SIGTERM" % code)
print(payload)
"""


@unittest.skipUnless(shutil.which("dbus-run-session"), "dbus-run-session not found")
@unittest.skipIf(install.pygobject_missing(), "PyGObject not available")
class DaemonOnSessionBusTests(unittest.TestCase):
    def ask(self, *command):
        # The private bus reads the standard service directories, so an
        # installed activation file would start a second daemon and steal the
        # name. Point XDG_DATA_HOME at an empty directory to keep it out.
        with tempfile.TemporaryDirectory() as data_home:
            env = {**os.environ, "XDG_DATA_HOME": data_home}
            result = subprocess.run(
                [
                    "dbus-run-session", "--", sys.executable, "-c", CLIENT,
                    BUS_NAME, OBJECT_PATH, IFACE, str(DAEMON_DIR), *command,
                ],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
                env=env,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout.strip().splitlines()[-1])

    def test_package_answers_and_exits_cleanly(self):
        payload = self.ask(sys.executable, "-m", "ampere_hid", "run")
        self.assertIsInstance(payload["devices"], list)

    def test_built_program_answers_and_exits_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            program = pathlib.Path(tmp) / "ampere-hid"
            program.write_bytes(archive.build_bytes())
            program.chmod(0o755)
            payload = self.ask(sys.executable, str(program), "run")
        self.assertIsInstance(payload["devices"], list)


if __name__ == "__main__":
    unittest.main()
