// SPDX-License-Identifier: GPL-2.0-or-later

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

import {STATE, isCancelled} from './utils.js';

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

const isMissingProperty = e => e instanceof GLib.Error && (
    e.matches(Gio.DBusError, Gio.DBusError.UNKNOWN_PROPERTY) ||
    e.matches(Gio.DBusError, Gio.DBusError.INVALID_ARGS));

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

/** Version of the running service; null when it predates the property. */
export async function readHidVersion(cancellable) {
    try {
        const reply = await Gio.DBus.session.call(
            HID_BATTERY_BUS_NAME, HID_BATTERY_OBJECT_PATH,
            'org.freedesktop.DBus.Properties', 'Get',
            new GLib.Variant('(ss)', [HID_BATTERY_IFACE, 'Version']),
            new GLib.VariantType('(v)'), Gio.DBusCallFlags.NONE, 5000, cancellable);
        const [version] = reply.recursiveUnpack();
        return String(version);
    } catch (e) {
        if (isCancelled(e))
            throw e;
        if (isMissingProperty(e))
            return null;
        throw e;
    }
}

/**
 * True when the version is older than the one the extension needs; null counts
 * as older, 'dev' and unknown values never do.
 */
export function daemonTooOld(version, required) {
    if (version === null)
        return true;
    if (typeof version !== 'string' || version === 'dev')
        return false;
    if (typeof required !== 'string' || required === 'dev')
        return false;

    const have = version.split('.').map(n => parseInt(n, 10));
    if (have.some(Number.isNaN))
        return false;
    const want = required.split('.').map(n => parseInt(n, 10));
    for (let i = 0; i < want.length; i++) {
        const part = have[i] ?? 0;
        if (part !== want[i])
            return part < want[i];
    }
    return false;
}

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
