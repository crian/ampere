# Changelog

The version numbers on the left are git tags. EGO assigns its own counter to
extension uploads; entries that went to EGO note it, so both can be matched up.

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
