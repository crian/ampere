# SPDX-License-Identifier: GPL-2.0-or-later
"""Registry of supported vendor protocols.

Adding a mouse:

1. Add a module here with a ``Driver`` subclass.
2. Append an instance to ``DRIVERS``.
3. Add its VID/PID to ``daemon/70-ampere-hid.rules``.
4. Document whether it was tested on real hardware.

Order matters: a device is handled by the first matching driver. The Lamzu
driver claims the VIDs it shares with Compx receivers and tries both
transports itself, so the plain Compx driver only lists the remaining VIDs.
"""

from .compx import CompxDriver
from .lamzu import LamzuDriver
from .mchose import MchoseDriver

DRIVERS = [
    LamzuDriver(),
    CompxDriver(),
    MchoseDriver(),
]


def matching_drivers(device):
    return [driver for driver in DRIVERS if driver.matches(device)]


def find_driver(device):
    drivers = matching_drivers(device)
    return drivers[0] if drivers else None
