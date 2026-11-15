# SPDX-License-Identifier: GPL-2.0-or-later
"""Protocol parser tests.

Run with:

    cd daemon && python3 -m unittest discover -s tests -v

Cases marked "upstream" mirror anany1702/lamzu-ctl's tests, so the Compx and
Aurora layouts stay compatible with the reference implementation.
"""

import os
import pathlib
import sys
import threading
import time
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from ampere_hid import hidraw  # noqa: E402
from ampere_hid.drivers import compx, lamzu, mchose  # noqa: E402


class CompxTests(unittest.TestCase):
    def test_battery_packet_layout(self):
        packet = compx.build_battery_packet()
        self.assertEqual(len(packet), 17)
        self.assertEqual(packet[:2], bytes([0x08, 0x04]))
        self.assertEqual(packet[16], (compx.checksum(bytes(packet[1:16]) + b"\x00") - 8) & 0xFF)

    def test_real_reply_shape(self):
        # 08 04 00 00 00 02 23 00 0e 94 ... (0x23 = 35 %)
        reply = bytes.fromhex("08040000000223000e9400000000000082")
        self.assertEqual(compx.parse_battery_response(reply), (35, 0))

    def test_without_report_id(self):
        self.assertEqual(
            compx.parse_battery_response(bytes.fromhex("04000000022300")), (35, 0)
        )

    def test_charging_flag(self):
        reply = bytes.fromhex("08040000000223010e94" + "00" * 6)
        self.assertEqual(compx.parse_battery_response(reply), (35, 1))

    def test_rejects_impossible_percentage(self):
        self.assertIsNone(
            compx.parse_battery_response(bytes.fromhex("080400000002ff00"))
        )

    def test_rejects_wrong_command(self):
        self.assertIsNone(
            compx.parse_battery_response(bytes.fromhex("0808000000022300"))
        )

    def test_rejects_maya_x_echo(self):
        # The Maya X answers the report-8 request with a generic echo.
        self.assertIsNone(compx.parse_battery_response(bytes.fromhex("a1010802000011")))


class AuroraTests(unittest.TestCase):
    def test_query_layouts(self):
        new = lamzu.build_aurora_battery_packet(True)
        legacy = lamzu.build_aurora_battery_packet(False)
        self.assertEqual(len(new), 65)
        self.assertEqual(new[3:7], bytes([2, 2, 0, 0x83]))
        self.assertEqual(legacy[2:5], bytes([2, 0x8F, 1]))

    def test_new_with_report_id(self):  # upstream
        response = bytes([0, 0xA1, 0, 0, 2, 0, 0x83, 1, 87])
        self.assertEqual(lamzu.parse_aurora_battery_response(response), (87, 1))

    def test_legacy_without_report_id(self):  # upstream
        response = bytes([0xA1, 2, 0x8F, 0, 0, 64])
        self.assertEqual(lamzu.parse_aurora_battery_response(response), (64, 0))

    def test_invalid_response(self):  # upstream
        self.assertIsNone(lamzu.parse_aurora_battery_response(bytes([0xA1])))

    def test_rejects_impossible_percentage(self):  # upstream
        response = bytes([0, 0xA1, 0, 0, 2, 0, 0x83, 0, 101])
        self.assertIsNone(lamzu.parse_aurora_battery_response(response))

    def test_maya_x_legacy_frame(self):
        # 00 a1 02 8f 01 00 00 00 59: the Maya X keeps state/level at 6/7.
        response = bytes.fromhex("00a1028f0100000059") + bytes(56)
        self.assertEqual(lamzu.parse_aurora_battery_response(response), (89, 0))

    def test_rejects_all_zero_template(self):
        response = bytes.fromhex("00a1028f0100000000") + bytes(56)
        self.assertIsNone(lamzu.parse_aurora_battery_response(response))

    def test_rejects_no_answer_marker(self):
        # A sleeping Maya X answers with marker 0xA0, not 0xA1.
        response = bytes.fromhex("00a000020200830000") + bytes(55)
        self.assertIsNone(lamzu.parse_aurora_battery_response(response))


