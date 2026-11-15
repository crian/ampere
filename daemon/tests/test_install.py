# SPDX-License-Identifier: GPL-2.0-or-later
"""Installer, udev table and single-file build. No hardware, no systemd."""

import contextlib
import io
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import ampere_hid  # noqa: E402
from ampere_hid import archive, cli, install, udev  # noqa: E402
from ampere_hid.drivers import DRIVERS  # noqa: E402
from ampere_hid.names import BUS_NAME  # noqa: E402


class QuotingTests(unittest.TestCase):
    def test_plain_path_is_left_alone(self):
        self.assertEqual(install.systemd_quote("/usr/bin/python3"), "/usr/bin/python3")

    def test_spaces_are_quoted(self):
        self.assertEqual(install.systemd_quote("/home/a b/x"), '"/home/a b/x"')

    def test_specifiers_and_variables_are_escaped(self):
        self.assertEqual(install.systemd_quote("/a%h/$HOME"), '"/a%%h/$$HOME"')

    def test_unit_and_dbus_file_name_the_run_command(self):
        unit = install.render_unit("/usr/bin/python3", pathlib.Path("/h/my dir/ampere-hid"))
        self.assertIn('ExecStart=/usr/bin/python3 "/h/my dir/ampere-hid" run', unit)
        service = install.render_dbus_service("/usr/bin/python3", pathlib.Path("/h/b/ampere-hid"))
        self.assertIn(f"Name={BUS_NAME}", service)
        self.assertIn("Exec=/usr/bin/python3 /h/b/ampere-hid run", service)
        self.assertIn("SystemdService=ampere-hid.service", service)


class UdevTableTests(unittest.TestCase):
    def test_every_vendor_belongs_to_a_driver_and_vice_versa(self):
        driver_vids = set().union(*(d.vids for d in DRIVERS))
        self.assertEqual(udev.vendor_ids(), driver_vids)

    def test_no_duplicate_ids(self):
        ids = [pair for _, _, group in udev.GROUPS for pair in group]
        self.assertEqual(len(ids), len(set(ids)))

    def test_rules_are_one_line_per_id_and_use_lowercase_hex(self):
        text = udev.render_rules()
        rules = [line for line in text.splitlines() if line.startswith("SUBSYSTEM")]
        ids = [pair for _, _, group in udev.GROUPS for pair in group]
        self.assertEqual(len(rules), len(ids))
        for rule in rules:
            self.assertRegex(
                rule,
                r'idVendor\}=="[0-9a-f]{4}", ATTRS\{idProduct\}=="[0-9a-f]{4}"',
            )
        self.assertTrue(text.endswith("\n"))


class FakeRun:
    def __init__(self):
        self.calls = []
        self.rules_seen = None

    def __call__(self, cmd, quiet=False):
        self.calls.append(cmd)
        if cmd[:1] == ["sudo"]:
            self.rules_seen = pathlib.Path(cmd[-2]).read_text(encoding="utf-8")
        return 0


class InstallTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = pathlib.Path(self._tmp.name)
        self.env = {"HOME": str(root)}
        self.rules_path = root / "70-ampere-hid.rules"
        self.run = FakeRun()
        self.paths = install.paths(self.env)
        patcher = mock.patch.object(install.shutil, "which", return_value="/usr/bin/x")
        patcher.start()
        self.addCleanup(patcher.stop)

    def do_install(self, **kwargs):
        options = {
            "env": self.env,
            "run": self.run,
            "interactive": False,
            "rules_path": self.rules_path,
            "check_pygobject": lambda: False,
        }
        options.update(kwargs)
        with contextlib.redirect_stdout(io.StringIO()):
            return install.install(**options)

    def systemctl(self, action):
        return ["systemctl", "--user", action, install.UNIT_NAME] in self.run.calls

    def test_install_writes_program_unit_and_activation_file(self):
        self.assertEqual(self.do_install(udev=False), 0)
        program = self.paths.program
        self.assertTrue(program.is_file())
        self.assertEqual(program.stat().st_mode & 0o777, 0o755)
        self.assertTrue(program.read_bytes().startswith(b"#!/usr/bin/env python3"))
        unit = self.paths.unit.read_text(encoding="utf-8")
        self.assertIn(f"{program} run", unit)
        self.assertIn(f"{program} run", self.paths.dbus_service.read_text(encoding="utf-8"))
        self.assertTrue(self.systemctl("restart"))

    def test_second_install_changes_nothing_and_only_starts(self):
        self.do_install(udev=False)
        self.run.calls.clear()
        self.do_install(udev=False)
        self.assertTrue(self.systemctl("start"))
        self.assertFalse(self.systemctl("restart"))

    def test_the_old_script_install_is_removed(self):
        legacy = self.paths.legacy_libdir
        (legacy / "ampere_hid").mkdir(parents=True)
        (legacy / "ampere_hid" / "daemon.py").write_text("old")
        (legacy / "ampere-hid").write_text("old")
        self.do_install(udev=False)
        self.assertFalse(legacy.exists())

    def test_udev_rule_is_installed_through_sudo_when_requested(self):
        with mock.patch.object(install, "privilege_prefix", return_value=["sudo"]):
            self.do_install(udev=True)
        self.assertEqual(self.run.rules_seen, udev.render_rules())
        sudo_call = next(c for c in self.run.calls if c[:1] == ["sudo"])
        self.assertEqual(sudo_call[-1], str(self.rules_path))

    def test_no_udev_flag_never_asks_or_installs(self):
        with mock.patch.object(install, "privilege_prefix") as prefix:
            self.do_install(udev=False, interactive=True)
        prefix.assert_not_called()

    def test_current_rule_is_not_written_again(self):
        self.rules_path.write_text(udev.render_rules(), encoding="utf-8")
        with mock.patch.object(install, "privilege_prefix") as prefix:
            self.do_install(udev=True)
        prefix.assert_not_called()
        self.assertEqual(install.rules_state(self.rules_path), "current")

    def test_outdated_rule_is_detected(self):
        self.rules_path.write_text("# old\n", encoding="utf-8")
        self.assertEqual(install.rules_state(self.rules_path), "outdated")
        self.assertEqual(install.rules_state(self.rules_path.with_suffix(".x")), "missing")

    def test_missing_pygobject_stops_before_writing_anything(self):
        with contextlib.redirect_stderr(io.StringIO()) as err:
            status = self.do_install(check_pygobject=lambda: True)
        self.assertEqual(status, 1)
        self.assertIn("PyGObject", err.getvalue())
        self.assertFalse(self.paths.program.exists())

    def test_uninstall_removes_everything_but_keeps_the_rule(self):
        self.do_install(udev=False)
        self.rules_path.write_text("x", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()) as out:
            install.uninstall(
                env=self.env, run=self.run, interactive=False, rules_path=self.rules_path
            )
        for path in (self.paths.program, self.paths.unit, self.paths.dbus_service):
            self.assertFalse(path.exists())
        self.assertTrue(self.rules_path.exists())
        self.assertIn("uninstall --udev", out.getvalue())
        self.assertIn(
            ["systemctl", "--user", "disable", "--now", install.UNIT_NAME], self.run.calls
        )


