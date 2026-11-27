// SPDX-License-Identifier: GPL-2.0-or-later

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

import {AmpereIndicator} from './indicator.js';
import {
    ICONS,
    STATE,
    UPOWER,
    deviceKey,
    deviceName,
    displayName,
    fetchDevices,
    isCancelled,
    isPeripheral,
    readDeviceNames,
} from './utils.js';
import {HID_BATTERY_BUS_NAME, HID_BATTERY_IFACE, HID_BATTERY_OBJECT_PATH, readHidBattery} from './hidbattery.js';

const POLL_SECONDS = 30;
// Margin above threshold-low before a device can notify again without charging.
const LOW_NOTIFY_HYSTERESIS = 5;

export default class AmpereExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._cancellable = new Gio.Cancellable();
        this._items = [];
        this._vendorItems = [];
        this._lastSignature = null;
        this._notifiedLow = new Set();
        this._notificationSource = null;
        this._notificationSourceDestroyId = 0;
        this._polling = false;
        this._again = false;

        this._indicator = new AmpereIndicator(this._settings, () => this.openPreferences());
        Main.panel.addToStatusArea(this.uuid, this._indicator, 0, 'right');
        this._settingsId = this._settings.connect('changed', (_settings, key) => {
            if (key === 'vendor-hid-enabled') {
                this._lastSignature = null;
                this._poll();
            } else {
                this._render(true);
            }
        });

        // The companion service emits Changed when a value differs.
        this._vendorSignalId = Gio.DBus.session.signal_subscribe(
            HID_BATTERY_BUS_NAME, HID_BATTERY_IFACE, 'Changed', HID_BATTERY_OBJECT_PATH, null,
            Gio.DBusSignalFlags.NONE, () => this._onVendorHidChanged());

        // UPower over D-Bus, asynchronous so the shell never blocks.
        const bus = Gio.DBus.system;
        const onChange = () => this._poll();
        this._subIds = ['DeviceAdded', 'DeviceRemoved'].map(name =>
            bus.signal_subscribe(UPOWER.BUS, UPOWER.BUS, name, UPOWER.ROOT, null,
                Gio.DBusSignalFlags.NONE, onChange));

        this._poll();
        this._timerId = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, POLL_SECONDS, () => {
            this._poll();
            return GLib.SOURCE_CONTINUE;
        });
    }

    disable() {
        this._cancellable.cancel();
        this._cancellable = null;

        if (this._timerId) {
            GLib.Source.remove(this._timerId);
            this._timerId = 0;
        }
        if (this._vendorSignalId) {
            Gio.DBus.session.signal_unsubscribe(this._vendorSignalId);
            this._vendorSignalId = 0;
        }
        for (const id of this._subIds)
            Gio.DBus.system.signal_unsubscribe(id);
        this._subIds = null;

        if (this._settingsId) {
            this._settings.disconnect(this._settingsId);
            this._settingsId = 0;
        }
        this._settings = null;

        if (this._notificationSource) {
            if (this._notificationSourceDestroyId) {
                this._notificationSource.disconnect(this._notificationSourceDestroyId);
                this._notificationSourceDestroyId = 0;
            }
            this._notificationSource.destroy();
            this._notificationSource = null;
        }
        this._notifiedLow = null;

        this._indicator.destroy();
        this._indicator = null;
        this._items = null;
        this._vendorItems = null;
    }

    async _poll() {
        if (this._polling) {
            this._again = true;
            return;
        }
        this._polling = true;
        try {
            const devices = await fetchDevices(this._cancellable);
            if (!this._indicator)
                return;
            this._items = this._collect(devices);
            await this._pollVendorHid();
            this._render(false);
        } catch (e) {
            if (!isCancelled(e))
                console.error(`[ampere] refresh failed: ${e}`);
        } finally {
            this._polling = false;
            if (this._again && this._indicator) {
                this._again = false;
                this._poll();
            }
        }
    }

    // The same physical device (cable + receiver) is merged by key.
    _collect(devices) {
        const byKey = new Map();
        for (const p of devices) {
            if (!isPeripheral(p))
                continue;
            const item = {
                key: deviceKey(p),
                name: deviceName(p),
                icon: ICONS.get(p.Type) ?? 'battery-symbolic',
                pct: Math.round(p.Percentage ?? 0),
                state: p.State ?? STATE.UNKNOWN,
            };
            const prev = byKey.get(item.key);
            if (!prev || (prev.state === STATE.UNKNOWN && item.state !== STATE.UNKNOWN))
                byKey.set(item.key, item);
        }
        return [...byKey.values()];
    }

    // Vendor HID devices are not in UPower; the companion service provides them.
    async _pollVendorHid() {
        if (!this._indicator || !this._settings.get_boolean('vendor-hid-enabled')) {
            this._vendorItems = [];
            return;
        }

        try {
            const {items} = await readHidBattery(this._cancellable);
            if (!this._indicator)
                return;
            this._vendorItems = items;
        } catch (e) {
            if (!this._indicator)
                return;
            this._vendorItems = [];
            if (!isCancelled(e))
                console.error(`[ampere] vendor HID service query failed: ${e}`);
        }
    }

    async _onVendorHidChanged() {
        if (!this._indicator)
            return;
        await this._pollVendorHid();
        if (!this._indicator)
            return;
        this._render(false);
    }

    _render(force) {
        if (!this._indicator)
            return;
        const hidden = this._settings.get_strv('hidden-devices');
        const names = readDeviceNames(this._settings);
        const items = [...this._items, ...this._vendorItems]
            .filter(i => !hidden.includes(i.key))
            .map(i => ({...i, name: displayName(i.key, i.name, names)}));
        const signature = JSON.stringify(items);
        if (!force && signature === this._lastSignature)
            return;
        this._lastSignature = signature;
        this._indicator.update(items);
        this._checkLowBattery(items);
    }

    _checkLowBattery(items) {
        if (!this._settings.get_boolean('notify-low')) {
            this._notifiedLow.clear();
            return;
        }
        const threshold = this._settings.get_int('threshold-low');
        for (const item of items) {
            const charging = item.state === STATE.CHARGING || item.state === STATE.FULLY_CHARGED;
            if (charging || item.pct > threshold + LOW_NOTIFY_HYSTERESIS) {
                this._notifiedLow.delete(item.key);
                continue;
            }
            if (item.pct <= threshold && !this._notifiedLow.has(item.key)) {
                this._notifiedLow.add(item.key);
                this._notifyLow(item);
            }
        }
    }

    _ensureNotificationSource() {
        if (!this._notificationSource) {
            this._notificationSource = new MessageTray.Source({
                title: _('Ampere'),
                iconName: 'battery-caution-symbolic',
            });
            this._notificationSourceDestroyId = this._notificationSource.connect('destroy', () => {
                this._notificationSource = null;
                this._notificationSourceDestroyId = 0;
            });
            Main.messageTray.add(this._notificationSource);
        }
        return this._notificationSource;
    }

    _notifyLow(item) {
        const source = this._ensureNotificationSource();
        const notification = new MessageTray.Notification({
            source,
            title: _('Battery low'),
            body: `${item.name}: ${item.pct}%`,
            iconName: item.icon,
            urgency: MessageTray.Urgency.NORMAL,
        });
        source.addNotification(notification);
    }
}
