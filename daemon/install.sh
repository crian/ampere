#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-2.0-or-later
set -eu

usage() {
    cat <<'EOF'
usage: install.sh [--udev]

  --udev   also install the udev rule (runs sudo)
EOF
}

here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
libdir=$HOME/.local/lib/ampere
unitdir=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
dbusdir=${XDG_DATA_HOME:-$HOME/.local/share}/dbus-1/services
rules=/etc/udev/rules.d/70-ampere-hid.rules

with_udev=0
while [ $# -gt 0 ]; do
    case "$1" in
        --udev) with_udev=1 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

if ! python=$(command -v python3); then
    echo "python3 is required but was not found in PATH." >&2
    exit 1
fi

if ! "$python" -c 'import gi
gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib' 2>/dev/null; then
    cat >&2 <<'EOF'
PyGObject (the "gi" module) is missing, so the service cannot start.
Install it first:
  Fedora:         sudo dnf install python3-gobject
  Debian/Ubuntu:  sudo apt install python3-gi
  Arch:           sudo pacman -S python-gobject
EOF
    exit 1
fi

# Offer the udev rule when it is missing, but only if we can ask (a terminal).
if [ "$with_udev" -eq 0 ] && [ ! -f "$rules" ] && [ -t 0 ]; then
    printf 'Install the udev rule now (needs sudo)? [y/N] '
    read -r answer || answer=
    case "$answer" in
        y|Y|yes|YES) with_udev=1 ;;
    esac
fi

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

if [ "$with_udev" -eq 1 ]; then
    sudo install -m644 "$here/70-ampere-hid.rules" "$rules"
    sudo udevadm control --reload-rules
    sudo udevadm trigger --subsystem-match=hidraw
fi

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

Read the battery once:
  $python $libdir/ampere-hid --once

Inspect devices and their answers:
  $python $libdir/ampere-hid --probe      list HID devices and their driver
  $python $libdir/ampere-hid --diagnose   query the battery, print the raw answers
  $python $libdir/ampere-hid --sniff 3    dump input reports, no queries
EOF

if [ "$with_udev" -eq 0 ]; then
    cat <<EOF

If a device is not detected, install the udev rule once:
  sudo install -m644 "$here/70-ampere-hid.rules" /etc/udev/rules.d/
  sudo udevadm control --reload-rules
  sudo udevadm trigger --subsystem-match=hidraw
or run this script again with --udev.
EOF
fi
