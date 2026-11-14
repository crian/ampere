// SPDX-License-Identifier: GPL-2.0-or-later

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {STATE, isCancelled} from './utils.js';

// Session-bus service provided by the optional vendor HID companion daemon
// (see daemon/ in the repository). The extension never spawns it directly.
export const HID_BATTERY_BUS_NAME = 'org.gnome.shell.extensions.ampere.HidBattery';
export const HID_BATTERY_OBJECT_PATH = '/org/gnome/shell/extensions/ampere/HidBattery';
export const HID_BATTERY_IFACE = 'org.gnome.shell.extensions.ampere.HidBattery';

const STATE_BY_PROTOCOL = {
    0: STATE.DISCHARGING,
    1: STATE.CHARGING,
    2: STATE.FULLY_CHARGED,
};

const ICON_BY_KIND = {
    mouse: 'input-mouse-symbolic',
    keyboard: 'input-keyboard-symbolic',
    gaming: 'input-gaming-symbolic',
    touchpad: 'input-touchpad-symbolic',
    tablet: 'input-tablet-symbolic',
    headset: 'audio-headset-symbolic',
    headphones: 'audio-headphones-symbolic',
    speakers: 'audio-speakers-symbolic',
    phone: 'phone-symbolic',
};

const isMissingService = e => e instanceof GLib.Error && (
    e.matches(Gio.DBusError, Gio.DBusError.SERVICE_UNKNOWN) ||
    e.matches(Gio.DBusError, Gio.DBusError.SPAWN_EXEC_FAILED) ||
    e.matches(Gio.DBusError, Gio.DBusError.SPAWN_FAILED));

const parse = json => {
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
            icon: ICON_BY_KIND[d.kind] ?? 'battery-symbolic',
            pct: Math.round(d.pct),
            state: STATE_BY_PROTOCOL[d.state] ?? STATE.UNKNOWN,
        }));
};

const getDevices = (cancellable, flags = Gio.DBusCallFlags.NONE) =>
    Gio.DBus.session.call(
        HID_BATTERY_BUS_NAME, HID_BATTERY_OBJECT_PATH, HID_BATTERY_IFACE,
        'GetDevices', null, new GLib.VariantType('(s)'), flags, 5000,
        cancellable);

/** Async query for the shell. ok is false if the companion daemon is missing. */
export async function readHidBattery(cancellable) {
    try {
        const reply = await getDevices(cancellable);
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

/** Blocking query, only for the preferences window (own process). */
export function readHidBatterySync() {
    try {
        const reply = Gio.DBus.session.call_sync(
            HID_BATTERY_BUS_NAME, HID_BATTERY_OBJECT_PATH, HID_BATTERY_IFACE,
            'GetDevices', null, new GLib.VariantType('(s)'),
            Gio.DBusCallFlags.NONE, 5000, null);
        const [json] = reply.deepUnpack();
        return {items: parse(json), ok: true};
    } catch (e) {
        return {items: [], ok: false};
    }
}
