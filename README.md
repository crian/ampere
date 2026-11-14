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

1. Install the udev rule so your user may access the HID devices. This needs
   root and is a one-time step:

   ```sh
   sudo install -m644 daemon/70-ampere-hid.rules /etc/udev/rules.d/
   sudo udevadm control --reload-rules
   sudo udevadm trigger --subsystem-match=hidraw
   ```

2. Install the service:

   ```sh
   ./daemon/install.sh
   ```

3. Turn on "Vendor HID battery" in the extension preferences.

The installer writes `~/.local/lib/ampere/`, a systemd user unit
(`ampere-hid.service`) and a D-Bus activation file. The service starts the
first time the extension asks for data and stops with your session. To remove
it again:

```sh
./daemon/uninstall.sh
```

The udev rule has to be deleted by hand, since it belongs to root.

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
missing, run the command below.

### If a device does not show up

```sh
python3 ~/.local/lib/ampere/ampere-hid --diagnose
```

It lists every hidraw device, the driver that claims it and the raw answer. It
only reads, so it is safe to run on unknown hardware. A sleeping mouse answers
nothing; move it and try again.

Devices in UPower do not need the service at all. Check them with `upower -e`.

`systemctl --user stop ampere-hid` does not keep the service down for long: the
next query from the extension starts it again. Use the preference switch to
hide the vendor devices instead.

## Requirements

* GNOME Shell 48–51
* Nothing extra for the extension itself
* For the optional service: Python 3 with PyGObject (`python3-gi`)

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
