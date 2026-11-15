# SPDX-License-Identifier: GPL-2.0-or-later
"""Keep the last reading of a device that stopped answering.

A sleeping mouse answers nothing, so its panel entry would vanish right away.
It is kept for a few polls instead.
"""


def apply(matched_keys, fresh, last, misses, grace_polls):
    """Return the items to publish.

    ``matched_keys`` are the keys of the devices that are physically present,
    ``fresh`` maps keys to a current reading. ``last`` and ``misses`` carry
    state between calls and are updated in place.
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
    """Return what would be shown right now, without counting any misses.

    A device that gave a reading is shown with it; a device that stays silent
    keeps its last reading. Used to publish a sweep's result before the sweep
    waits for a sleeping mouse, so the other devices do not wait along.
    ``apply`` still runs once at the end of the sweep and does the counting.
    """
    items = []
    for key in matched_keys:
        if key in fresh:
            items.append(fresh[key])
        elif key in last:
            items.append(last[key])
    return items
