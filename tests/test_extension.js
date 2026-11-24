// SPDX-License-Identifier: GPL-2.0-or-later

import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import System from 'system';

import {
    KIND,
    STATE,
    deviceKey,
    deviceName,
    displayName,
    isCancelled,
    isPeripheral,
    readDeviceNames,
    writeDeviceNames,
} from '../extension/utils.js';
import {daemonTooOld, parse} from '../extension/hidbattery.js';

let passed = 0;
let failed = 0;

function assertEqual(actual, expected, msg = '') {
    const a = JSON.stringify(actual);
    const b = JSON.stringify(expected);
    if (a !== b)
        throw new Error(`${msg ? `${msg}: ` : ''}expected ${b}, got ${a}`);
}

function assertThrows(fn, msg = '') {
    let threw = false;
    try {
        fn();
    } catch {
        threw = true;
    }
    if (!threw)
        throw new Error(`${msg ? `${msg}: ` : ''}expected function to throw`);
}

function test(name, fn) {
    try {
        fn();
        passed++;
        print(`ok - ${name}`);
    } catch (e) {
        failed++;
        printerr(`FAIL - ${name}: ${e.message}`);
    }
}

// --- utils.js ---

test('isPeripheral accepts present non-supply peripheral devices', () => {
    for (const type of [
        KIND.MOUSE, KIND.KEYBOARD, KIND.GAMING_INPUT, KIND.TOUCHPAD,
        KIND.TABLET, KIND.PEN, KIND.PHONE, KIND.HEADSET, KIND.HEADPHONES, KIND.SPEAKERS,
    ])
        assertEqual(isPeripheral({Type: type, PowerSupply: false, IsPresent: true}), true, `type ${type}`);
});

test('isPeripheral rejects laptop batteries, AC, UPS, monitors, unknown and absent devices', () => {
    for (const type of [KIND.UNKNOWN, KIND.LINE_POWER, KIND.BATTERY, KIND.UPS, KIND.MONITOR])
        assertEqual(isPeripheral({Type: type, PowerSupply: false, IsPresent: true}), false, `type ${type}`);
    assertEqual(isPeripheral({Type: KIND.MOUSE, PowerSupply: true, IsPresent: true}), false, 'PowerSupply');
    assertEqual(isPeripheral({Type: KIND.MOUSE, PowerSupply: false, IsPresent: false}), false, 'not present');
});

test('deviceKey prefers Serial and falls back to Model/Vendor + NativePath', () => {
    assertEqual(deviceKey({Serial: 'ABC123', Model: 'Mouse', NativePath: 'hid-1'}), 'ABC123');
    assertEqual(deviceKey({Model: 'MX Master', NativePath: 'hid-1'}), 'MX Master@hid-1');
    assertEqual(deviceKey({Vendor: 'Logitech', NativePath: 'hid-2'}), 'Logitech@hid-2');
    assertEqual(deviceKey({Model: 'MX Master'}), 'MX Master');
    assertEqual(deviceKey({NativePath: 'hid-3'}), 'device@hid-3');
    assertEqual(deviceKey({}), 'device');
});

test('deviceName combines Vendor and Model without duplicating Vendor', () => {
    assertEqual(deviceName({Vendor: 'Logitech', Model: 'MX Master 3'}), 'Logitech MX Master 3');
    assertEqual(deviceName({Vendor: 'Logitech', Model: 'Logitech G Pro'}), 'Logitech G Pro');
    assertEqual(deviceName({Vendor: 'lamzu', Model: 'LAMZU Maya X'}), 'LAMZU Maya X');
    assertEqual(deviceName({Model: 'Wireless Mouse'}), 'Wireless Mouse');
    assertEqual(deviceName({Vendor: 'Keychron'}), 'Keychron');
    assertEqual(deviceName({}), 'Unknown device');
});

