"""Guards on the .bat files, which cmd.exe parses far less forgivingly than it looks.

A user reported the setup window closing the instant they pressed a key, with no
message and no install.log: the batch file had LF-only line endings and 1,118 bytes of
UTF-8 Arabic. cmd.exe reads a batch file with the console codepage and mis-parses both,
so `goto` stopped resolving and the script fell off the end silently. Nothing in the
Python test suite could have caught it, hence these checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
BATCH_FILES = sorted(REPO.glob("*.bat"))


def test_batch_files_are_present() -> None:
    assert {path.name for path in BATCH_FILES} == {
        "setup.bat",
        "update.bat",
        "uninstall.bat",
    }


@pytest.mark.parametrize("path", BATCH_FILES, ids=lambda p: p.name)
class TestBatchEncoding:
    def test_is_pure_ascii(self, path: Path) -> None:
        """Arabic must not live inside a .bat file.

        cmd.exe decodes the script with the console codepage, so multi-byte UTF-8 in
        the file body corrupts parsing. User-facing Arabic belongs in install.py, or in
        a UTF-8 .txt streamed by `type` once the codepage is 65001.
        """
        data = path.read_bytes()
        offenders = [
            (index, byte) for index, byte in enumerate(data) if byte > 0x7F
        ]
        assert not offenders, (
            f"{path.name} has {len(offenders)} non-ASCII byte(s), first at offset "
            f"{offenders[0][0] if offenders else '-'}"
        )

    def test_uses_crlf_line_endings(self, path: Path) -> None:
        """LF-only endings make cmd.exe mis-parse labels and exit without a word."""
        data = path.read_bytes()
        lone_lf = data.replace(b"\r\n", b"").count(b"\n")
        assert lone_lf == 0, f"{path.name} has {lone_lf} LF-only line ending(s)"
        assert b"\r\n" in data, f"{path.name} has no CRLF line endings at all"

    def test_has_no_byte_order_mark(self, path: Path) -> None:
        # cmd.exe echoes a BOM as stray characters and can choke on the first command.
        assert not path.read_bytes().startswith(b"\xef\xbb\xbf")


@pytest.mark.parametrize("path", BATCH_FILES, ids=lambda p: p.name)
def test_every_goto_target_exists(path: Path) -> None:
    """A `goto` with no label makes cmd.exe abort and close the window."""
    text = path.read_text(encoding="ascii")
    labels = {
        line.strip()[1:].split()[0].lower()
        for line in text.splitlines()
        if line.strip().startswith(":") and not line.strip().startswith("::")
    }
    targets = {
        line.strip().split()[1].lower()
        for line in text.splitlines()
        if line.strip().lower().startswith(("goto ", "call :"))
        and len(line.strip().split()) > 1
    }
    missing = {
        target.lstrip(":")
        for target in targets
        if target.lstrip(":") not in labels and target.lower() != "eof"
    }
    assert not missing, f"{path.name} jumps to undefined label(s): {sorted(missing)}"


@pytest.mark.parametrize("path", BATCH_FILES, ids=lambda p: p.name)
def test_window_never_closes_without_a_pause(path: Path) -> None:
    """Every exit path must pause, or a double-clicking user sees nothing.

    This is the reported symptom: a window that vanishes leaves the user unable to tell
    success from failure.
    """
    text = path.read_text(encoding="ascii")
    lines = [line.strip().lower() for line in text.splitlines()]

    # Reaching :done is the single way out, and :done pauses before exiting.
    assert ":done" in lines
    done_index = lines.index(":done")
    tail = lines[done_index:]
    assert any(line.startswith("pause") for line in tail), "the :done block must pause"

    # No exit may bypass it, other than the subroutine returns (exit /b 0) and :done's own.
    for index, line in enumerate(lines):
        if not line.startswith("exit /b"):
            continue
        if index >= done_index:
            continue
        assert line == "exit /b 0", (
            f"{path.name} line {index + 1}: {line!r} leaves the script without pausing"
        )


def test_setup_quotes_the_interpreter_without_swallowing_its_arguments() -> None:
    """`"py -3"` would be read as one filename; the launcher and its flag must be split.

    The probe can select the `py` launcher, which needs `-3` passed separately. Quoting
    them together produces "not recognized as an internal or external command".
    """
    text = (REPO / "setup.bat").read_text(encoding="ascii")
    assert '"%PY_EXE%" %PY_ARG%' in text
    assert '"%PY_EXE% %PY_ARG%"' not in text


def test_message_files_are_utf8_for_type() -> None:
    """The pre-Python Arabic messages are streamed by `type`, so they must be UTF-8."""
    assets = REPO / "assets"
    files = sorted(assets.glob("*-ar.txt"))
    assert files, "expected Arabic message files under assets/"
    for path in files:
        data = path.read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf"), f"{path.name} should have no BOM"
        text = data.decode("utf-8")  # raises if not valid UTF-8
        assert any("؀" <= ch <= "ۿ" for ch in text), (
            f"{path.name} carries no Arabic"
        )
        assert b"\r\n" in data, f"{path.name} should use CRLF for the Windows console"


def test_batch_files_referenced_by_setup_exist() -> None:
    """A missing referenced file would silently skip a message the user needs."""
    text = (REPO / "setup.bat").read_text(encoding="ascii")
    for name in ("install.py",):
        assert name in text
    for asset in ("python-missing-ar.txt", "python-installed-ar.txt"):
        assert asset in text
        assert (REPO / "assets" / asset).is_file(), f"assets/{asset} is referenced but absent"
