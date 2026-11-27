// SPDX-License-Identifier: GPL-2.0-or-later

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {ICONS, KIND, STATE, isCancelled} from './utils.js';

// Session-bus service of the optional companion daemon; the extension never
// spawns it itself.
export const HID_BATTERY_BUS_NAME = 'org.gnome.shell.extensions.ampere.HidBattery';
export const HID_BATTERY_OBJECT_PATH = '/org/gnome/shell/extensions/ampere/HidBattery';
export const HID_BATTERY_IFACE = 'org.gnome.shell.extensions.ampere.HidBattery';

const STATE_BY_PROTOCOL = {
    0: STATE.DISCHARGING,
    1: STATE.CHARGING,
    2: STATE.FULLY_CHARGED,
};

const KIND_BY_NAME = {
    mouse: KIND.MOUSE,
    keyboard: KIND.KEYBOARD,
    gaming: KIND.GAMING_INPUT,
    touchpad: KIND.TOUCHPAD,
    tablet: KIND.TABLET,
    headset: KIND.HEADSET,
    headphones: KIND.HEADPHONES,
    speakers: KIND.SPEAKERS,
    phone: KIND.PHONE,
};

const isMissingService = e => e instanceof GLib.Error && (
    e.matches(Gio.DBusError, Gio.DBusError.SERVICE_UNKNOWN) ||
    e.matches(Gio.DBusError, Gio.DBusError.SPAWN_EXEC_FAILED) ||
    e.matches(Gio.DBusError, Gio.DBusError.SPAWN_FAILED));

export const parse = json => {
    let data;
    try {
        data = JSON.parse(json);
    } catch (e) {
        throw new Error(`invalid daemon output: ${e.message}`);
    }

    return (data.devices ?? [])
        .filter(d => Number.isFinite(d.pct) && d.pct >= 0 && d.pct <= 100)
        .map(d => ({
            key: `vendor:${d.key}`,
            name: d.name || 'Vendor HID device',
            icon: ICONS.get(KIND_BY_NAME[d.kind]) ?? 'battery-symbolic',
            pct: Math.round(d.pct),
            state: STATE_BY_PROTOCOL[d.state] ?? STATE.UNKNOWN,
        }));
};

/** Async query for the shell. ok is false if the companion daemon is missing. */
export async function readHidBattery(cancellable) {
    try {
        const reply = await Gio.DBus.session.call(
            HID_BATTERY_BUS_NAME, HID_BATTERY_OBJECT_PATH, HID_BATTERY_IFACE,
            'GetDevices', null, new GLib.VariantType('(s)'), Gio.DBusCallFlags.NONE, 5000,
            cancellable);
        const [json] = reply.deepUnpack();
        return {items: parse(json), ok: true};
    } catch (e) {
        if (isCancelled(e))
            throw e;
        if (isMissingService(e))
            return {items: [], ok: false};
        throw e;
    }
}
