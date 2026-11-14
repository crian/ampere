# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for the sweep: a failing device must not hide the others."""

import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ampere_hid import sweep  # noqa: E402
from ampere_hid.hidraw import HidDevice, HidNode  # noqa: E402


def make_device(name):
    node = HidNode(f"/dev/hidraw-{name}", 0x373E, 0x001E, name, name, 2, "usb-1/input2")
    device = HidDevice(node.key, name)
    device.add_node(node)
    return device


class BrokenDriver:
    kind = "mouse"

    def read(self, device):
        raise OSError(19, "No such device")


class SilentDriver:
    kind = "mouse"

    def read(self, device):
        return None


class WorkingDriver:
    kind = "mouse"

    def read(self, device):
        return 55, 0


class SweepTests(unittest.TestCase):
    def setUp(self):
        self.broken = make_device("broken")
        self.silent = make_device("silent")
        self.working = make_device("working")

    def run_sweep(self, drivers):
        devices = self.broken.nodes + self.silent.nodes + self.working.nodes
        with mock.patch.object(sweep, "enumerate_nodes", lambda: devices), \
                mock.patch.object(sweep, "find_driver",
                                  lambda device: drivers.get(device.key)), \
                mock.patch.object(sweep, "log", lambda *_: None):
            return sweep.read_devices()

    def test_a_failing_device_keeps_the_others(self):
        drivers = {
            self.broken.key: BrokenDriver(),
            self.working.key: WorkingDriver(),
        }

        matched, fresh = self.run_sweep(drivers)

        self.assertEqual([device.key for device in matched],
                         [self.broken.key, self.working.key])
        self.assertEqual(list(fresh), [self.working.key])
        self.assertEqual(fresh[self.working.key]["pct"], 55)

    def test_a_silent_device_is_reported_without_a_reading(self):
        matched, fresh = self.run_sweep({
            self.silent.key: SilentDriver(),
            self.working.key: WorkingDriver(),
        })

        self.assertEqual([device.key for device in matched],
                         [self.silent.key, self.working.key])
        self.assertEqual(list(fresh), [self.working.key])

    def test_devices_without_a_driver_are_ignored(self):
        matched, fresh = self.run_sweep({self.working.key: WorkingDriver()})

        self.assertEqual([device.key for device in matched], [self.working.key])
        self.assertEqual(list(fresh), [self.working.key])

    def test_reading_carries_the_driver_kind(self):
        _, fresh = self.run_sweep({self.working.key: WorkingDriver()})

        self.assertEqual(fresh[self.working.key]["kind"], "mouse")
        self.assertEqual(fresh[self.working.key]["state"], 0)


if __name__ == "__main__":
    unittest.main()
