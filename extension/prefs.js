// SPDX-License-Identifier: GPL-2.0-or-later

import Adw from 'gi://Adw';
import Gdk from 'gi://Gdk';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences, gettext as _} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

import {
    deviceKey,
    deviceName,
    fetchDevices,
    isCancelled,
    isPeripheral,
    readDeviceNames,
    writeDeviceNames,
} from './utils.js';
import {
    HID_BATTERY_BUS_NAME,
    HID_BATTERY_IFACE,
    HID_BATTERY_OBJECT_PATH,
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

        const cancellable = new Gio.Cancellable();

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
        addSwitches(_('Notifications'), [
            ['notify-low', _('Low battery notification'),
                _('Notify once per discharge cycle when a device drops to the low threshold')],
        ]);

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

        // Only copied, never executed.
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
            window.add_toast(new Adw.Toast({title: _('Command copied'), timeout: 2}));
        });

        const service = new Adw.ActionRow({
            use_markup: true,
            visible: false,
        });
        service.add_suffix(copyButton);
        vendor.add(service);

        const showService = (ok, version) => {
            service.visible = true;
            service.title = ok ? _('Companion service') : _('Companion service not running');
            if (!ok)
                service.subtitle = `<tt>${GLib.markup_escape_text(installCommand, -1)}</tt>`;
            else if (version)
                service.subtitle = _('Version %s').replace('%s', GLib.markup_escape_text(version, -1));
            else
                service.subtitle = _('Version unknown');
        };
        page.add(vendor);

        const colorKeys = [
            'color-high',
            'color-medium',
            'color-low',
            'color-charging',
            'threshold-medium',
            'threshold-low',
        ];
        const resetColors = new Gtk.Button({
            child: new Adw.ButtonContent({
                icon_name: 'edit-undo-symbolic',
                label: _('Reset'),
            }),
            css_classes: ['flat'],
            valign: Gtk.Align.CENTER,
            tooltip_text: _('Reset colors and thresholds to defaults'),
        });
        const colors = new Adw.PreferencesGroup({
            title: _('Level colors'),
            description: _('Used when "Colored bar" is on (bar and ring).'),
            header_suffix: resetColors,
        });
        const colorSyncers = [];
        const addColor = (key, title) => {
            const button = new Gtk.ColorDialogButton({
                dialog: new Gtk.ColorDialog({with_alpha: false}),
                valign: Gtk.Align.CENTER,
            });
            const sync = () => {
                const rgba = new Gdk.RGBA();
                rgba.parse(settings.get_string(key));
                button.rgba = rgba;
            };
            sync();
            colorSyncers.push(sync);
            button.connect('notify::rgba', () => {
                const hex = toHex(button.rgba);
                if (hex !== settings.get_string(key).toLowerCase())
                    settings.set_string(key, hex);
            });
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

        const updateResetSensitive = () => {
            resetColors.sensitive = colorKeys.some(key => settings.get_user_value(key) !== null);
        };
        updateResetSensitive();
        resetColors.connect('clicked', () => {
            // Reset threshold-medium before threshold-low so clampThresholds
            // cannot clamp the default low value against a smaller medium one.
            for (const key of colorKeys)
                settings.reset(key);
            for (const sync of colorSyncers)
                sync();
            updateResetSensitive();
        });
        const colorChangedIds = colorKeys.map(key =>
            settings.connect(`changed::${key}`, updateResetSensitive));

        const clampThresholds = () => {
            const medium = settings.get_int('threshold-medium');
            const low = settings.get_int('threshold-low');
            if (low >= medium)
                settings.set_int('threshold-low', Math.max(1, medium - 1));
        };
        const thresholdMedId = settings.connect('changed::threshold-medium', clampThresholds);
        const thresholdLowId = settings.connect('changed::threshold-low', clampThresholds);

        const group = new Adw.PreferencesGroup({
            title: _('Devices'),
            description: _('Choose which devices are shown or give them custom names. ' +
                'A device that UPower reports twice (cable and receiver) is merged automatically.'),
        });
        const loading = new Adw.ActionRow({title: _('Loading devices…')});
        group.add(loading);
        page.add(group);

        let rows = [];
        let signature = null;
        let running = false;
        let again = false;

        const fillDevices = async () => {
            const devices = new Map();
            try {
                for (const device of await fetchDevices(cancellable)) {
                    if (isPeripheral(device))
                        devices.set(deviceKey(device), deviceName(device));
                }
            } catch (e) {
                if (isCancelled(e))
                    return;
                console.error(`[ampere] cannot read UPower devices: ${e}`);
            }

            if (cancellable.is_cancelled())
                return;

            // Only asked when enabled, so opening the window never starts the daemon.
            if (settings.get_boolean('vendor-hid-enabled')) {
                try {
                    const result = await readHidBattery(cancellable);
                    if (cancellable.is_cancelled())
                        return;
                    const version = result.ok ? await readHidVersion(cancellable) : null;
                    if (cancellable.is_cancelled())
                        return;
                    showService(result.ok, version);
                    for (const item of result.items)
                        devices.set(item.key, item.name);
                } catch (e) {
                    if (isCancelled(e))
                        return;
                    console.error(`[ampere] cannot read vendor HID devices: ${e}`);
                    showService(false, null);
                }
            } else {
                service.visible = false;
            }

            if (cancellable.is_cancelled())
                return;

            const hidden = new Set(settings.get_strv('hidden-devices'));
            const names = readDeviceNames(settings);
            const disconnected = key => _('%s (not connected)').replace('%s', key);
            for (const key of new Set([...hidden, ...Object.keys(names)])) {
                if (!devices.has(key))
                    devices.set(key, disconnected(key));
            }

            // Skip the rebuild when nothing changed, so rows stay usable while clicking.
            const current = JSON.stringify([[...devices], [...hidden]]);
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

            for (const [key, detectedName] of devices) {
                const custom = names[key]?.trim() ?? '';
                const disconnectedLabel = disconnected(key);
                const row = new Adw.ExpanderRow({use_markup: false});

                const toggle = new Gtk.Switch({
                    active: !hidden.has(key),
                    valign: Gtk.Align.CENTER,
                    tooltip_text: _('Show in panel'),
                });
                toggle.connect('notify::active', () => {
                    const list = new Set(settings.get_strv('hidden-devices'));
                    if (toggle.active)
                        list.delete(key);
                    else
                        list.add(key);
                    settings.set_strv('hidden-devices', [...list]);
                });
                row.add_suffix(toggle);

                const entry = new Adw.EntryRow({
                    title: _('Custom name'),
                    text: custom,
                    show_apply_button: true,
                });
                const clearBtn = new Gtk.Button({
                    icon_name: 'edit-clear-symbolic',
                    valign: Gtk.Align.CENTER,
                    tooltip_text: _('Reset name'),
                    css_classes: ['flat'],
                    visible: Boolean(custom),
                });
                entry.add_suffix(clearBtn);
                row.add_row(entry);

                const updateHeader = alias => {
                    row.title = alias || detectedName;
                    if (!alias)
                        row.subtitle = key;
                    else if (detectedName === disconnectedLabel)
                        row.subtitle = detectedName;
                    else
                        row.subtitle = `${detectedName} · ${key}`;
                    clearBtn.visible = Boolean(alias);
                };
                const saveName = raw => {
                    const alias = raw.trim();
                    const map = readDeviceNames(settings);
                    if (alias)
                        map[key] = alias;
                    else
                        delete map[key];
                    writeDeviceNames(settings, map);
                    if (entry.text !== alias) {
                        entry.show_apply_button = false;
                        entry.text = alias;
                        entry.show_apply_button = true;
                    }
                    updateHeader(alias);
                };
                entry.connect('apply', () => saveName(entry.text));
                clearBtn.connect('clicked', () => saveName(''));

                updateHeader(custom);
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
                } while (again && !cancellable.is_cancelled());
            } catch (e) {
                if (!isCancelled(e))
                    console.error(`[ampere] cannot refresh the device list: ${e}`);
            } finally {
                running = false;
            }
        };

        const vendorSettingsId = settings.connect('changed::vendor-hid-enabled', () => {
            refreshDevices();
        });

        const changedId = Gio.DBus.session.signal_subscribe(
            HID_BATTERY_BUS_NAME, HID_BATTERY_IFACE, 'Changed', HID_BATTERY_OBJECT_PATH, null,
            Gio.DBusSignalFlags.NONE, () => {
                if (settings.get_boolean('vendor-hid-enabled'))
                    refreshDevices();
            });

        window.connect('destroy', () => {
            cancellable.cancel();
            for (const id of colorChangedIds)
                settings.disconnect(id);
            settings.disconnect(thresholdMedId);
            settings.disconnect(thresholdLowId);
            settings.disconnect(vendorSettingsId);
            Gio.DBus.session.signal_unsubscribe(changedId);
        });

        refreshDevices();
    }
}
