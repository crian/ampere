#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
set -eu

libdir=$HOME/.local/lib/ampere
unitdir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
dbusdir=${XDG_DATA_HOME:-$HOME/.local/share}/dbus-1/services

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user disable --now ampere-hid.service 2>/dev/null || true
fi

rm -f "$unitdir/ampere-hid.service"
rm -f "$dbusdir/org.gnome.shell.extensions.ampere.HidBattery.service"
rm -rf "$libdir/ampere_hid"
rm -f "$libdir/ampere-hid"
rmdir "$libdir" 2>/dev/null || true

if command -v systemctl >/dev/null 2>&1; then
    systemctl --user daemon-reload
    # The running bus keeps the removed activation file until it is reloaded.
    systemctl --user reload dbus-broker.service 2>/dev/null ||
        systemctl --user reload dbus.service 2>/dev/null || true
fi

echo "Ampere vendor HID battery daemon removed."
echo "The udev rule in /etc/udev/rules.d/ must be removed manually (sudo)."
