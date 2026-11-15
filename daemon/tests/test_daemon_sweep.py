# SPDX-License-Identifier: GPL-2.0-or-later
"""The daemon's worker with fake devices: no bus, no hardware.

Needs PyGObject; skipped without it. A device that answers must not wait for one
that is asleep, and a new request must end the wait for the sleeping one.
"""

import json
import os
import pathlib
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ampere_hid import sweep  # noqa: E402
from ampere_hid.hidraw import HidDevice, HidNode  # noqa: E402

try:
    from ampere_hid import daemon
    from gi.repository import GLib
except (ImportError, ValueError):
    daemon = None


def fifo_device(directory, key):
    """A device whose only node never reports input, like a sleeping mouse."""
    path = os.path.join(directory, key)
    os.mkfifo(path)
    device = HidDevice(key, key)
    device.add_node(HidNode(path, 0x3837, 0x100A, key, key, 2, ""))
    return device


@unittest.skipIf(daemon is None, "PyGObject not available")
class WorkerTests(unittest.TestCase):
    AWAKE = {"key": "awake", "name": "Awake", "kind": "mouse", "pct": 80, "state": 0}

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.awake = fifo_device(tmp.name, "awake")
        self.asleep = fifo_device(tmp.name, "asleep")
        self.reads = []

        def fake_read_devices():
            self.reads.append(time.monotonic())
            return [self.awake, self.asleep], {"awake": dict(self.AWAKE)}

        for patcher in (
            mock.patch.object(daemon, "read_devices", fake_read_devices),
            mock.patch.object(sweep, "can_access", return_value=True),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

        self.daemon = daemon.Daemon(GLib.MainLoop())
        # Ends the worker's wait, so no thread outlives the test by 25 s.
        self.addCleanup(self.daemon._wake)

    def run_until(self, done, timeout=5.0):
        context = GLib.MainContext.default()
        deadline = time.monotonic() + timeout
        while not done() and time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.01)
        return done()

    def devices(self):
        return json.loads(self.daemon._payload)["devices"]

    def test_an_awake_device_is_shown_while_another_one_sleeps(self):
        start = time.monotonic()
        self.daemon.request_poll()
        self.assertTrue(self.run_until(self.devices))
        self.assertLess(time.monotonic() - start, 2.0)
        self.assertEqual(self.devices()[0]["pct"], 80)

    def test_a_new_request_ends_the_wait_for_the_sleeping_device(self):
        self.daemon.request_poll()
        self.assertTrue(self.run_until(lambda: len(self.reads) >= 1))
        self.daemon.request_poll()  # e.g. a plug-in event while one mouse sleeps
        self.assertTrue(self.run_until(lambda: len(self.reads) >= 2))
        self.assertLess(self.reads[1] - self.reads[0], 2.0)


if __name__ == "__main__":
    unittest.main()
