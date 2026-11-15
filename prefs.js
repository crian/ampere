// SPDX-License-Identifier: GPL-2.0-or-later

import Adw from 'gi://Adw';
import Gdk from 'gi://Gdk';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences, gettext as _} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

import {fetchDevices, isPeripheral, deviceKey, deviceName} from './utils.js';
import {
    HID_BATTERY_BUS_NAME,
    HID_BATTERY_IFACE,
    HID_BATTERY_OBJECT_PATH,
    daemonTooOld,
    readHidBattery,
    readHidVersion,
} from './hidbattery.js';

const toHex = rgba =>
    `#${[rgba.red, rgba.green, rgba.blue].map(v => Math.round(v * 255).toString(16).padStart(2, '0')).join('')}`;

export default class AmperePrefs extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        const page = new Adw.PreferencesPage({
            title: _('General'),
            icon_name: 'preferences-system-symbolic',
        });
        window.add(page);

        // Everything below is built without waiting for D-Bus, so the window
        // opens immediately; the device list fills in when the answers arrive.
        let alive = true;
        window.connect('destroy', () => {
            alive = false;
        });

        // --- Placement ---
        const placement = new Adw.PreferencesGroup({title: _('Placement')});
        const values = ['separate', 'quick-settings'];
        const combo = new Adw.ComboRow({
            title: _('Position in the top bar'),
            subtitle: _('Own button next to the system menu, or between the other system icons'),
            model: new Gtk.StringList({
                strings: [_('Own button'), _('Inside the system icons')],
            }),
        });
        combo.selected = Math.max(0, values.indexOf(settings.get_string('placement')));
        combo.connect('notify::selected', () => settings.set_string('placement', values[combo.selected]));
        placement.add(combo);
        page.add(placement);

        // --- Switches ---
        const addSwitches = (title, rows) => {
            const group = new Adw.PreferencesGroup({title});
            for (const [key, rowTitle, subtitle] of rows) {
                const row = new Adw.SwitchRow({title: rowTitle, subtitle});
                settings.bind(key, row, 'active', Gio.SettingsBindFlags.DEFAULT);
                group.add(row);
            }
            page.add(group);
        };
        addSwitches(_('Panel indicator'), [
            ['show-icon', _('Show device icon'), _('Mouse, keyboard, ... icon')],
            ['show-bar', _('Show battery bar'), _('Vertical bar showing the charge level')],
            ['show-percentage', _('Show percentage'), _('Numeric charge level next to the bar')],
            ['colored-bar', _('Colored bar'), _('Off = monochrome, same color as the other panel icons')],
        ]);
        addSwitches(_('Info card'), [
            ['hover-card', _('Show on hover'), _('Show name, ring and percentage when hovering the indicator')],
        ]);

        // --- Vendor HID devices (not in UPower) ---
        const vendor = new Adw.PreferencesGroup({
            title: _('Vendor HID devices'),
            description: _('Optional: devices that do not report their battery to UPower, ' +
                'e.g. Lamzu mice. Requires the separate Ampere HID companion service.'),
        });
        const vendorRow = new Adw.SwitchRow({
            title: _('Show vendor HID battery'),
            subtitle: _('Provided by the companion service over D-Bus'),
        });
        settings.bind('vendor-hid-enabled', vendorRow, 'active', Gio.SettingsBindFlags.DEFAULT);
        vendor.add(vendorRow);

        // Shown while the service is missing or older than the extension. The
        // command is only copied, never executed.
        // Three lines read better in the row; the copy button copies them as is.
        const installCommand = [
            'cd "$(mktemp -d)"',
            'curl -fsSLo ampere-hid https://github.com/crian/ampere/releases/latest/download/ampere-hid',
            'chmod +x ampere-hid && ./ampere-hid install --udev',
        ].join('\n');
        const copyButton = new Gtk.Button({
            icon_name: 'edit-copy-symbolic',
            valign: Gtk.Align.CENTER,
            tooltip_text: _('Copy the command'),
            css_classes: ['flat'],
        });
        copyButton.connect('clicked', () => {
            const bytes = new GLib.Bytes(new TextEncoder().encode(installCommand));
            copyButton.get_clipboard().set_content(
                Gdk.ContentProvider.new_for_bytes('text/plain;charset=utf-8', bytes));
        });

        const warning = new Adw.ActionRow({
            title: _('Companion service not running'),
            subtitle: `<tt>${GLib.markup_escape_text(installCommand, -1)}</tt>`,
            use_markup: true,
            visible: false,
        });
        warning.add_suffix(copyButton);
        vendor.add(warning);
        // version: undefined = not asked, null = no Version property yet.
        const serviceWarning = (ok, version) => {
            const outdated = ok && daemonTooOld(version);
            if (!outdated) {
                warning.title = _('Companion service not running');
            } else if (version) {
                warning.title = _('Companion service %s is outdated')
                    .replace('%s', version);
            } else {
                warning.title = _('Companion service is outdated');
            }
            warning.visible = !ok || outdated;
        };
        page.add(vendor);

        // --- Level colors ---
        const colors = new Adw.PreferencesGroup({
            title: _('Level colors'),
            description: _('Used when "Colored bar" is on (bar and ring).'),
        });
        const addColor = (key, title) => {
            const button = new Gtk.ColorDialogButton({
                dialog: new Gtk.ColorDialog({with_alpha: false}),
                valign: Gtk.Align.CENTER,
            });
            const rgba = new Gdk.RGBA();
            rgba.parse(settings.get_string(key));
            button.rgba = rgba;
            button.connect('notify::rgba', () => settings.set_string(key, toHex(button.rgba)));
            const row = new Adw.ActionRow({title, activatable_widget: button});
            row.add_suffix(button);
            colors.add(row);
        };
        addColor('color-high', _('High charge'));
        addColor('color-medium', _('Medium charge'));
        addColor('color-low', _('Low charge'));
        addColor('color-charging', _('Charging'));

        const addSpin = (key, title, subtitle, lower, upper) => {
            const row = new Adw.SpinRow({
                title,
                subtitle,
                adjustment: new Gtk.Adjustment({lower, upper, step_increment: 1, page_increment: 5}),
            });
            settings.bind(key, row, 'value', Gio.SettingsBindFlags.DEFAULT);
            colors.add(row);
        };
        addSpin('threshold-medium', _('Medium up to (%)'),
            _('Above this value the high color is used'), 2, 99);
        addSpin('threshold-low', _('Low up to (%)'),
            _('At or below this value the low color is used'), 1, 98);
        page.add(colors);

        // The low threshold must stay below the medium one; keep it in range
        // when either value changes.
        const clampThresholds = () => {
            const medium = settings.get_int('threshold-medium');
            const low = settings.get_int('threshold-low');
            if (low >= medium)
                settings.set_int('threshold-low', Math.max(1, medium - 1));
        };
        settings.connect('changed::threshold-medium', clampThresholds);
        settings.connect('changed::threshold-low', clampThresholds);

        // --- Devices, filled asynchronously ---
        const group = new Adw.PreferencesGroup({
            title: _('Devices'),
            description: _('Choose which devices are shown. A device that UPower ' +
                'reports twice (cable and receiver) is merged automatically.'),
        });
        const loading = new Adw.ActionRow({title: _('Loading devices…')});
        group.add(loading);
        page.add(group);

        // The rows are rebuilt when the vendor switch changes, so enabling it
        // fills the list without closing and reopening the window.
        let rows = [];
        let signature = null;
        let running = false;
        let again = false;

        const fillDevices = async () => {
            const devices = new Map();
            try {
                for (const device of await fetchDevices(null)) {
                    if (isPeripheral(device))
                        devices.set(deviceKey(device), deviceName(device));
                }
            } catch (e) {
                console.error(`[ampere] cannot read UPower devices: ${e}`);
            }

            // The vendor service is only asked when it is enabled, so opening
            // the window neither starts the daemon nor blocks on it.
            if (settings.get_boolean('vendor-hid-enabled')) {
                try {
                    const result = await readHidBattery(null);
                    let version;
                    if (result.ok) {
                        try {
                            version = await readHidVersion(null);
                        } catch (e) {
                            console.error(`[ampere] cannot read the service version: ${e}`);
                        }
                    }
                    serviceWarning(result.ok, version);
                    for (const item of result.items)
                        devices.set(item.key, item.name);
                } catch (e) {
                    console.error(`[ampere] cannot read vendor HID devices: ${e}`);
                }
            } else {
                serviceWarning(true);
            }

            if (!alive)
                return;

            const hidden = new Set(settings.get_strv('hidden-devices'));
            for (const key of hidden) {
                if (!devices.has(key))
                    devices.set(key, _('%s (not connected)').replace('%s', key));
            }

            // A Changed on every new percentage would otherwise rebuild the
            // rows while the user is clicking in them.
            const current = [...devices].map(([key, name]) => `${key}\t${name}`).join('\n') +
                `\n--\n${[...hidden].join('\n')}`;
            if (current === signature && rows.length)
                return;
            signature = current;

            if (loading.get_parent() === group)
                group.remove(loading);
            for (const row of rows)
                group.remove(row);
            rows = [];

            if (!devices.size) {
                const empty = new Adw.ActionRow({
                    title: _('No devices found'),
                    subtitle: _('Check "upower -e"'),
                });
                group.add(empty);
                rows.push(empty);
                return;
            }

            for (const [key, name] of devices) {
                const row = new Adw.SwitchRow({title: name, subtitle: key, active: !hidden.has(key)});
                row.connect('notify::active', () => {
                    const list = new Set(settings.get_strv('hidden-devices'));
                    if (row.active)
                        list.delete(key);
                    else
                        list.add(key);
                    settings.set_strv('hidden-devices', [...list]);
                });
                group.add(row);
                rows.push(row);
            }
        };

        const refreshDevices = async () => {
            if (running) {
                again = true;
                return;
            }
            running = true;
            try {
                do {
                    again = false;
                    await fillDevices();
                } while (again && alive);
            } finally {
                running = false;
            }
        };

        const vendorSettingsId = settings.connect('changed::vendor-hid-enabled', () => {
            refreshDevices().catch(e =>
                console.error(`[ampere] cannot refresh the device list: ${e}`));
        });
        window.connect('destroy', () => settings.disconnect(vendorSettingsId));

        // A freshly started service answers with an empty cache first; the
        // Changed it emits after its first sweep fills the list.
        const changedId = Gio.DBus.session.signal_subscribe(
            HID_BATTERY_BUS_NAME, HID_BATTERY_IFACE, 'Changed', HID_BATTERY_OBJECT_PATH, null,
            Gio.DBusSignalFlags.NONE, () => {
                if (settings.get_boolean('vendor-hid-enabled')) {
                    refreshDevices().catch(e =>
                        console.error(`[ampere] cannot refresh the device list: ${e}`));
                }
            });
        window.connect('destroy', () => Gio.DBus.session.signal_unsubscribe(changedId));

        refreshDevices().catch(e => console.error(`[ampere] cannot fill the device list: ${e}`));
    }
}
