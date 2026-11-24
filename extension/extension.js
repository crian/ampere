// SPDX-License-Identifier: GPL-2.0-or-later

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as MessageTray from 'resource:///org/gnome/shell/ui/messageTray.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import {Extension, gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

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

const S = STATE;
const POLL_SECONDS = 30;
// Margin above threshold-low before a device can notify again without charging.
const LOW_NOTIFY_HYSTERESIS = 5;

const stateText = state => {
    switch (state) {
    case S.CHARGING: return _('Charging');
    case S.DISCHARGING: return _('Discharging');
    case S.FULLY_CHARGED: return _('Fully charged');
    default: return '';
    }
};

const hexToRgb = hex => {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex ?? '');
    if (!m)
        return [1, 1, 1];
    const n = parseInt(m[1], 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
};

// Foreground color of the theme at this actor, i.e. what the other panel icons use.
const themeColor = actor => {
    try {
        const c = actor.get_theme_node().get_foreground_color();
        return [c.red / 255, c.green / 255, c.blue / 255, c.alpha / 255];
    } catch {
        return [1, 1, 1, 1];
    }
};

const roundRect = (cr, x, y, w, h, r) => {
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    cr.newSubPath();
    cr.arc(x + w - r, y + r, r, -Math.PI / 2, 0);
    cr.arc(x + w - r, y + h - r, r, 0, Math.PI / 2);
    cr.arc(x + r, y + h - r, r, Math.PI / 2, Math.PI);
    cr.arc(x + r, y + r, r, Math.PI, 3 * Math.PI / 2);
    cr.closePath();
};