class MchoseTests(unittest.TestCase):
    def test_request_layout(self):
        request = mchose.build_request(0x06, mchose.LONG_REPORT, mchose.LONG_LEN)
        self.assertEqual(len(request), 65)
        self.assertEqual(request[0], 0x12)
        self.assertEqual(request[1], 0x06 ^ 0xFF)
        self.assertEqual(request[2:], b"\xFF" * 63)

    def _reply(self, vid, level, charge, report=0x12):
        # decoded payload: vid, model, firmware(4), flags, level, charging, level, charging
        payload = (vid.to_bytes(2, "little") + b"\x31\x00" + b"\x02\x05\x07\x00"
                   + bytes([0x09, level, charge, level, charge]))
        return bytes([report, 0x06 ^ 0xFF]) + mchose.invert(payload)

    def test_m7_ultra_reply(self):
        # HaloBattery's documented M7 Ultra reply (0x5253, 100 %).
        self.assertEqual(mchose.parse_status_response(self._reply(0x5253, 100, 0)), (100, 0))

    def test_a7_v2_pro_reply(self):
        self.assertEqual(mchose.parse_status_response(self._reply(0x3837, 62, 0)), (62, 0))

    def test_charging(self):
        self.assertEqual(mchose.parse_status_response(self._reply(0x3837, 62, 1)), (62, 1))

    def test_charging_full(self):
        self.assertEqual(mchose.parse_status_response(self._reply(0x3837, 100, 1)), (100, 2))

    def test_rejects_all_zero_payload(self):
        # The receiver answers the frame with zeros while the mouse sleeps.
        zero = bytes([0x12, 0x06 ^ 0xFF]) + b"\x00" * 63
        self.assertIsNone(mchose.parse_status_response(zero))

    def test_rejects_unknown_vendor(self):
        self.assertIsNone(mchose.parse_status_response(self._reply(0x1234, 50, 0)))

    def test_rejects_impossible_percentage(self):
        self.assertIsNone(mchose.parse_status_response(self._reply(0x3837, 200, 0)))

    def test_push_notice(self):
        raw = bytes([0x13, 0x1D, 0xFE, 0xFE, 0xFF, 0xA2]) + bytes(10)
        self.assertEqual(mchose.parse_push_report(raw), (93, 0))

    def test_rejects_wrong_push(self):
        self.assertIsNone(
            mchose.parse_push_report(bytes([0x12, 0x1D, 0, 0, 0, 0]) + bytes(10))
        )


class ActivityWaitTests(unittest.TestCase):
    """wait_for_activity uses select(); a FIFO exercises the same code path."""

    def _fifo(self):
        import tempfile

        path = os.path.join(tempfile.mkdtemp(), "fifo")
        os.mkfifo(path)
        return path

    def test_returns_false_on_timeout(self):
        start = time.monotonic()
        self.assertFalse(hidraw.wait_for_activity([self._fifo()], 0.2))
        self.assertGreaterEqual(time.monotonic() - start, 0.19)

    def test_returns_true_when_data_arrives(self):
        path = self._fifo()

        def writer():
            time.sleep(0.15)
            fd = os.open(path, os.O_WRONLY)
            os.write(fd, b"x")
            os.close(fd)

        threading.Thread(target=writer, daemon=True).start()
        start = time.monotonic()
        self.assertTrue(hidraw.wait_for_activity([path], 5.0))
        self.assertLess(time.monotonic() - start, 2.0)

    def test_wake_fd_ends_the_wait_early(self):
        wake_r, wake_w = os.pipe()
        self.addCleanup(os.close, wake_r)
        self.addCleanup(os.close, wake_w)
        threading.Timer(0.15, os.write, (wake_w, b"x")).start()
        start = time.monotonic()
        self.assertTrue(hidraw.wait_for_activity([self._fifo()], 5.0, wake_r))
        self.assertLess(time.monotonic() - start, 2.0)

    def test_wake_fd_works_when_no_node_can_be_opened(self):
        wake_r, wake_w = os.pipe()
        self.addCleanup(os.close, wake_r)
        self.addCleanup(os.close, wake_w)
        os.write(wake_w, b"x")
        self.assertTrue(hidraw.wait_for_activity(["/nonexistent/hidraw"], 5.0, wake_r))

    def test_unused_wake_fd_does_not_change_the_timeout(self):
        wake_r, wake_w = os.pipe()
        self.addCleanup(os.close, wake_r)
        self.addCleanup(os.close, wake_w)
        start = time.monotonic()
        self.assertFalse(hidraw.wait_for_activity([self._fifo()], 0.2, wake_r))
        self.assertGreaterEqual(time.monotonic() - start, 0.19)

    def test_missing_nodes_do_not_spin(self):
        start = time.monotonic()
        self.assertFalse(hidraw.wait_for_activity(["/nonexistent/hidraw"], 0.2))
        self.assertGreaterEqual(time.monotonic() - start, 0.19)


if __name__ == "__main__":
    unittest.main()