class ProgramTests(unittest.TestCase):
    def test_built_program_runs_on_its_own(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "ampere-hid"
            target.write_bytes(archive.build_bytes())
            target.chmod(0o755)
            result = subprocess.run(
                [sys.executable, str(target), "rules"],
                capture_output=True,
                text=True,
                check=True,
            )
        self.assertEqual(result.stdout, udev.render_rules())

    def test_program_contains_no_caches_or_tests(self):
        import zipfile

        with zipfile.ZipFile(io.BytesIO(archive.build_bytes())) as z:
            names = z.namelist()
        self.assertIn("__main__.py", names)
        self.assertFalse([n for n in names if "__pycache__" in n or "tests" in n])
        self.assertTrue(any(re.fullmatch(r"ampere_hid/drivers/lamzu\.py", n) for n in names))

    def test_version_flag_prints_the_version(self):
        with contextlib.redirect_stdout(io.StringIO()) as out, self.assertRaises(
            SystemExit
        ) as raised:
            cli.main(["--version"])
        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(out.getvalue().strip(), f"ampere-hid {ampere_hid.__version__}")

    def test_build_stamps_the_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = pathlib.Path(tmp) / "ampere-hid"
            target.write_bytes(archive.build_bytes("9.9"))
            target.chmod(0o755)
            result = subprocess.run(
                [sys.executable, str(target), "--version"],
                capture_output=True, text=True, check=True,
            )
        self.assertEqual(result.stdout.strip(), "ampere-hid 9.9")

    def test_cli_without_command_prints_help(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main([]), 0)
        self.assertIn("install", out.getvalue())

    def test_cli_rules_prints_the_rule_text(self):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.main(["rules"]), 0)
        self.assertEqual(out.getvalue(), udev.render_rules())


class InstallFromProgramTests(unittest.TestCase):
    """The user path: run the built file and let it install itself."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = pathlib.Path(self._tmp.name)
        self.home = self.root / "home"
        self.bin = self.root / "bin"
        self.home.mkdir()
        self.bin.mkdir()

        self.log = self.root / "systemctl.log"
        fake = self.bin / "systemctl"
        fake.write_text(
            '#!/bin/sh\necho "$@" >> "' + str(self.log) + '"\nexit 0\n',
            encoding="utf-8",
        )
        fake.chmod(0o755)

        self.program = self.root / "ampere-hid"
        self.program.write_bytes(archive.build_bytes())
        self.program.chmod(0o755)

    def run_program(self, *args):
        env = {
            "HOME": str(self.home),
            "PATH": f"{self.bin}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        }
        return subprocess.run(
            [sys.executable, str(self.program), *args],
            env=env, capture_output=True, text=True,
        )

    def installed_paths(self):
        home = self.home
        return (
            home / ".local" / "bin" / "ampere-hid",
            home / ".config" / "systemd" / "user" / "ampere-hid.service",
            home / ".local" / "share" / "dbus-1" / "services" / f"{BUS_NAME}.service",
        )

    def test_the_built_file_installs_itself(self):
        result = self.run_program("install", "--no-udev")
        self.assertEqual(result.returncode, 0, result.stderr)

        program, unit, dbus_service = self.installed_paths()
        self.assertTrue(program.is_file())
        self.assertEqual(program.read_bytes(), self.program.read_bytes())
        self.assertIn(" run", unit.read_text(encoding="utf-8"))
        self.assertIn(f"Name={BUS_NAME}", dbus_service.read_text(encoding="utf-8"))

        # The fake systemctl was called, and the first install restarts.
        logged = self.log.read_text(encoding="utf-8")
        self.assertIn("--user restart ampere-hid.service", logged)

    def test_running_install_twice_only_starts(self):
        self.run_program("install", "--no-udev")
        self.log.write_text("", encoding="utf-8")

        result = self.run_program("install", "--no-udev")
        self.assertEqual(result.returncode, 0, result.stderr)
        logged = self.log.read_text(encoding="utf-8")
        self.assertIn("--user start ampere-hid.service", logged)
        self.assertNotIn("--user restart ampere-hid.service", logged)

    def test_uninstall_removes_the_installed_files(self):
        self.run_program("install", "--no-udev")
        result = self.run_program("uninstall")
        self.assertEqual(result.returncode, 0, result.stderr)

        for path in self.installed_paths():
            self.assertFalse(path.exists(), path)


if __name__ == "__main__":
    unittest.main()
