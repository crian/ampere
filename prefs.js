// SPDX-License-Identifier: GPL-2.0-or-later

import Adw from 'gi://Adw';
import Gdk from 'gi://Gdk';
import Gio from 'gi://Gio';
import GObject from 'gi://GObject';
import Gtk from 'gi://Gtk';

import {ExtensionPreferences} from 'resource:///org/gnome/Shell/Extensions/js/extensions/prefs.js';

import {fetchDevicesSync, isPeripheral, deviceKey, deviceName} from './utils.js';
import {readHidBatterySync} from './hidbattery.js';

const toHex = rgba =>
    `#${[rgba.red, rgba.green, rgba.blue].map(v => Math.round(v * 255).toString(16).padStart(2, '0')).join('')}`;

export default class AmperePrefs extends ExtensionPreferences {
    fillPreferencesWindow(window) {
        const settings = this.getSettings();
        const vendorHid = readHidBatterySync();
        const page = new Adw.PreferencesPage({title: 'General', icon_name: 'preferences-system-symbolic'});
        window.add(page);

        // --- Placement ---
        const placement = new Adw.PreferencesGroup({title: 'Placement'});
        const values = ['separate', 'quick-settings'];
        const combo = new Adw.ComboRow({
            title: 'Position in the top bar',
            subtitle: 'Own button next to the system menu, or between the other system icons',
            model: new Gtk.StringList({strings: ['Own button', 'Inside the system icons']}),
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
        addSwitches('Panel indicator', [
            ['show-icon', 'Show device icon', 'Mouse, keyboard, ... icon'],
            ['show-bar', 'Show battery bar', 'Vertical bar showing the charge level'],
            ['show-percentage', 'Show percentage', 'Numeric charge level next to the bar'],
            ['colored-bar', 'Colored bar', 'Off = monochrome, same color as the other panel icons'],
        ]);
        addSwitches('Info card', [
            ['hover-card', 'Show on hover', 'Show name, ring and percentage when hovering the indicator'],
        ]);

        // --- Vendor HID devices (not in UPower) ---
        // Only shown when the service answers or the user enabled it.
        if (vendorHid.ok || settings.get_boolean('vendor-hid-enabled')) {
            const vendor = new Adw.PreferencesGroup({
                title: 'Vendor HID devices',
                description: 'Optional: devices that do not report their battery to UPower, ' +
                    'e.g. Lamzu mice. Requires the separate Ampere HID companion service.',
            });
            const vendorRow = new Adw.SwitchRow({
                title: 'Show vendor HID battery',
                subtitle: 'Provided by the companion service over D-Bus',
            });
            settings.bind('vendor-hid-enabled', vendorRow, 'active', Gio.SettingsBindFlags.DEFAULT);
            vendor.add(vendorRow);

            if (!vendorHid.ok) {
                const warning = new Adw.ActionRow({
                    title: 'Companion service not running',
                    subtitle: 'Install the Ampere HID daemon (see README)',
                });
                vendorRow.bind_property('active', warning, 'visible',
                    GObject.BindingFlags.SYNC_CREATE);
                vendor.add(warning);
            }
            page.add(vendor);
        }

        // --- Level colors ---
        const colors = new Adw.PreferencesGroup({
            title: 'Level colors',
            description: 'Used when "Colored bar" is on (bar and ring).',
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
        addColor('color-high', 'High charge');
        addColor('color-medium', 'Medium charge');
        addColor('color-low', 'Low charge');
        addColor('color-charging', 'Charging');

        const addSpin = (key, title, subtitle, lower, upper) => {
            const row = new Adw.SpinRow({
                title,
                subtitle,
                adjustment: new Gtk.Adjustment({lower, upper, step_increment: 1, page_increment: 5}),
            });
            settings.bind(key, row, 'value', Gio.SettingsBindFlags.DEFAULT);
            colors.add(row);
        };
        addSpin('threshold-medium', 'Medium up to (%)', 'Above this value the high color is used', 2, 99);
        addSpin('threshold-low', 'Low up to (%)', 'At or below this value the low color is used', 1, 98);
        page.add(colors);

        // --- Devices (own process, so a blocking call is fine here) ---
        const devices = new Map();
        try {
            for (const d of fetchDevicesSync()) {
                if (isPeripheral(d))
                    devices.set(deviceKey(d), deviceName(d));
            }
        } catch (e) {
            console.error(`[ampere] cannot read UPower devices: ${e}`);
        }
        for (const item of vendorHid.items)
            devices.set(item.key, item.name);
        const hidden = new Set(settings.get_strv('hidden-devices'));
        for (const key of hidden) {
            if (!devices.has(key))
                devices.set(key, `${key} (not connected)`);
        }

        const group = new Adw.PreferencesGroup({
            title: 'Devices',
            description: 'Choose which devices are shown. A device reported twice (cable and receiver) is merged automatically.',
        });
        if (!devices.size)
            group.add(new Adw.ActionRow({title: 'No devices found', subtitle: 'Check "upower -e"'}));
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
        }
        page.add(group);
    }
}
