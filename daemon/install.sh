#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
set -eu

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python=$(command -v python3)
libdir=$HOME/.local/lib/ampere
unitdir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
dbusdir=${XDG_DATA_HOME:-$HOME/.local/share}/dbus-1/services

install -d "$libdir" "$unitdir" "$dbusdir"

# Replace the package, so dropped drivers or stale caches do not linger.
rm -rf "$libdir/ampere_hid"
install -d "$libdir/ampere_hid/drivers"
for file in "$here"/ampere_hid/*.py; do
    install -m644 "$file" "$libdir/ampere_hid/"
done
for file in "$here"/ampere_hid/drivers/*.py; do
    install -m644 "$file" "$libdir/ampere_hid/drivers/"
done
install -m755 "$here/ampere-hid" "$libdir/ampere-hid"

sed -e "s|@PYTHON@|$python|g" -e "s|@LIBDIR@|$libdir|g" \
    "$here/ampere-hid.service.in" > "$unitdir/ampere-hid.service"
sed -e "s|@PYTHON@|$python|g" -e "s|@LIBDIR@|$libdir|g" \
    "$here/org.gnome.shell.extensions.ampere.HidBattery.service.in" \
    > "$dbusdir/org.gnome.shell.extensions.ampere.HidBattery.service"

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
    # The running session bus only learns about new activation files on reload.
    systemctl --user reload dbus-broker.service 2>/dev/null ||
        systemctl --user reload dbus.service 2>/dev/null || true
    # Activation on demand is enough: no autostart at login. Older installs may
    # have enabled the unit, so drop that.
    systemctl --user disable ampere-hid.service 2>/dev/null || true
    systemctl --user start ampere-hid.service
else
    echo "systemd not found; start the daemon manually:"
    echo "  $python $libdir/ampere-hid"
fi

cat <<EOF
Ampere vendor HID battery daemon installed.

If a device is not detected, install the udev rule once:
  sudo install -m644 "$here/70-ampere-hid.rules" /etc/udev/rules.d/
  sudo udevadm control --reload-rules
  sudo udevadm trigger --subsystem-match=hidraw

Inspect devices without querying them:
  $python $libdir/ampere-hid --probe

Diagnose a device (never writes to it):
  $python $libdir/ampere-hid --diagnose
  $python $libdir/ampere-hid --sniff 3

Read the battery once:
  $python $libdir/ampere-hid --once
EOF
