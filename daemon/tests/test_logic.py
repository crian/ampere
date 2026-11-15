# SPDX-License-Identifier: GPL-2.0-or-later
"""Tests for keys, drivers, grace, uevent, status and the backoff.

These do not need hardware or PyGObject.
"""

import errno
import pathlib
import socket
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ampere_hid import grace, status, sweep, uevent  # noqa: E402
from ampere_hid.drivers import compx, find_driver, lamzu, matching_drivers  # noqa: E402
from ampere_hid.hidraw import HidDevice, HidNode, access_help  # noqa: E402


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


class CompxBackoffTests(unittest.TestCase):
    """A receiver without a mouse must not cost a full probe every sweep."""

    def setUp(self):
        self.device = HidDevice("compx-test", "test device")
        for interface in (0, 1, 2):
            self.device.add_node(
                HidNode(
                    f"/dev/hidraw-test{interface}",
                    0x373B,
                    0x11FE,
                    "test device",
                    "",
                    interface,
                )
            )
        self.driver = compx.CompxDriver()
        self.calls = []
        self.result = None

    def read_node(self, node, timeout=compx.TIMEOUT):
        self.calls.append((node.path, timeout))
        return self.result

    def patch(self):
        return mock.patch.multiple(
            compx,
            can_access=mock.Mock(return_value=True),
            read_node=mock.Mock(side_effect=self.read_node),
        )

    def test_two_full_sweeps_then_a_cheap_one(self):
        with self.patch():
            for _ in range(compx.FAILURES_BEFORE_FAST):
                self.assertIsNone(self.driver.read(self.device))
            self.assertEqual(len(self.calls), 6)
            self.calls.clear()
            self.assertIsNone(self.driver.read(self.device))
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(all(call[1] == compx.FAST_TIMEOUT for call in self.calls))

    def test_a_full_probe_comes_back(self):
        with self.patch():
            for _ in range(compx.FULL_PROBE_EVERY):
                self.driver.read(self.device)
            self.calls.clear()
            self.driver.read(self.device)
        self.assertEqual(len(self.calls), 3)
        self.assertTrue(all(call[1] == compx.TIMEOUT for call in self.calls))

    def test_remembers_the_node_that_answered(self):
        def answer_on_one(node, timeout=compx.TIMEOUT):
            self.calls.append((node.path, timeout))
            return (50, 0) if node.path.endswith("1") else None

        with self.patch(), mock.patch.object(compx, "read_node", answer_on_one):
            self.assertEqual(self.driver.read(self.device), (50, 0))
            self.assertEqual([call[0] for call in self.calls][:2],
                             ["/dev/hidraw-test0", "/dev/hidraw-test1"])
            self.calls.clear()
            self.assertEqual(self.driver.read(self.device), (50, 0))
        self.assertEqual([call[0] for call in self.calls], ["/dev/hidraw-test1"])

    def test_a_reading_resets_the_failures(self):
        with self.patch():
            for _ in range(compx.FAILURES_BEFORE_FAST):
                self.driver.read(self.device)
            self.result = (50, 0)
            self.calls.clear()
            self.assertEqual(self.driver.read(self.device), (50, 0))
            self.assertEqual(len(self.calls), 1)
            self.assertEqual(self.calls[0][1], compx.FAST_TIMEOUT)

            self.result = None
            self.calls.clear()
            self.driver.read(self.device)
        self.assertEqual(len(self.calls), 3)  # failures were reset


class LamzuOfflineTests(unittest.TestCase):
    """An 0xA0 answer means the mouse is off, but only for firmware that
    proved it speaks the new protocol."""

    ONLINE = bytes([0xA1, 0x00, 0x02, 0x02, 0x00, 0x83, 0x00, 0x64])
    OFFLINE = bytes([0xA0, 0x00, 0x02, 0x02, 0x00, 0x83, 0x00, 0x00])

    def setUp(self):
        self.node = HidNode("/dev/hidraw-test", 0x373E, 0x001E, "x", "", 2)

    def patch(self, answer):
        self.legacy = mock.Mock(return_value=(50, 0))
        return mock.patch.multiple(
            lamzu,
            aurora_query=mock.Mock(return_value=answer),
            query_feature=self.legacy,
        )

    def test_online_answer_is_used(self):
        with self.patch(self.ONLINE):
            battery, online = lamzu.aurora_read_node(self.node)
        self.assertEqual(battery, (100, 0))
        self.assertTrue(online)
        self.assertFalse(self.legacy.called)

    def test_offline_falls_back_until_the_device_is_known(self):
        with self.patch(self.OFFLINE):
            battery, online = lamzu.aurora_read_node(self.node)
        self.assertEqual(battery, (50, 0))
        self.assertFalse(online)
        self.assertTrue(self.legacy.called)

    def test_offline_is_final_when_the_device_is_known(self):
        with self.patch(self.OFFLINE):
            battery, online = lamzu.aurora_read_node(self.node, trust_offline=True)
        self.assertIsNone(battery)
        self.assertFalse(online)
        self.assertFalse(self.legacy.called)

    def test_the_driver_learns_the_new_protocol(self):
        device = make_device(0x373E, 0x001E)
        driver = lamzu.LamzuDriver()
        answers = [((80, 0), True), (None, False)]
        with mock.patch.object(lamzu, "can_access", return_value=True), \
                mock.patch.object(compx, "read_node", return_value=None), \
                mock.patch.object(lamzu, "aurora_read_node",
                                  side_effect=answers) as aurora:
            self.assertEqual(driver.read(device), (80, 0))
            self.assertIsNone(driver.read(device))
        aurora.assert_called_with(device.nodes[0], True, False)


