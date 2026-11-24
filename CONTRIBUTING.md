# Contributing to Ampere

Bug reports, patches and new device support are welcome. Below: how to build
and run the code, how the companion service works, and how to add a mouse.

## Layout

```
Makefile            build, install and development targets (`make help`)
eslint.config.mjs   lint rules for the extension JavaScript
icon/, screenshots/ project assets, not part of the extension package

extension/          everything that ships in the GNOME extension
  extension.js      panel indicator, polling and rendering
  prefs.js          preferences window
  hidbattery.js     D-Bus client for the companion service
  utils.js          UPower D-Bus access, shared enums and helpers
  stylesheet.css    panel and hover-card styling
  metadata.json     UUID, supported shell versions, version
  schemas/          GSettings schema (the compiled file is generated)

daemon/
  ampere_hid/       the package; released as the single file `ampere-hid`
    cli.py          subcommands: run, status, once, probe, diagnose, ...
    daemon.py       D-Bus service, cache, scheduling (the only part needing gi)
    sweep.py        one pass over all devices
    hidraw.py       device enumeration, HID report helpers
    tools.py        probe, sniff, diagnose (no gi needed)
    status.py       one-screen summary: version, unit, rule, devices
    install.py      install/uninstall: program, systemd unit, D-Bus file, udev
    udev.py         product IDs that get hidraw access; renders the rule
    archive.py      builds the single-file program (a zipapp)
    names.py        bus name and object path
    drivers/
      base.py       Driver interface
      lamzu.py      Lamzu (Aurora + Compx)
      compx.py      ATK/VXE/VGN and other Compx silicon
      mchose.py     MCHOSE (RealTek)
      __init__.py   driver registry
  tests/            parser, sweep and installer tests, no hardware needed
```

## Building and running

```sh
make install      # compile the schema, pack extension/, install the package
make pack         # only build build/ampere@crian.shell-extension.zip
make lint         # ESLint on extension/
make test         # daemon tests
make help         # everything else
```

Log out and back in after changes (Wayland cannot reload the shell in place).

`make pack` runs `gnome-extensions pack` on `extension/`, which only includes
the standard files; the runtime modules (`utils.js`, `hidbattery.js`) and
`LICENSE` are added with `--extra-source`. The package therefore holds nothing
from `daemon/` or the project root. `build/` and
`extension/schemas/gschemas.compiled` are build artifacts and are not tracked.

For development it is convenient to link the directory into the extensions
folder instead of packing:

```sh
make dev          # ln -s extension/ ~/.local/share/gnome-shell/extensions/ampere@crian
make undev        # remove the link again
```

The link needs `extension/schemas/gschemas.compiled`, because the shell loads
the schema straight from that directory; `make dev` compiles it first.

The companion service is built and installed from the checkout with:

```sh
make daemon-install                # builds the single file and installs it
# or, from daemon/: python3 -m ampere_hid install
systemctl --user status ampere-hid
journalctl --user -u ampere-hid -f
make daemon                        # only build ./ampere-hid, for a release
```

## The companion service

The extension never spawns processes; it talks to the service over D-Bus:

```
Bus name:  org.gnome.shell.extensions.ampere.HidBattery
Object:    /org/gnome/shell/extensions/ampere/HidBattery
Interface: org.gnome.shell.extensions.ampere.HidBattery
Method:    GetDevices() -> s   JSON {"devices": [...]}
Property:  Version -> s        the service's own version
Signal:    Changed()
```

Each device entry is `{"key", "name", "kind", "pct", "state"}`, where `kind`
selects the panel icon and `state` is `0` discharging, `1` charging, `2` full.

Design notes:

* The sweep runs in a worker thread and `GetDevices` answers from the cache.
  In the main loop it would take a few hundred milliseconds awake and up to a
  few seconds asleep, which delays the icon after login.
* The result is applied on the main loop, which emits `Changed` only when the
  payload differs.
* The poll timer repeats every 30 s and is not restarted by a sweep, so waiting
  for a sleeping mouse does not stretch the interval.
* Kernel hidraw add/remove events trigger a sweep at once (`uevent.py`); without
  the netlink socket the timer is the only trigger.
* While a device gives no reading, the worker waits for input reports on its
  nodes (`wait_for_activity`). A pipe and the uevent handler end the wait at
  once, and the readings of the devices that did answer are published before it.
* A device without an answer keeps its last reading for three sweeps before it
  disappears.
* The service reports its version; the preferences compare it with their own
  `version-name` and point at the install command when it is older.
* A driver only sends battery query commands; it never changes device settings.
  Do not add configuration, EEPROM or firmware writes.

## Debugging without D-Bus

Run from the `daemon/` directory, or use the installed `ampere-hid`:

