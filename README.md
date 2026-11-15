# Ampere

Battery level of wireless mice and keyboards in the GNOME top bar.

Devices that UPower reports work out of the box. Mice that only expose their
battery through vendor HID reports (Lamzu, MCHOSE, ATK/Compx) need an optional
companion service, which the extension talks to over D-Bus.

![Icon, bar, percentage and the hover card](screenshots/hover-card.png)

## Features

* Icon, bar and percentage per device in the panel
* Colour coding by charge level and charging state, with configurable thresholds
* Hover card with ring, name, percentage and state
* Own button, or placed inside the Quick Settings icon row
* Individual devices can be hidden

## Installation

Install it from
[extensions.gnome.org](https://extensions.gnome.org/extension/11770/ampere/) and
enable it in the Extensions app.

From source:

```sh
git clone https://github.com/crian/ampere.git
cd ampere
glib-compile-schemas schemas/
gnome-extensions pack --force \
  --extra-source=utils.js \
  --extra-source=hidbattery.js \
  --extra-source=LICENSE
gnome-extensions install --force ampere@crian.shell-extension.zip
```

Log out and back in afterwards; Wayland cannot reload the shell in place.

## Vendor HID devices (optional)

This part is only needed for mice that are not in UPower, for example a Lamzu
Maya X, an MCHOSE A7 V2 Pro or an ATK A9. Everything else works without it.

The companion service is a single file. Download `ampere-hid` from the
[latest release](https://github.com/crian/ampere/releases/latest) and run its
installer (the file is attached to releases from v1.1 on):

```sh
curl -fsSLo ampere-hid \
  https://github.com/crian/ampere/releases/latest/download/ampere-hid
chmod +x ampere-hid
./ampere-hid install --udev
```

This is a one-time command for you to run. The extension never downloads or
executes anything itself; it only talks to the service over D-Bus.

Then turn on "Vendor HID battery" in the extension preferences.

`install` copies the program to `~/.local/bin/ampere-hid` and writes a systemd
user unit and a D-Bus activation file. The unit is not enabled at login;
activation starts it when the extension asks, and it exits after five minutes
without requests (`AMPERE_HID_IDLE_EXIT`, `0` disables).

`--udev` also installs the udev rule that lets your user access the HID
devices. That needs root (`sudo`, or `pkexec` without a terminal) and is a
one-time step. Without the flag, `install` asks when the rule is missing or out
of date; `--no-udev` skips the question. The rule names exact product IDs, so
other devices on the same chip (keyboards, for example) stay untouched. If your
model is missing, see [CONTRIBUTING.md](CONTRIBUTING.md); to try a model on your
own machine, put a rule for it into a separate file in `/etc/udev/rules.d/`,
because the generated one is overwritten on update.

To remove everything again:

```sh
ampere-hid uninstall          # add --udev to remove the udev rule as well
```

From a checkout, use `python3 -m ampere_hid install` from the `daemon/`
directory instead; it builds the same single file.

### Supported devices

| Device | Transport | Verified |
| --- | --- | --- |
| Lamzu Maya X 8K (`373e:001e`, `001c`) | Aurora 65-byte feature report | yes |
| Lamzu Thorn (`3554:f50d`) | Compx 17-byte report | yes |
| Other Lamzu receivers (`3554`, `37b0`) | Aurora and Compx, whichever answers | not tested |
| ATK A9 Max Ultra 2.0 (`373b:11fe`) | Compx 17-byte report | yes |
| Other ATK/VXE/VGN and Compx rebrands (`373b`, `260d`, `33e4`) | Compx | not tested |
| MCHOSE A7 V2 Pro (`3837:100a`) | MCHOSE feature query | yes |
| Other MCHOSE receivers (`3837`, `5253`) | MCHOSE feature query | not tested |

Only the rows marked "yes" were checked on real hardware. If your model is
missing, run the command below. Also check whether its product ID is in the
output of `ampere-hid rules`; if it is not, the VID:PID that `lsusb` shows has
to be added (see [CONTRIBUTING.md](CONTRIBUTING.md)).

### If a device does not show up

```sh
ampere-hid diagnose
```

It shows every hidraw device, its driver and the raw answer. It only sends
battery queries and changes no settings. A sleeping mouse answers nothing; move
it and try again.

Devices in UPower do not need the service at all. Check them with `upower -e`.

`systemctl --user stop ampere-hid` does not keep the service down for long: the
next query from the extension starts it again. Use the preference switch to
hide the vendor devices instead.

## Requirements

* GNOME Shell 48–51
* Nothing extra for the extension itself
* For the optional service: Python 3.8 or newer with PyGObject (`python3-gi`)

## Contributing

Bug reports, new devices and patches are welcome. See
[CONTRIBUTING.md](CONTRIBUTING.md) for the layout, the driver interface and how
to bring up an unsupported mouse.

## Credits

The vendor protocols were reverse engineered by others and are documented in
their projects: [anany1702/lamzu-ctl](https://github.com/anany1702/lamzu-ctl)
(Compx and Aurora), [Fan4Metal/mouse_tray](https://github.com/Fan4Metal/mouse_tray)
(Compx/Nordic; the same report-8 layout is used by the Linux `hid-vxe-r1`
module), [HeyOkay/HaloBattery](https://github.com/HeyOkay/HaloBattery) and
[alexfrih/mchose-linux](https://github.com/alexfrih/mchose-linux) (MCHOSE).

## License

Ampere is released under the GNU General Public License, version 2 or later
(GPL-2.0-or-later). The full text is in [LICENSE](LICENSE).

Copyright © 2026 crian

This program is distributed in the hope that it will be useful, but WITHOUT ANY
WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A
PARTICULAR PURPOSE.
