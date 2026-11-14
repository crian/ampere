# Contributing to Ampere

Bug reports, patches and new device support are welcome. Below: how to build
and run the code, how the companion service works, and how to add a mouse.

## Layout

```
extension.js        panel indicator, polling and rendering
prefs.js            preferences window
hidbattery.js       D-Bus client for the companion service
utils.js            UPower D-Bus access, shared enums and helpers
stylesheet.css      panel and hover-card styling
schemas/            GSettings schema (the compiled file is generated)

daemon/
  ampere-hid        entry point of the installed service
  ampere_hid/
    daemon.py       D-Bus service, cache, scheduling
    hidraw.py       device enumeration, HID report helpers
    drivers/
      base.py       Driver interface
      lamzu.py      Lamzu (Aurora + Compx)
      compx.py      ATK/VXE/VGN and other Compx silicon
      mchose.py     MCHOSE (RealTek)
      __init__.py   driver registry
  tests/            parser tests, no hardware needed
  70-ampere-hid.rules
  install.sh / uninstall.sh
```

## Building and running

```sh
glib-compile-schemas schemas/
gnome-extensions pack --force \
  --extra-source=utils.js \
  --extra-source=hidbattery.js \
  --extra-source=LICENSE
gnome-extensions install --force ampere@crian.shell-extension.zip
```

Log out and back in after changes (Wayland cannot reload the shell in place).

`gnome-extensions pack` only includes the standard files; the `--extra-source`
arguments add the runtime modules and the licence. The ZIP and
`schemas/gschemas.compiled` are build artifacts and are not tracked.

For development it is convenient to symlink the checkout into the extension
directory instead of packing:

```sh
ln -s "$PWD" ~/.local/share/gnome-shell/extensions/ampere@crian
```

In that case `schemas/gschemas.compiled` has to exist, because the shell loads
the schema straight from that directory.

Control the companion service with:

```sh
./daemon/install.sh
systemctl --user status ampere-hid
journalctl --user -u ampere-hid -f
```

## The companion service

The extension never spawns processes; it talks to the service over D-Bus:

```
Bus name:  org.gnome.shell.extensions.ampere.HidBattery
Object:    /org/gnome/shell/extensions/ampere/HidBattery
Interface: org.gnome.shell.extensions.ampere.HidBattery
Method:    GetDevices() -> s   JSON {"devices": [...]}
Signal:    Changed()
```

Each device entry is `{"key", "name", "kind", "pct", "state"}`, where `kind`
selects the panel icon and `state` is `0` discharging, `1` charging, `2` full.

Design notes:

* The sweep runs in a worker thread and `GetDevices` answers from the cache.
  In the main loop it would take 0.4 s awake and up to 2 s asleep, which delays
  the icon after login.
* The result is applied on the main loop, which emits `Changed` only when the
  payload differs.
* Polling is every 30 s. When a receiver is present but does not answer, the
  worker waits for input reports (`wait_for_activity`) instead of polling: a
  sleeping mouse sends nothing, so this costs no CPU and returns as soon as the
  mouse moves, at which point the reading is retried immediately.
* A device without an answer keeps its last reading for three polls before it
  disappears.
* A driver only sends battery query commands; it never changes device settings.
  Do not add configuration, EEPROM or firmware writes.

## Debugging without D-Bus

Run from the `daemon/` directory, or use `~/.local/lib/ampere/ampere-hid`:

```sh
python3 -m ampere_hid --probe      # hidraw devices, VID/PID, interface, driver
python3 -m ampere_hid --once       # read the battery once
python3 -m ampere_hid --diagnose   # devices, matching drivers and raw answers
python3 -m ampere_hid --sniff 3    # passively dump input reports for 3 s
```

`--probe` and `--sniff` do not talk to the device; `--diagnose` sends the
battery query. None of them changes a setting. `--sniff` finds devices that
push their status instead of answering a query.

## Adding a device

1. Check whether UPower already knows the device (`upower -e`,
   `ls /sys/class/power_supply/`). If it does, nothing needs to be added.
2. Inspect the hidraw nodes with `--probe`: VID/PID, interface numbers and
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

6. Add the device's VID:PID to `daemon/70-ampere-hid.rules`. The rules are per
   product ID on purpose: a vendor-wide rule would also grant access to
   keyboards and other HID devices on the same chip.
7. Test with `--probe`, `--diagnose` and `--once`, then add parser tests.
8. Note in the README table whether it was verified on real hardware.

## Tests

The protocol parsers are covered without hardware:

```sh
cd daemon && python3 -m unittest discover -s tests -v
```

Please add a case for every new report layout, including the "no answer" and
"out of range" variants. Those are what keep a sleeping mouse from showing a
bogus percentage.

CI compiles the schema, runs these tests, lints Python, JavaScript and shell,
and builds the extension package to check that it contains only extension
files.

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
by hand for now, one entry per EGO version.

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
