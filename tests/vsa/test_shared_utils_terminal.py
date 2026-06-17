"""Tests for :mod:`job_bot.shared.utils.terminal` (issue #151).

The VSA port of the legacy terminal module
module preserves the public :func:`setup_terminal` function and the
``print_kitty_image`` / ``print_sixel_mage`` helpers. :func:`setup_terminal`
is a no-op on non-Windows platforms and only touches the Windows
console on Windows.

Tests mock :func:`platform.system` so the Windows branch can be
exercised on Linux/macOS CI. The actual ``ctypes.windll`` access is
patched via the module's own reference (the standard ``ctypes``
module does not expose ``windll`` on Linux, so patching the public
``ctypes.windll`` path is impossible outside Windows).
"""

from __future__ import annotations

import base64
import io
import os
import platform as platform_module
from unittest import mock

import pytest

from job_bot.shared.utils import terminal as terminal_module
from job_bot.shared.utils.terminal import (
    print_kitty_image,
    print_sixel_mage,
    setup_terminal,
)


def test_setup_terminal_is_noop_on_linux() -> None:
    """``setup_terminal()`` returns immediately on non-Windows systems."""
    fake_ctypes = mock.MagicMock(name="ctypes")
    with (
        mock.patch.object(platform_module, "system", return_value="Linux"),
        mock.patch.object(terminal_module, "ctypes", fake_ctypes),
    ):
        setup_terminal()
        fake_ctypes.windll.kernel32.GetStdHandle.assert_not_called()


def test_setup_terminal_is_noop_on_darwin() -> None:
    """``setup_terminal()`` returns immediately on macOS."""
    fake_ctypes = mock.MagicMock(name="ctypes")
    with (
        mock.patch.object(platform_module, "system", return_value="Darwin"),
        mock.patch.object(terminal_module, "ctypes", fake_ctypes),
    ):
        setup_terminal()
        fake_ctypes.windll.kernel32.GetStdHandle.assert_not_called()


def test_setup_terminal_windows_path_calls_api() -> None:
    """On Windows, ``setup_terminal()`` calls the kernel32 console API.

    We patch :func:`platform.system` to report "Windows" and replace
    the :mod:`ctypes` module reference inside the terminal module
    with a mock that returns success from ``GetConsoleMode`` so the
    call branch that sets ``ENABLE_VIRTUAL_TERMINAL_PROCESSING`` is
    exercised.
    """
    fake_handle = mock.MagicMock(name="handle")
    fake_mode = mock.MagicMock()
    fake_mode.value = 0
    fake_kernel32 = mock.MagicMock(name="kernel32")
    fake_kernel32.GetStdHandle.return_value = fake_handle
    fake_kernel32.GetConsoleMode.return_value = 1  # nonzero = success
    fake_ctypes = mock.MagicMock(name="ctypes")
    fake_ctypes.windll.kernel32 = fake_kernel32
    fake_ctypes.c_uint.return_value = fake_mode

    with (
        mock.patch.object(platform_module, "system", return_value="Windows"),
        mock.patch.object(terminal_module, "ctypes", fake_ctypes),
    ):
        setup_terminal()
        fake_ctypes.c_uint.assert_called()
        fake_kernel32.GetStdHandle.assert_called_once_with(-11)
        fake_kernel32.SetConsoleMode.assert_called_once()


def test_setup_terminal_windows_path_swallows_errors() -> None:
    """Any Windows API failure is silently swallowed."""
    fake_kernel32 = mock.MagicMock(name="kernel32")
    fake_kernel32.GetStdHandle.side_effect = OSError("nope")
    fake_ctypes = mock.MagicMock(name="ctypes")
    fake_ctypes.windll.kernel32 = fake_kernel32
    fake_ctypes.c_uint.return_value = mock.MagicMock()

    with (
        mock.patch.object(platform_module, "system", return_value="Windows"),
        mock.patch.object(terminal_module, "ctypes", fake_ctypes),
    ):
        # Must not raise.
        setup_terminal()


def test_print_kitty_image_writes_escape_sequence() -> None:
    """``print_kitty_image`` writes a Kitty graphics protocol escape to stdout."""
    data = b"\x89PNG\r\n\x1a\n"  # PNG magic
    buf = io.StringIO()
    with mock.patch("sys.stdout", buf):
        print_kitty_image(data)
    out = buf.getvalue()
    # The escape sequence ``\\033_G`` (``\\x1b_G``) starts the protocol.
    assert "\x1b_G" in out
    # The base64-encoded payload must appear inside the sequence.
    expected_b64 = base64.b64encode(data).decode("ascii")
    assert expected_b64 in out
    # f=100 is the "PNG, auto size" indicator.
    assert "f=100" in out


def test_print_sixel_mage_writes_sixel_sequence(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``print_sixel_mage`` writes a sixel graphics protocol sequence to stdout.

    The PIL-based renderer requires a real :class:`PIL.Image.Image`; we
    patch :class:`PIL.Image.open` to return a tiny in-memory 4x4 RGB
    image and verify that the output starts with a sixel DCS introducer
    and contains palette + raster fragments.
    """
    fake_img = mock.MagicMock(name="PIL.Image")
    fake_img.size = (4, 4)
    # convert('RGB') returns a same object
    fake_img.convert.return_value = fake_img
    # quantize returns a palettised image
    fake_quantized = mock.MagicMock(name="PIL.Image.quantized")
    fake_quantized.getpalette.return_value = [i * 10 for i in range(256 * 3)]
    fake_quantized.size = (4, 4)
    pixels = mock.MagicMock(name="pixels")
    # Each row: a single color, different per band
    pixels.__getitem__.side_effect = lambda xy: xy[0]  # constant per column
    fake_quantized.load.return_value = pixels
    fake_img.quantize.return_value = fake_quantized

    with mock.patch("PIL.Image.open", return_value=fake_img):
        # Force a non-multiplexer env (no ZELLIJ/TMUX)
        old_zellij = os.environ.pop("ZELLIJ", None)
        old_tmux = os.environ.pop("TMUX", None)
        try:
            print_sixel_mage(b"fake png bytes")
        finally:
            if old_zellij is not None:
                os.environ["ZELLIJ"] = old_zellij
            if old_tmux is not None:
                os.environ["TMUX"] = old_tmux

    out = capsys.readouterr().out
    # DCS introducer for sixel is ``\\x1bPq`` (``ESC P q``).
    assert "\x1bPq" in out
    # String terminator ``\\x1b\\`` ends the sixel stream.
    assert "\x1b\\" in out
