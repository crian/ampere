# SPDX-License-Identifier: GPL-2.0-or-later
"""Command line of the ``ampere-hid`` program."""

import argparse
import json
import os
import sys

from . import __version__, status, tools
from . import install as installer
from .archive import build_bytes
from .hidraw import log
from .sweep import query_all
from .udev import render_rules


def cmd_run(args):
    try:
        from . import daemon
    except (ImportError, ValueError):
        print(installer.PYGOBJECT_HELP, file=sys.stderr, end="")
        return 1
    return daemon.run()


def cmd_once(args):
    items, matched = query_all()
    print(json.dumps({"devices": items}, separators=(",", ":")))
    if matched and not items:
        log("supported device found, no battery answer (asleep?)")
        return 1
    return 0


def cmd_probe(args):
    print(json.dumps({"devices": tools.probe()}, indent=2))
    return 0


def cmd_status(args):
    print(status.status_text())
    return 0


def cmd_diagnose(args):
    print(json.dumps({"devices": tools.diagnose()}, indent=2))
    return 0


def cmd_sniff(args):
    print(json.dumps({"nodes": tools.sniff(args.seconds)}, indent=2))
    return 0


def cmd_rules(args):
    sys.stdout.write(render_rules())
    return 0


def cmd_install(args):
    udev = True if args.udev else (False if args.no_udev else None)
    return installer.install(udev=udev)


def cmd_uninstall(args):
    return installer.uninstall(udev=args.udev)


def cmd_build(args):
    with open(args.output, "wb") as f:
        f.write(build_bytes(args.stamp))
    os.chmod(args.output, 0o755)
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ampere-hid",
        description="Battery level of wireless mice that UPower does not report.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    sub = parser.add_subparsers(dest="command", metavar="command")

    def add(name, func, help_text):
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.set_defaults(func=func)
        return p

    add("run", cmd_run, "run the D-Bus service (what systemd starts)")
    add("once", cmd_once, "read the battery once and print it as JSON")
    add("status", cmd_status, "show version, service, rule and devices")
    add("probe", cmd_probe, "list hidraw devices and their driver, no queries")
    add("diagnose", cmd_diagnose, "query the battery and print the raw answers")
    p = add("sniff", cmd_sniff, "passively dump input reports, no queries")
    p.add_argument("seconds", nargs="?", type=float, default=3.0)
    add("rules", cmd_rules, "print the udev rule that grants hidraw access")
    p = add("install", cmd_install, "install the service for the current user")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--udev", action="store_true", help="install the udev rule (root)")
    group.add_argument("--no-udev", action="store_true", help="do not ask about the udev rule")
    p = add("uninstall", cmd_uninstall, "remove the service")
    p.add_argument("--udev", action="store_true", help="also remove the udev rule (root)")
    p = add("build", cmd_build, "write the single-file program (for packaging)")
    p.add_argument("output")
    p.add_argument(
        "--stamp", dest="stamp", metavar="VERSION",
        help="write this version into the program (the release does this)",
    )
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return args.func(args)
