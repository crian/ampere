# SPDX-License-Identifier: GPL-2.0-or-later
"""Keep the last reading of a device that stopped answering, for a few polls."""


def apply(matched_keys, fresh, last, misses, grace_polls):
    """Return the items to publish.

    ``matched_keys`` are the present devices, ``fresh`` maps keys to readings.
    ``last`` and ``misses`` carry state and are updated in place.
    """
    items = []
    for key in matched_keys:
        if key in fresh:
            last[key] = fresh[key]
            misses[key] = 0
            items.append(fresh[key])
            continue

        misses[key] = misses.get(key, 0) + 1
        if key in last and misses[key] < grace_polls:
            items.append(last[key])
        else:
            last.pop(key, None)

    present = set(matched_keys)
    for key in list(last):
        if key not in present:
            del last[key]
            misses.pop(key, None)

    return items


def peek(matched_keys, fresh, last):
    """What apply would show right now, without counting a miss.

    Used to publish a sweep before it waits for a silent device.
    """
    items = []
    for key in matched_keys:
        if key in fresh:
            items.append(fresh[key])
        elif key in last:
            items.append(last[key])
    return items