const Indicator = GObject.registerClass(
class AmpereIndicator extends PanelMenu.Button {
    _init(settings, onActivate) {
        // dontCreateMenu = true: no popup menu, no input grab, no hover loops.
        super._init(0.5, 'Ampere', true);
        this._settings = settings;
        this._onActivate = onActivate;
        this._hasItems = false;
        this._qsBox = null;
        this._qsChildId = 0;
        this.add_style_class_name('ampere-button');

        this._box = new St.BoxLayout({
            style_class: 'ampere-panel-box',
            y_align: Clutter.ActorAlign.CENTER,
            reactive: true,
            track_hover: true,
        });

        // Plain actor on the UI layer: shown on hover, hidden on leave.
        // bms-popup-blur-target lets Blur my Shell (v78+) blur it when installed.
        this._tooltip = new St.BoxLayout({
            style_class: 'ampere-tooltip bms-popup-blur-target',
            orientation: Clutter.Orientation.VERTICAL,
            reactive: false,
            visible: false,
        });
        Main.layoutManager.uiGroup.add_child(this._tooltip);

        this._box.connect('notify::hover', () => {
            if (this._box.hover)
                this._showTooltip();
            else
                this._tooltip.hide();
        });

        // Clicking the own button (separate mode) opens the preferences.
        if (Clutter.ClickGesture) {
            const click = new Clutter.ClickGesture({
                required_button: Clutter.BUTTON_PRIMARY,
            });
            click.connect('recognize', () => this._activate());
            this.add_action(click);
        } else {
            this.connect('button-release-event', (_actor, event) => {
                if (event.get_button() !== Clutter.BUTTON_PRIMARY)
                    return Clutter.EVENT_PROPAGATE;
                this._activate();
                return Clutter.EVENT_STOP;
            });
        }

        this._place();
    }

    _activate() {
        this._tooltip?.hide();
        this._onActivate?.();
    }

    // The quick settings container is private API, so read it defensively.
    _quickSettingsBox() {
        try {
            return Main.panel.statusArea.quickSettings?._indicators ?? null;
        } catch {
            return null;
        }
    }

    // Own button next to the quick settings, or inside the quick settings icon row.
    _place() {
        const mode = this._settings.get_string('placement');
        const qsBox = this._quickSettingsBox();
        const wanted = mode === 'quick-settings' && qsBox ? 'quick-settings' : 'separate';

        if (wanted === 'quick-settings') {
            this._watchQuickSettings(qsBox);
            if (this._box.get_parent() !== qsBox) {
                this._box.get_parent()?.remove_child(this._box);
                qsBox.insert_child_at_index(this._box, 0);
            } else if (qsBox.get_first_child() !== this._box) {
                qsBox.set_child_at_index(this._box, 0);
            }
        } else {
            this._unwatchQuickSettings();
            if (this._box.get_parent() !== this) {
                this._box.get_parent()?.remove_child(this._box);
                this.add_child(this._box);
            }
        }
        this._box.visible = this._hasItems;
        this.visible = wanted === 'separate' && this._hasItems;
    }

    // Quick settings adds its own indicators asynchronously; keep ours in front.
    _watchQuickSettings(qsBox) {
        if (this._qsBox === qsBox)
            return;
        this._unwatchQuickSettings();
        this._qsBox = qsBox;
        this._qsChildId = qsBox.connect('child-added', (_box, child) => {
            if (child !== this._box && this._box.get_parent() === qsBox)
                qsBox.set_child_at_index(this._box, 0);
        });
    }

    _unwatchQuickSettings() {
        if (this._qsChildId) {
            try {
                this._qsBox.disconnect(this._qsChildId);
            } catch {
                // The quick settings box is already gone at shutdown.
            }
        }
        this._qsBox = null;
        this._qsChildId = 0;
    }

    _showTooltip() {
        if (!this._settings.get_boolean('hover-card') || !this._tooltip.get_n_children())
            return;
        this._tooltip.show();
        const [bx] = this._box.get_transformed_position();
        const [bw] = this._box.get_size();
        const [, tw] = this._tooltip.get_preferred_width(-1);
        const monitor = Main.layoutManager.findMonitorForActor(this._box) ?? Main.layoutManager.primaryMonitor;
        let x = Math.round(bx + bw / 2 - tw / 2);
        x = Math.max(monitor.x + 4, Math.min(x, monitor.x + monitor.width - tw - 4));
        // Anchor below the panel (not below the small box), so the card sits where it did before.
        const [, py] = Main.panel.get_transformed_position();
        this._tooltip.set_position(x, Math.round(py + Main.panel.height + 4));
    }

    // null = monochrome (use the theme color like the other icons)
    _levelColor(item) {
        const s = this._settings;
        if (!s.get_boolean('colored-bar'))
            return null;
        if (item.state === S.CHARGING)
            return hexToRgb(s.get_string('color-charging'));
        if (item.pct <= s.get_int('threshold-low'))
            return hexToRgb(s.get_string('color-low'));
        if (item.pct <= s.get_int('threshold-medium'))
            return hexToRgb(s.get_string('color-medium'));
        return hexToRgb(s.get_string('color-high'));
    }

    _makeBar(item) {
        const area = new St.DrawingArea({width: 5, height: 14, y_align: Clutter.ActorAlign.CENTER});
        area.connect('style-changed', () => area.queue_repaint());
        area.connect('repaint', () => {
            const cr = area.get_context();
            const [w, h] = area.get_surface_size();
            const fg = themeColor(area);
            const fill = this._levelColor(item) ?? fg;
            cr.setLineWidth(1);
            cr.setSourceRGBA(fg[0], fg[1], fg[2], 0.75 * fg[3]);
            roundRect(cr, 0.5, 0.5, w - 1, h - 1, 2);
            cr.stroke();
            const inner = h - 2;
            const fh = Math.round(inner * Math.max(0, Math.min(100, item.pct)) / 100);
            if (fh > 0) {
                cr.setSourceRGBA(fill[0], fill[1], fill[2], 1);
                roundRect(cr, 1, 1 + inner - fh, w - 2, fh, 1);
                cr.fill();
            }
            cr.$dispose();
        });
        return area;
    }

    _makeRing(item) {
        const ring = new St.DrawingArea({width: 44, height: 44});
        ring.connect('style-changed', () => ring.queue_repaint());
        ring.connect('repaint', area => {
            const cr = area.get_context();
            const [w, h] = area.get_surface_size();
            const r = Math.min(w, h) / 2 - 4;
            const start = -Math.PI / 2;
            const fg = themeColor(area);
            cr.setLineWidth(4);
            cr.setSourceRGBA(fg[0], fg[1], fg[2], 0.15);
            cr.arc(w / 2, h / 2, r, 0, 2 * Math.PI);
            cr.stroke();
            if (item.pct > 0) {
                const c = this._levelColor(item) ?? fg;
                cr.setSourceRGBA(c[0], c[1], c[2], 1);
                cr.setLineCap(1); // ROUND
                cr.arc(w / 2, h / 2, r, start, start + 2 * Math.PI * item.pct / 100);
                cr.stroke();
            }
            cr.$dispose();
        });
        return ring;
    }

    update(items) {
        this._hasItems = items.length > 0;
        this._place();
        this._box.destroy_all_children();
        this._tooltip.destroy_all_children();
        if (!items.length) {
            this._tooltip.hide();
            return;
        }

        const s = this._settings;
        for (const item of items) {
            // Panel: icon | vertical bar | percentage (1 px apart, via CSS)
            const row = new St.BoxLayout({style_class: 'ampere-item', y_align: Clutter.ActorAlign.CENTER});
            if (s.get_boolean('show-icon'))
                row.add_child(new St.Icon({icon_name: item.icon, icon_size: 16, y_align: Clutter.ActorAlign.CENTER}));
            if (s.get_boolean('show-bar'))
                row.add_child(this._makeBar(item));
            if (s.get_boolean('show-percentage'))
                row.add_child(new St.Label({text: `${item.pct}%`, style_class: 'ampere-label', y_align: Clutter.ActorAlign.CENTER}));
            this._box.add_child(row);

            // Hover card: ring | name, percentage, state
            const card = new St.BoxLayout({style_class: 'ampere-card', y_align: Clutter.ActorAlign.CENTER});
            card.add_child(this._makeRing(item));
            const text = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL, y_align: Clutter.ActorAlign.CENTER});
            text.add_child(new St.Label({text: item.name, style_class: 'ampere-card-name'}));
            text.add_child(new St.Label({text: `${item.pct}%`, style_class: 'ampere-card-percent'}));
            const sub = stateText(item.state);
            if (sub)
                text.add_child(new St.Label({text: sub, style_class: 'ampere-card-sub'}));
            card.add_child(text);
            this._tooltip.add_child(card);
        }

        if (this._box.hover)
            this._showTooltip();
    }

    destroy() {
        this._unwatchQuickSettings();
        this._onActivate = null;
        this._tooltip?.destroy();
        this._tooltip = null;
        this._box?.destroy();
        this._box = null;
        super.destroy();
    }
});