class SilentPathsTests(unittest.TestCase):
    def test_only_devices_without_a_reading_are_watched(self):
        awake = HidDevice("awake", "awake")
        awake.add_node(HidNode("/dev/hidraw-awake", 0x373E, 0x001E, "a", "", 0))
        asleep = HidDevice("asleep", "asleep")
        asleep.add_node(HidNode("/dev/hidraw-asleep", 0x373B, 0x11FE, "b", "", 0))
        with mock.patch.object(sweep, "can_access", return_value=True):
            paths = sweep.silent_paths([awake, asleep], {"awake": {}})
        self.assertEqual(paths, ["/dev/hidraw-asleep"])

    def test_nothing_to_watch_when_every_device_answers(self):
        device = HidDevice("awake", "awake")
        device.add_node(HidNode("/dev/hidraw-awake", 0x373E, 0x001E, "a", "", 0))
        with mock.patch.object(sweep, "can_access", return_value=True):
            self.assertEqual(sweep.silent_paths([device], {"awake": {}}), [])

    def test_nodes_without_access_are_skipped(self):
        device = HidDevice("asleep", "asleep")
        device.add_node(HidNode("/dev/hidraw-asleep", 0x373B, 0x11FE, "b", "", 0))
        with mock.patch.object(sweep, "can_access", return_value=False):
            self.assertEqual(sweep.silent_paths([device], {}), [])


class AccessHelpTests(unittest.TestCase):
    def test_a_listed_product_points_at_the_installer(self):
        node = HidNode("/dev/hidraw-test", 0x373B, 0x11FE, "x", "", 0)
        reason, hint = access_help(node)
        self.assertIn("missing or outdated", reason)
        self.assertIn("install --udev", hint)

    def test_an_unknown_product_says_the_table_is_missing(self):
        node = HidNode("/dev/hidraw-test", 0x373B, 0x1271, "x", "", 0)
        reason, hint = access_help(node)
        self.assertIn("373b:1271 is not in the udev table", reason)
        self.assertIn("udev.py", hint)


class LamzuBackoffTests(unittest.TestCase):
    """A silent Lamzu receiver must not cost a full probe every sweep."""

    def setUp(self):
        self.device = HidDevice("lamzu-test", "test device")
        for interface in (0, 1, 2):
            self.device.add_node(
                HidNode(
                    f"/dev/hidraw-test{interface}",
                    0x373E,
                    0x001E,
                    "test device",
                    "",
                    interface,
                )
            )
        self.driver = lamzu.LamzuDriver()
        self.compx_calls = []
        self.aurora_calls = []

    def read_compx(self, node, timeout=compx.TIMEOUT):
        self.compx_calls.append(timeout)
        return None

    def read_aurora(self, node, trust_offline=False, quick=False):
        self.aurora_calls.append(quick)
        return None, False

    def patch(self):
        return (
            mock.patch.object(lamzu, "can_access", return_value=True),
            mock.patch.object(compx, "read_node", side_effect=self.read_compx),
            mock.patch.object(
                lamzu, "aurora_read_node", side_effect=self.read_aurora
            ),
        )

    def test_two_full_sweeps_then_short_ones(self):
        patches = self.patch()
        with patches[0], patches[1], patches[2]:
            for _ in range(lamzu.FAILURES_BEFORE_FAST):
                self.assertIsNone(self.driver.read(self.device))
            self.assertTrue(all(t == compx.TIMEOUT for t in self.compx_calls))
            self.assertFalse(any(self.aurora_calls))
            self.compx_calls.clear()
            self.aurora_calls.clear()
            self.assertIsNone(self.driver.read(self.device))
        self.assertEqual(len(self.compx_calls), 3)
        self.assertTrue(all(t == lamzu.FAST_TIMEOUT for t in self.compx_calls))
        self.assertEqual(self.aurora_calls, [True, True, True])

    def test_a_full_probe_comes_back(self):
        patches = self.patch()
        with patches[0], patches[1], patches[2]:
            for _ in range(lamzu.FULL_PROBE_EVERY):
                self.driver.read(self.device)
            self.compx_calls.clear()
            self.aurora_calls.clear()
            self.driver.read(self.device)
        self.assertTrue(all(t == compx.TIMEOUT for t in self.compx_calls))
        self.assertFalse(any(self.aurora_calls))


