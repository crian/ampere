# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for device keys, driver matching and the grace period.

These do not need hardware or PyGObject.
"""

import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ampere_hid import grace  # noqa: E402
from ampere_hid.drivers import find_driver, matching_drivers  # noqa: E402
from ampere_hid.hidraw import HidDevice, HidNode  # noqa: E402


def make_device(vendor, product, uniq="", phys=""):
    node = HidNode("/dev/hidraw-test", vendor, product, "test device", uniq, 2, phys)
    device = HidDevice(node.key, "test device")
    device.add_node(node)
    return device


class DriverSelectionTests(unittest.TestCase):
    def test_lamzu_receivers(self):
        for vendor in (0x373E, 0x3554, 0x37B0):
            with self.subTest(vendor=hex(vendor)):
                self.assertEqual(find_driver(make_device(vendor, 0x001E)).name, "Lamzu")

    def test_compx_receivers(self):
        for vendor in (0x373B, 0x260D, 0x33E4):
            with self.subTest(vendor=hex(vendor)):
                self.assertEqual(find_driver(make_device(vendor, 0x1020)).name, "Compx")

    def test_mchose_receivers(self):
        for vendor in (0x3837, 0x5253):
            with self.subTest(vendor=hex(vendor)):
                self.assertEqual(find_driver(make_device(vendor, 0x100A)).name, "MCHOSE")

    def test_unknown_vendor_has_no_driver(self):
        for vendor in (0x1532, 0x046D, 0x31E3):
            with self.subTest(vendor=hex(vendor)):
                self.assertIsNone(find_driver(make_device(vendor, 0x0001)))

    def test_lamzu_owns_the_shared_vendor(self):
        # 0x3554 is Compx silicon; the Lamzu driver claims it because it speaks
        # both transports itself, so the plain Compx driver must not compete.
        device = make_device(0x3554, 0xF50D)
        self.assertEqual([driver.name for driver in matching_drivers(device)], ["Lamzu"])


class DeviceKeyTests(unittest.TestCase):
    def test_serial_is_used_when_present(self):
        node = HidNode("/dev/hidraw1", 0x373E, 0x001E, "x", "SERIAL123", 2, "usb-1/input2")
        self.assertEqual(node.key, "SERIAL123")

    def test_identical_receivers_do_not_share_a_key(self):
        first = HidNode("/dev/hidraw1", 0x373E, 0x001E, "x", "", 2, "usb-0000:00:14.0-3/input2")
        second = HidNode("/dev/hidraw2", 0x373E, 0x001E, "x", "", 2, "usb-0000:00:14.0-4/input2")
        self.assertNotEqual(first.key, second.key)

    def test_nodes_of_one_receiver_share_a_key(self):
        control = HidNode("/dev/hidraw1", 0x373E, 0x001E, "x", "", 2, "usb-0000:00:14.0-3/input2")
        other = HidNode("/dev/hidraw2", 0x373E, 0x001E, "x", "", 0, "usb-0000:00:14.0-3/input0")
        self.assertEqual(control.key, other.key)

    def test_key_falls_back_to_the_path(self):
        node = HidNode("/dev/hidraw9", 0x1234, 0x5678, "x", "", -1, "")
        self.assertEqual(node.key, "1234:5678:/dev/hidraw9")


class GraceTests(unittest.TestCase):
    def setUp(self):
        self.last = {}
        self.misses = {}

    def apply(self, matched, fresh, grace_polls=3):
        return grace.apply(matched, fresh, self.last, self.misses, grace_polls)

    def test_reading_is_published(self):
        self.assertEqual(self.apply(["a"], {"a": {"pct": 50}}), [{"pct": 50}])
        self.assertEqual(self.misses["a"], 0)

    def test_last_value_is_kept_for_the_grace_period(self):
        self.apply(["a"], {"a": {"pct": 50}})
        for miss in (1, 2):
            with self.subTest(miss=miss):
                self.assertEqual(self.apply(["a"], {}), [{"pct": 50}], f"miss {miss}")

    def test_device_disappears_after_the_grace_period(self):
        self.apply(["a"], {"a": {"pct": 50}})
        self.apply(["a"], {})
        self.apply(["a"], {})
        self.assertEqual(self.apply(["a"], {}), [])
        self.assertNotIn("a", self.last)

    def test_new_reading_resets_the_misses(self):
        self.apply(["a"], {"a": {"pct": 50}})
        self.apply(["a"], {})
        self.assertEqual(self.apply(["a"], {"a": {"pct": 49}}), [{"pct": 49}])
        self.assertEqual(self.misses["a"], 0)

    def test_removed_device_forgets_its_state(self):
        self.apply(["a"], {"a": {"pct": 50}})
        self.apply([], {})
        self.assertNotIn("a", self.last)
        self.assertNotIn("a", self.misses)

    def test_order_follows_the_matched_devices(self):
        fresh = {"a": {"pct": 1}, "b": {"pct": 2}}
        self.assertEqual(self.apply(["b", "a"], fresh), [{"pct": 2}, {"pct": 1}])


if __name__ == "__main__":
    unittest.main()