test('displayName applies trimmed alias and falls back cleanly', () => {
    const names = {'k1': '  My Mouse  ', 'k2': '   ', 'k3': ''};
    assertEqual(displayName('k1', '2.4G Receiver', names), 'My Mouse');
    assertEqual(displayName('k2', '2.4G Receiver', names), '2.4G Receiver');
    assertEqual(displayName('k3', '2.4G Receiver', names), '2.4G Receiver');
    assertEqual(displayName('other', 'Fallback', names), 'Fallback');
    assertEqual(displayName('k1', 'Fallback', null), 'Fallback');
});

test('readDeviceNames and writeDeviceNames round-trip a{ss} variants', () => {
    let stored = new GLib.Variant('a{ss}', {});
    const fakeSettings = {
        get_value: () => stored,
        set_value: (_key, val) => {
            stored = val;
        },
    };
    assertEqual(readDeviceNames(fakeSettings), {});
    writeDeviceNames(fakeSettings, {'vendor:1': 'Maya X', 'ABC': 'Keyboard'});
    assertEqual(readDeviceNames(fakeSettings), {'vendor:1': 'Maya X', 'ABC': 'Keyboard'});
    writeDeviceNames(fakeSettings, {});
    assertEqual(readDeviceNames(fakeSettings), {});
});

test('isCancelled matches only Gio.IOErrorEnum.CANCELLED', () => {
    const cancelled = new GLib.Error(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED, 'cancelled');
    const failedErr = new GLib.Error(Gio.IOErrorEnum, Gio.IOErrorEnum.FAILED, 'failed');
    assertEqual(isCancelled(cancelled), true);
    assertEqual(isCancelled(failedErr), false);
    assertEqual(isCancelled(new Error('plain')), false);
});

// --- hidbattery.js ---

test('daemonTooOld compares version strings and special values', () => {
    assertEqual(daemonTooOld(null, '1.3'), true, 'null predates Version property');
    assertEqual(daemonTooOld('dev', '1.3'), false, 'dev service');
    assertEqual(daemonTooOld('1.0', 'dev'), false, 'dev extension');
    assertEqual(daemonTooOld('1.2', '1.3'), true);
    assertEqual(daemonTooOld('1.2.9', '1.3'), true);
    assertEqual(daemonTooOld('1.3', '1.3'), false);
    assertEqual(daemonTooOld('1.3.1', '1.3'), false);
    assertEqual(daemonTooOld('1.3', '1.3.1'), true);
    assertEqual(daemonTooOld('2.0', '1.9'), false);
    assertEqual(daemonTooOld('unknown', '1.3'), false);
    assertEqual(daemonTooOld(undefined, '1.3'), false);
});

test('parse maps valid daemon devices and filters invalid percentages', () => {
    const json = JSON.stringify({
        devices: [
            {key: 'a', name: 'Maya X', kind: 'mouse', pct: 84.6, state: 0},
            {key: 'b', name: '', kind: 'unknown', pct: 100, state: 2},
            {key: 'c', name: 'Charging', kind: 'keyboard', pct: 0, state: 1},
            {key: 'bad1', name: 'Neg', kind: 'mouse', pct: -1, state: 0},
            {key: 'bad2', name: 'High', kind: 'mouse', pct: 101, state: 0},
            {key: 'bad3', name: 'Null', kind: 'mouse', pct: null, state: 0},
        ],
    });
    assertEqual(parse(json), [
        {key: 'vendor:a', name: 'Maya X', icon: 'input-mouse-symbolic', pct: 85, state: STATE.DISCHARGING},
        {key: 'vendor:b', name: 'Vendor HID device', icon: 'battery-symbolic', pct: 100, state: STATE.FULLY_CHARGED},
        {key: 'vendor:c', name: 'Charging', icon: 'input-keyboard-symbolic', pct: 0, state: STATE.CHARGING},
    ]);
    assertEqual(parse('{}'), []);
    assertThrows(() => parse('{not json'), 'invalid json');
});

print(`\nRan ${passed + failed} JS tests (${passed} passed, ${failed} failed)`);
if (failed > 0)
    System.exit(1);
