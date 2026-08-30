"""Guards on the installer's contract with the running server.

Claude Desktop launches the server with `-m shamela_mcp` from its own working
directory, not from this project folder. A plain `pip install .` therefore hands it a
*copy* in site-packages: editing the source and restarting the app changes nothing,
and the staleness check cannot see the difference either, because it fingerprints
whichever copy got imported. That is how a correct fix appeared to fail twice.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
VENV_PYTHON = REPO / ".venv" / "Scripts" / "python.exe"


def test_installer_installs_editable_not_a_copy() -> None:
    source = (REPO / "install.py").read_text(encoding="utf-8")
    assert '"--editable"' in source, (
        "install.py must install editable, or Claude Desktop runs a stale snapshot"
    )
    # A bare `pip install <repo>` alongside it would recreate the copy.
    assert '"install", "--quiet", str(REPO)' not in source


def test_config_entry_points_at_the_venv_interpreter() -> None:
    """An absolute interpreter path is what makes PATH and Anaconda irrelevant."""
    source = (REPO / "install.py").read_text(encoding="utf-8")
    assert "VENV_PYTHON" in source
    assert '"-m", "shamela_mcp"' in source or "'-m', 'shamela_mcp'" in source


@pytest.mark.skipif(
    not VENV_PYTHON.is_file(), reason="no .venv on this machine"
)
def test_module_resolves_to_this_source_tree_from_a_foreign_cwd() -> None:
    """The decisive check: what Claude Desktop imports must be the code on disk here.

    Run from a directory that is not the project, so nothing is picked up merely
    because it sits in the current folder.
    """
    result = subprocess.run(
        [
            str(VENV_PYTHON),
            "-X",
            "utf8",
            "-c",
            "import shamela_mcp, pathlib; print(pathlib.Path(shamela_mcp.__file__).parent)",
        ],
        cwd=str(Path(sys.prefix).anchor or REPO.anchor),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    imported = Path(result.stdout.strip())
    assert imported == REPO / "shamela_mcp", (
        f"Claude Desktop would import {imported}, not this source tree"
    )


@pytest.mark.skipif(
    not VENV_PYTHON.is_file(), reason="no .venv on this machine"
)
def test_no_stale_copy_shadows_the_source() -> None:
    site_packages = REPO / ".venv" / "Lib" / "site-packages"
    if not site_packages.is_dir():
        pytest.skip("layout differs on this platform")
    copied = site_packages / "shamela_mcp" / "__init__.py"
    assert not copied.exists(), (
        "a copied package in site-packages shadows the source tree; "
        "reinstall with pip install --editable ."
    )
