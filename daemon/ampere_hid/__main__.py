# SPDX-License-Identifier: GPL-2.0-or-later
"""Allow ``python3 -m ampere_hid``."""

import sys

from .daemon import main

if __name__ == "__main__":
    sys.exit(main(sys.argv))