export default class AmpereExtension extends Extension {
    enable() {
        this._settings = this.getSettings();
        this._cancellable = new Gio.Cancellable();
        this._items = [];
        this._vendorItems = [];
        this._lastSignature = null;
        this._notifiedLow = new Set();
        this._notificationSource = null;
        this._polling = false;
        this._again = false;

        this._indicator = new Indicator(this._settings, () => {
            try {
                this.openPreferences()?.catch(e =>
                    console.error(`[ampere] failed to open preferences: ${e}`));
            } catch (e) {
                console.error(`[ampere] failed to open preferences: ${e}`);
            }
        });
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

    // Plain-data snapshot; same physical device (cable + receiver) is merged by key.
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
                state: p.State ?? S.UNKNOWN,
            };
            const prev = byKey.get(item.key);
            if (!prev || (prev.state === S.UNKNOWN && item.state !== S.UNKNOWN))
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
            const charging = item.state === S.CHARGING || item.state === S.FULLY_CHARGED;
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
            this._notificationSource.connect('destroy', () => {
                this._notificationSource = null;
            });
            Main.messageTray.add(this._notificationSource);
        }
        return this._notificationSource;
    }

    _notifyLow(item) {
        const source = this._ensureNotificationSource();
        const notification = new MessageTray.Notification({
            source,
            title: _('%s battery low').replace('%s', item.name),
            body: _('%s% remaining').replace('%s', String(item.pct)),
            iconName: item.icon,
            urgency: MessageTray.Urgency.NORMAL,
        });
        source.addNotification(notification);
    }

    disable() {
        this._cancellable?.cancel();
        this._cancellable = null;
        if (this._vendorSignalId) {
            Gio.DBus.session.signal_unsubscribe(this._vendorSignalId);
            this._vendorSignalId = 0;
        }
        if (this._timerId) {
            GLib.source_remove(this._timerId);
            this._timerId = 0;
        }
        for (const id of this._subIds ?? [])
            Gio.DBus.system.signal_unsubscribe(id);
        this._subIds = null;
        if (this._settings && this._settingsId)
            this._settings.disconnect(this._settingsId);
        this._settingsId = 0;
        this._settings = null;
        this._notificationSource?.destroy();
        this._notificationSource = null;
        this._notifiedLow = null;
        this._indicator?.destroy();
        this._indicator = null;
        this._items = null;
        this._vendorItems = null;
    }
}
