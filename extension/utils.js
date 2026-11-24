// SPDX-License-Identifier: GPL-2.0-or-later

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

// UPower D-Bus enums (org.freedesktop.UPower.Device)
export const KIND = {
    UNKNOWN: 0, LINE_POWER: 1, BATTERY: 2, UPS: 3, MONITOR: 4, MOUSE: 5, KEYBOARD: 6,
    PHONE: 8, TABLET: 10, GAMING_INPUT: 12, PEN: 13, TOUCHPAD: 14,
    HEADSET: 17, SPEAKERS: 18, HEADPHONES: 19,
};
export const STATE = {
    UNKNOWN: 0, CHARGING: 1, DISCHARGING: 2, EMPTY: 3, FULLY_CHARGED: 4,
};

export const ICONS = new Map([
    [KIND.MOUSE, 'input-mouse-symbolic'],
    [KIND.KEYBOARD, 'input-keyboard-symbolic'],
    [KIND.GAMING_INPUT, 'input-gaming-symbolic'],
    [KIND.TOUCHPAD, 'input-touchpad-symbolic'],
    [KIND.TABLET, 'input-tablet-symbolic'],
    [KIND.PEN, 'input-tablet-symbolic'],
    [KIND.PHONE, 'phone-symbolic'],
    [KIND.HEADSET, 'audio-headset-symbolic'],
    [KIND.HEADPHONES, 'audio-headphones-symbolic'],
    [KIND.SPEAKERS, 'audio-speakers-symbolic'],
]);

const BUS = 'org.freedesktop.UPower';
const ROOT = '/org/freedesktop/UPower';
const DEVICE_IFACE = 'org.freedesktop.UPower.Device';
const TIMEOUT_MS = 5000;

export const dbusCall = (conn, busName, objectPath, iface, method, params, replyType, flags, timeoutMs, cancellable) =>
    new Promise((resolve, reject) => {
        conn.call(busName, objectPath, iface, method, params, replyType, flags, timeoutMs, cancellable, (_conn, res) => {
            try {
                resolve(conn.call_finish(res));
            } catch (e) {
                reject(e);
            }
        });
    });

export const isCancelled = e =>
    e instanceof GLib.Error && e.matches(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED);

/** True for batteries of peripherals (not laptop battery, AC adapter, UPS, ...). */
export function isPeripheral(p) {
    if (p.PowerSupply || !p.IsPresent)
        return false;
    return ![KIND.UNKNOWN, KIND.LINE_POWER, KIND.BATTERY, KIND.UPS, KIND.MONITOR].includes(p.Type);
}

/**
 * Stable key for a device. Two identical devices without a serial would
 * otherwise collapse into one entry, so fall back to the sysfs path, which is
 * unique per device and does not change while it stays on the same port.
 */
export function deviceKey(p) {
    if (p.Serial)
        return p.Serial;
    const name = p.Model || p.Vendor || 'device';
    return p.NativePath ? `${name}@${p.NativePath}` : name;
}

export function deviceName(p) {
    const vendor = p.Vendor ?? '';
    const model = p.Model ?? '';
    if (model && vendor && !model.toLowerCase().includes(vendor.toLowerCase()))
        return `${vendor} ${model}`;
    return model || vendor || 'Unknown device';
}

/** Async, never blocks the shell. Returns plain objects (D-Bus properties). */
export async function fetchDevices(cancellable) {
    const conn = Gio.DBus.system;
    const reply = await dbusCall(conn, BUS, ROOT, BUS, 'EnumerateDevices', null,
        new GLib.VariantType('(ao)'), Gio.DBusCallFlags.NONE, TIMEOUT_MS, cancellable);
    const [paths] = reply.deepUnpack();
    // Promise.all keeps the order of `paths`, so the panel keeps it too.
    // Pushing as the answers arrive would reshuffle the entries each poll.
    const devices = await Promise.all(paths.map(async path => {
        try {
            const r = await dbusCall(conn, BUS, path, 'org.freedesktop.DBus.Properties', 'GetAll',
                new GLib.Variant('(s)', [DEVICE_IFACE]), new GLib.VariantType('(a{sv})'),
                Gio.DBusCallFlags.NONE, TIMEOUT_MS, cancellable);
            const [props] = r.recursiveUnpack();
            return {path, ...props};
        } catch (e) {
            if (isCancelled(e))
                throw e;
            // device vanished between enumerate and read: ignore
            return null;
        }
    }));
    return devices.filter(Boolean);
}

export const UPOWER = {BUS, ROOT};
