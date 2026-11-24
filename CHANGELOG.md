# Changelog

The version numbers on the left are git tags. EGO assigns its own counter to
extension uploads; entries that went to EGO note it, so both can be matched up.

## v1.4 – EGO version 5 (2026-11-24)

- Optional low-battery notification (once per discharge cycle, with a 5%
  hysteresis margin and reset when charging)
- Custom device names (aliases) in the preferences, shown on the hover card
  and in low-battery notifications
- Clicking the panel button in separate mode now opens the preferences
- Blur my Shell (v78+) opt-in support on the hover card, including dark/light
  popup styling
- Preferences: Reset button for colors and charge thresholds, and a toast
  when copying the companion service install command
- Track Quick Settings indicator placement via `child-added` instead of
  polling retries
- Repository layout: moved the extension into `extension/`, added a
  `Makefile` and GJS unit tests for the extension helpers

As before, the extension package contains no daemon code. If you use the
optional companion service, reinstall `ampere-hid` from this release so its
reported version matches `1.4`.

## v1.3 – EGO version 4 (2026-11-16)

Supersedes the pending v1.2 upload on EGO with review and lifecycle clean-ups:

- Extension: use a local D-Bus call helper instead of mutating
  `Gio.DBusConnection.prototype`, guard async callbacks after `disable()`, and
  keep retrying Quick Settings placement if the panel is not ready yet
- Preferences: cancel pending D-Bus calls and disconnect threshold handlers when
  the window closes, and consolidate console error logging (fixes the Shexli
  `EGO-A-004` warning)
- Companion service: probe preferred HID interfaces in the declared order
  (`2, 1, 0` instead of `0, 1, 2`), deduplicate permission warnings per USB
  model instead of per `/dev/hidraw` node, and remove all GLib sources cleanly
  on exit

Reinstall the companion service (`ampere-hid install`) to get the daemon fixes
and match the version expected by the preferences. As before, the extension
package contains no daemon code.

## v1.2 – EGO version 3 (2026-11-15)

- A mouse that is switched off disappears from the panel after the grace
  period. Lamzu receivers say so explicitly; the other dongles simply stop
  answering. Before, the receiver kept repeating its last value.
- The service polls on a fixed 30 s cadence. Waiting for a sleeping mouse no
  longer stretches the interval, and the readings of the devices that did
  answer are shown before the wait.
- Plugging a device in ends that wait at once. Silent receivers are probed more
  cheaply (Compx and Lamzu), and a mouse that wakes up is found again on the
  next sweep.
- `ampere-hid status` shows version, service state, udev rule and devices on one
  screen.
- The service reports its version over D-Bus. The preferences point at the
  install command when it is older than the extension expects.
- The access warning now tells a missing or outdated udev rule apart from a
  product ID that is not in the table; `diagnose` reports the same reason.

The companion service changed too: reinstall it from this release
(`ampere-hid install`) to get the fixes above. As before, the extension package
contains no daemon code.

## v1.1 – EGO version 2 (2026-11-15)

- The companion service listens to hidraw uevents, so a device appears within a
  second or two of plugging it in and disappears at once when it is unplugged,
  instead of waiting for the next 30 s poll
- A short retry after plugging in covers udev granting access after the kernel
  event
- MCHOSE queries are much faster while the mouse is asleep or still starting up
  (about 8 s down to 2.9 s) and remember the node and report channel that
  answered
- udev: product IDs for the A9 Max Ultra 2.0 (`373b:1270`) and the A7 V2 Pro
  (`3837:4018`) on their cable
- Preferences: the install command is rendered in monospace, and the note about
  merged entries now only mentions UPower devices

The faster detection comes from the companion service, which is a separate
program: after updating the extension, run `ampere-hid install` or download the
new `ampere-hid` from the release. The extension package itself still contains
no daemon code.

## v1.0 – EGO version 1 (2026-11-14)

First release.

- Battery level of wireless mice and keyboards in the top bar: icon, bar,
  percentage, colour coding and a hover card
- UPower devices are read over D-Bus; mice that only expose their battery
  through vendor HID reports are handled by the optional companion service
- Verified on a Lamzu Maya X 8K, a Lamzu Thorn, an ATK A9 Max Ultra 2.0 and a
  MCHOSE A7 V2 Pro
- Level colours follow the Adwaita palette (success, warning, error, accent)

The companion service is installed separately (see the README). The
extension itself works without it for everything UPower reports, and the EGO
package contains no daemon code.
