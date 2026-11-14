# Changelog

The version numbers on the left are git tags. EGO assigns its own counter, which
is noted on each entry so both can be matched up.

## v1.0 – EGO version 1 (2026-11-14)

First release.

- Battery level of wireless mice and keyboards in the top bar: icon, bar,
  percentage, colour coding and a hover card
- UPower devices are read over D-Bus; mice that only expose their battery
  through vendor HID reports are handled by the optional companion service
- Verified on a Lamzu Maya X 8K, a Lamzu Thorn, an ATK A9 Max Ultra 2.0 and a
  MCHOSE A7 V2 Pro
- Level colours follow the Adwaita palette (success, warning, error, accent)

The companion service is installed separately with `daemon/install.sh`. The
extension itself works without it for everything UPower reports, and the EGO
package contains no daemon code.
