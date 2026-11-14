# SPDX-License-Identifier: GPL-2.0-or-later
"""Driver interface for vendor HID battery protocols."""


class Driver:
    """Base class for a vendor protocol.

    A driver declares which devices it handles (``vids``, optionally
    ``products``) and how to read the battery. Configuration writes are not
    part of this interface.
    """

    name = "Unknown"
    kind = "mouse"
    vids = frozenset()
    products = None  # None = any product of the vendor
    preferred_interfaces = ()

    def matches(self, device):
        if self.vids and not any(node.vendor in self.vids for node in device.nodes):
            return False
        if self.products is not None and not any(
            node.product in self.products for node in device.nodes
        ):
            return False
        return True

    def read(self, device):
        """Return ``(percentage, state)`` or ``None``.

        state: 0 = discharging, 1 = charging, 2 = full; any other value is
        reported as-is and shown as "unknown" by the consumer.
        """
        raise NotImplementedError
