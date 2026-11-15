# SPDX-License-Identifier: GPL-2.0-or-later
"""Build and locate the single-file ``ampere-hid`` program (a zipapp)."""

import io
import re
import shutil
import tempfile
import zipapp
import zipfile
from pathlib import Path

INTERPRETER = "/usr/bin/env python3"

ENTRY_POINT = """\
import sys

from ampere_hid.cli import main

sys.exit(main())
"""


def running_archive():
    """Return the path of the zipapp this code runs from, or ``None``."""
    for parent in Path(__file__).resolve().parents:
        if parent.is_file() and zipfile.is_zipfile(parent):
            return parent
    return None


def stamp_version(source, version):
    """Write ``version`` into the package copy that goes into the archive."""
    init = Path(source) / "ampere_hid" / "__init__.py"
    text, count = re.subn(
        r'^__version__ = .*$', f'__version__ = "{version}"', init.read_text(encoding="utf-8"),
        count=1, flags=re.MULTILINE,
    )
    if count != 1:
        raise ValueError(f"no __version__ line in {init}")
    init.write_text(text, encoding="utf-8")


def build_bytes(version=None):
    """Pack the ``ampere_hid`` package into a zipapp and return its bytes."""
    package = Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "src"
        shutil.copytree(
            package,
            source / package.name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        if version:
            stamp_version(source, version)
        (source / "__main__.py").write_text(ENTRY_POINT, encoding="utf-8")
        target = io.BytesIO()
        zipapp.create_archive(
            source, target, interpreter=INTERPRETER, compressed=True
        )
    return target.getvalue()


def program_bytes():
    """Bytes of the program: the running archive itself, or a fresh build."""
    archive = running_archive()
    if archive is not None:
        return archive.read_bytes()
    return build_bytes()
