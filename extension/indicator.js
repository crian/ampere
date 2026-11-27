// SPDX-License-Identifier: GPL-2.0-or-later

import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import {gettext as _} from 'resource:///org/gnome/shell/extensions/extension.js';

import {STATE} from './utils.js';

const stateText = state => {
    switch (state) {
    case STATE.CHARGING: return _('Charging');
    case STATE.DISCHARGING: return _('Discharging');
    case STATE.FULLY_CHARGED: return _('Fully charged');
    default: return '';
    }
};

const hexToRgb = hex => {
    const m = /^#?([0-9a-f]{6})$/i.exec(hex);
    if (!m)
        return [1, 1, 1];
    const n = parseInt(m[1], 16);
    return [((n >> 16) & 255) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
};

// Foreground color of the theme at this actor, i.e. what the other panel icons use.
const themeColor = actor => {
    const c = actor.get_theme_node().get_foreground_color();
    return [c.red / 255, c.green / 255, c.blue / 255, c.alpha / 255];
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

export const AmpereIndicator = GObject.registerClass(
class Indicator extends PanelMenu.Button {
    constructor(settings, onActivate) {
        // dontCreateMenu = true: no popup menu, no input grab, no hover loops.
        super(0.5, 'Ampere', true);
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

        // On the UI layer, so the card can sit below the panel instead of being
        // clipped by the button. bms-popup-blur-target is for Blur my Shell.
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

        // Clicking the button opens the preferences.
        const click = new Clutter.ClickGesture({
            required_button: Clutter.BUTTON_PRIMARY,
        });
        click.connect('recognize', () => this._activate());
        this.add_action(click);

        this._place();
    }

    _activate() {
        this._tooltip.hide();
        this._onActivate();
    }

    // Own button next to the quick settings, or inside the quick settings icon row.
    _place() {
        const inQuickSettings = this._settings.get_string('placement') === 'quick-settings';

        if (inQuickSettings) {
            // Private API: the icon row inside the Quick Settings panel button.
            const qsBox = Main.panel.statusArea.quickSettings._indicators;
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
        this.visible = !inQuickSettings && this._hasItems;
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
        if (this._qsChildId)
            this._qsBox.disconnect(this._qsChildId);
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
        // Anchor below the panel, not below the small box.
        const [, py] = Main.panel.get_transformed_position();
        this._tooltip.set_position(x, Math.round(py + Main.panel.height + 4));
    }

    // null = monochrome (use the theme color like the other icons)
    _levelColor(item) {
        const s = this._settings;
        if (!s.get_boolean('colored-bar'))
            return null;
        if (item.state === STATE.CHARGING)
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
        this._tooltip.destroy();
        this._tooltip = null;
        this._box.destroy();
        this._box = null;
        super.destroy();
    }
});