class StatusRenderTests(unittest.TestCase):
    def render(self, **kwargs):
        values = {
            "version": "1.1",
            "program": "/home/u/.local/bin/ampere-hid",
            "service": "active (running)",
            "rule": "current",
            "rule_path": "/etc/udev/rules.d/70-ampere-hid.rules",
            "devices": [],
        }
        values.update(kwargs)
        return status.render(**values)

    def test_lists_devices_with_their_state(self):
        text = self.render(devices=[{"name": "Maya X", "pct": 100, "state": 0}])
        self.assertIn("service     active (running)", text)
        self.assertIn("udev rule   current", text)
        self.assertIn("Maya X", text)
        self.assertIn("100 %  discharging", text)

    def test_says_when_there_is_nothing_to_show(self):
        self.assertIn("devices     none", self.render())
        self.assertIn("devices     not available", self.render(devices=None))

    def test_names_a_missing_pygobject(self):
        self.assertIn(
            "not available (PyGObject missing)", self.render(service=None)
        )


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

    def test_peek_shows_fresh_and_remembered_readings_without_counting(self):
        self.apply(["a", "b"], {"a": {"pct": 80}, "b": {"pct": 50}})
        last, misses = dict(self.last), dict(self.misses)
        items = grace.peek(["a", "b", "c"], {"a": {"pct": 79}}, self.last)
        self.assertEqual(items, [{"pct": 79}, {"pct": 50}])  # c was never seen
        self.assertEqual((self.last, self.misses), (last, misses))

    def test_peek_leaves_out_devices_that_are_gone(self):
        self.apply(["a"], {"a": {"pct": 80}})
        self.assertEqual(grace.peek([], {}, self.last), [])

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


class UeventTests(unittest.TestCase):
    def test_detects_hidraw_events(self):
        add = b"add@/devices/.../hidraw/hidraw3\x00SUBSYSTEM=hidraw\x00ACTION=add\x00"
        self.assertTrue(uevent.mentions_hidraw(add))
        self.assertFalse(
            uevent.mentions_hidraw(b"add@/devices/.../usb1\x00SUBSYSTEM=usb\x00")
        )

    def events(self, *messages):
        """A socket pair standing in for the kernel's uevent socket."""
        receiver, sender = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(receiver.close)
        self.addCleanup(sender.close)
        receiver.setblocking(False)
        for message in messages:
            sender.send(message)
        return receiver

    ADD = b"add@/devices/x/hidraw/hidraw%d\x00ACTION=add\x00SUBSYSTEM=hidraw\x00"
    REMOVE = b"remove@/devices/x/hidraw/hidraw3\x00ACTION=remove\x00SUBSYSTEM=hidraw\x00"
    OTHER = b"add@/devices/x/usb1\x00ACTION=add\x00SUBSYSTEM=usb\x00"

    def test_a_burst_of_adds_is_one_added(self):
        sock = self.events(self.ADD % 3, self.OTHER, self.ADD % 4, self.ADD % 5)
        self.assertEqual(uevent.read_events(sock), (True, False))

    def test_remove_is_reported(self):
        self.assertEqual(uevent.read_events(self.events(self.REMOVE)), (False, True))

    def test_add_and_remove_together(self):
        sock = self.events(self.REMOVE, self.ADD % 4)
        self.assertEqual(uevent.read_events(sock), (True, True))

    def test_other_subsystems_and_an_empty_queue_report_nothing(self):
        self.assertEqual(uevent.read_events(self.events(self.OTHER)), (False, False))
        self.assertEqual(uevent.read_events(self.events()), (False, False))

    def test_dropped_events_count_as_both(self):
        class Overflowing:
            def recv(self, size):
                raise OSError(errno.ENOBUFS, "No buffer space available")

        self.assertEqual(uevent.read_events(Overflowing()), (True, True))

    def test_other_errors_are_raised(self):
        class Broken:
            def recv(self, size):
                raise OSError(errno.EBADF, "Bad file descriptor")

        with self.assertRaises(OSError):
            uevent.read_events(Broken())

    def test_monitor_can_be_opened(self):
        try:
            sock = uevent.open_monitor()
        except OSError as e:
            self.skipTest(f"netlink uevents not available: {e}")
        sock.close()


if __name__ == "__main__":
    unittest.main()
