# Ampere

Battery level of wireless mice and keyboards in the GNOME top bar.

Devices that UPower reports work out of the box. Mice that only expose their
battery through vendor HID reports (Lamzu, MCHOSE, ATK/Compx) need an optional
companion service, which the extension talks to over D-Bus.

![Icon, bar, percentage and the hover card](screenshots/hover-card.png)

## Features

* Icon, bar and percentage per device in the panel
* Colour coding by charge level and charging state, with configurable thresholds
* Optional low-battery notification (once per discharge cycle)
* Hover card with ring, name, percentage and state
* Own button, or placed inside the Quick Settings icon row
* Individual devices can be hidden or renamed

## Installation

Install it from
[extensions.gnome.org](https://extensions.gnome.org/extension/11770/ampere/) and
enable it in the Extensions app.

From source:

```sh
git clone https://github.com/crian/ampere.git
cd ampere
make install
```

`make install` compiles the schema, packs the extension from `extension/` and
installs it with `gnome-extensions`. `make help` lists the other targets.

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

To check the download, fetch `ampere-hid.sha256` from the same release and run
`sha256sum -c ampere-hid.sha256` next to the file. That catches a broken or
truncated download; it does not prove that the release itself is genuine.

This is a one-time command for you to run; `ampere-hid status` afterwards shows
what the service sees. The extension never downloads or executes anything
itself; it only talks to the service over D-Bus.

Then turn on "Vendor HID battery" in the extension preferences. The preferences
also tell you when the service is older than the extension expects; run the
same commands again to update it.

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

From a checkout, use `make daemon-install` (add `UDEV=1` for the udev rule)
instead; it builds the same single file.

On NixOS, take the output of `ampere-hid rules` into
`services.udev.extraRules`; the service itself has to be packaged (for example
a home-manager module), which is out of scope here. Without systemd, `install`
still writes the files but starts nothing: run `ampere-hid run` from your
session startup instead.

### Supported devices

| Device | Transport | Verified |
| --- | --- | --- |
| Lamzu Maya X 8K (`373e:001e`, `001c`) | Aurora 65-byte feature report | yes |
| Lamzu Thorn (`3554:f50d`, on the cable `3554:f50f`) | Compx 17-byte report | yes |
| Other Lamzu receivers (`3554`, `37b0`) | Aurora and Compx, whichever answers | not tested |
| ATK A9 Max Ultra 2.0 (`373b:11fe`, on the cable `373b:1270`) | Compx 17-byte report | yes |
| Other ATK/VXE/VGN and Compx rebrands (`373b`, `260d`, `33e4`) | Compx | not tested |
| MCHOSE A7 V2 Pro (`3837:100a`, on the cable `3837:4018`) | MCHOSE feature query | yes |
| Other MCHOSE receivers (`3837`, `5253`) | MCHOSE feature query | not tested |

Only the rows marked "yes" were checked on real hardware. If your model is
missing, run the command below. Also check whether its product ID is in the
output of `ampere-hid rules`; if it is not, the VID:PID that `lsusb` shows has
to be added (see [CONTRIBUTING.md](CONTRIBUTING.md)).

### If a mouse is not recognized

1. **What does the service see?**

   ```sh
   ampere-hid status
   ```

   Version, service state, whether the udev rule matches this version and the
   devices it returns. "inactive" is normal: D-Bus starts the service on demand.

2. **What does the device answer?**

   ```sh
   ampere-hid diagnose
   ```

   Every hidraw device with its driver and the raw answer. It only sends battery
   queries and changes no settings. A sleeping mouse answers nothing; move it
   and try again. A node it cannot open carries a `reason`: either the rule is
   missing or its product ID is not in the table.

3. **Is the product ID in the rule?**

   ```sh
   lsusb -d <vendor>:
   ampere-hid rules | grep -i idProduct
   ```

   If `lsusb` shows a product ID that the rule does not list, add it (see
   [CONTRIBUTING.md](CONTRIBUTING.md)) or open an issue with that line.

If all of that looks right and the panel stays empty, open an issue with the
output of both commands and the model name.

Devices reported by UPower do not need the service at all; check them with
`upower -e`. A mouse connected by cable and receiver at the same time appears
twice: the two connections report different serial numbers, so they are two
devices as far as the service can tell.

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