```sh
python3 -m ampere_hid probe      # hidraw devices, VID/PID, interface, driver
python3 -m ampere_hid once       # read the battery once
python3 -m ampere_hid diagnose   # devices, matching drivers and raw answers
python3 -m ampere_hid sniff 3    # passively dump input reports for 3 s
python3 -m ampere_hid rules      # the udev rule that install --udev writes
```

`probe` and `sniff` do not talk to the device; `diagnose` sends the battery
query. None of them changes a setting. `sniff` finds devices that push their
status instead of answering a query. None of these needs PyGObject.

## Adding a device

1. Check whether UPower already knows the device (`upower -e`,
   `ls /sys/class/power_supply/`). If it does, nothing needs to be added.
2. Inspect the hidraw nodes with `probe`: VID/PID, interface numbers and
   whether a driver already claims the device.
3. Work out the protocol, e.g. from the vendor tool with USBPcap, or by probing
   feature reports by hand. Only send battery queries, nothing that writes.
4. Add a driver in `daemon/ampere_hid/drivers/`:

   ```python
   # daemon/ampere_hid/drivers/myvendor.py
   from .base import Driver
   from ..hidraw import HidHandle, can_access, query_feature

   class MyVendorDriver(Driver):
       name = "MyVendor"
       kind = "mouse"                     # selects the panel icon
       vids = frozenset({0x1234})
       preferred_interfaces = (2,)

       def read(self, device):            # -> (percent, state) or None
           for node in device.sorted_nodes(self.preferred_interfaces):
               if not can_access(node):
                   continue
               # A feature-report request/answer:
               result = query_feature(node, build_packet(), parse_response)
               # ... or a raw exchange:
               # with HidHandle(node.path) as hid:
               #     hid.flush(); hid.send(build_packet())
               #     result = parse_response(hid.read_input())
               if result:
                   return result
           return None
   ```

5. Register it in `daemon/ampere_hid/drivers/__init__.py`. Order matters: the
   first matching driver wins.

   ```python
   from .myvendor import MyVendorDriver
   DRIVERS = [LamzuDriver(), CompxDriver(), MchoseDriver(), MyVendorDriver()]
   ```

6. Add the device's VID:PID to `GROUPS` in `daemon/ampere_hid/udev.py`; the
   udev rule is generated from it. The rules are per product ID on purpose: a
   vendor-wide rule would also grant access to keyboards and other HID devices
   on the same chip. A test fails when a vendor ID there belongs to no driver.
7. Test with `probe`, `diagnose` and `once`, then add parser tests.
8. Note in the README table whether it was verified on real hardware.

## Tests

The protocol parsers, the sweep and the installer are covered without
hardware (the installer tests use a temporary home and a fake `systemctl`):

```sh
make test
```

`tests/test_daemon_bus.py` starts the real daemon on a private session bus
(`dbus-run-session`), asks it for its devices and version, and stops it with
SIGTERM. It is skipped when PyGObject or `dbus-run-session` is missing.

Please add a case for every new report layout, including the "no answer" and
"out of range" variants. Those are what keep a sleeping mouse from showing a
bogus percentage.

CI compiles the schema, runs these tests, lints Python and JavaScript, builds
the single-file `ampere-hid` and runs it, and builds the extension package to
check that it contains only extension files.

## Releasing

Push a tag. `v<version-name>` (for example `v1.2`) releases both sides: the
workflow builds the extension package for EGO and attaches it with `ampere-hid`
to the GitHub release. A patch tag such as `v1.2.1` leaves `version-name` alone
and releases only the service, for fixes that need no extension upload.

The changelog entry uses the tag as its heading; the release notes are taken
from it.

## Commit messages

Use [Conventional Commits](https://www.conventionalcommits.org/), so the
history stays filterable and a changelog could be generated from it later:

```
feat:     new behaviour
fix:      bug fix
docs:     documentation only
ci:       tests, workflows, packaging
refactor: no change in behaviour
chore:    maintenance
```

Keep the subject imperative and within 50 characters. `CHANGELOG.md` is written
by hand for now, one entry per release tag; the EGO version is noted where the
release was uploaded there.

## Protocol references

* [anany1702/lamzu-ctl](https://github.com/anany1702/lamzu-ctl) - Compx
  (report ID 8, command 4) and Aurora (report ID 0, `0x83`), including the
  legacy layout. Its test cases are mirrored here.
* [Fan4Metal/mouse_tray](https://github.com/Fan4Metal/mouse_tray) - Compx/Nordic
  silicon and the report-8 layout; the same layout is used by the Linux
  `hid-vxe-r1` module.
* [HeyOkay/HaloBattery](https://github.com/HeyOkay/HaloBattery) - MCHOSE feature
  query (report `0x11`/`0x12`, command `0x06`, payload inverted).
* [alexfrih/mchose-linux](https://github.com/alexfrih/mchose-linux) - MCHOSE
  protocol notes recovered from the vendor's web driver.
