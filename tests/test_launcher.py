"""Front-end dispatch: deciding between the CLI and the window.

This is what lets one executable serve both a double click and a shell, so
the rules are pinned down here rather than discovered by users.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fne import launcher  # noqa: E402


def _record(monkeypatch):
    """Replace both front ends with recorders, returning where calls land."""
    calls = {"cli": [], "gui": []}
    monkeypatch.setattr(
        launcher, "cli_main",
        lambda argv=None: calls["cli"].append(list(argv or [])) or 0)
    monkeypatch.setattr(
        launcher, "gui_main",
        lambda argv=None: calls["gui"].append(list(argv or [])) or 0)
    return calls


def test_no_arguments_opens_the_window(monkeypatch):
    """A bare double click must land in the GUI, not in a console prompt."""
    calls = _record(monkeypatch)

    assert launcher.main([]) == 0

    assert calls["gui"] == [[]]
    assert calls["cli"] == []


def test_command_line_arguments_reach_the_cli(monkeypatch):
    calls = _record(monkeypatch)

    launcher.main(["-i", "in", "-o", "out", "-r"])

    assert calls["cli"] == [["-i", "in", "-o", "out", "-r"]]
    assert calls["gui"] == []


@pytest.mark.parametrize("flag", ["-g", "--gui"])
def test_gui_flag_forces_the_window(monkeypatch, flag):
    calls = _record(monkeypatch)

    launcher.main([flag])

    assert calls["gui"] == [[]]
    assert calls["cli"] == []


def test_gui_flag_is_stripped_from_the_arguments_it_passes_on(monkeypatch):
    """The GUI has no argument parser, so the flag must not survive the trip."""
    calls = _record(monkeypatch)

    launcher.main(["--gui", "--verbose"])

    assert calls["gui"] == [["--verbose"]]


def test_arguments_default_to_sys_argv(monkeypatch):
    calls = _record(monkeypatch)
    monkeypatch.setattr(launcher.sys, "argv", ["fne.exe", "-V"])

    launcher.main()

    assert calls["cli"] == [["-V"]]


# --- the frozen-build self check -------------------------------------------
#
# This is what CI runs against the built executable, so it has to be able to
# both pass and fail honestly.

def _tkinter_available() -> bool:
    """True only if tkinter is importable *and* can actually open a window."""
    import importlib.util

    if importlib.util.find_spec("tkinter") is None:
        return False
    try:
        import tkinter

        root = tkinter.Tk()
        root.destroy()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _tkinter_available(), reason="tkinter not available")
def test_selftest_passes_when_everything_is_present(capsys):
    assert launcher.selftest() == 0

    out = capsys.readouterr().out
    assert "selftest: PASS" in out
    assert "tk window  : ok" in out
    assert "cache dir  : writable" in out


@pytest.mark.skipif(not _tkinter_available(), reason="tkinter not available")
def test_a_transient_tk_failure_is_retried(capsys, monkeypatch):
    """Tcl's theme loading can fail once and succeed on the very next attempt.

    Observed on Windows: after one Tcl interpreter has been created and
    destroyed, the next one intermittently reports a ttk theme script as
    unreadable even though the file is on disk. Condemning a healthy build
    over that would be wrong, so the window check tries again.
    """
    import tkinter

    calls = []

    class _Window:
        def withdraw(self):
            pass

        def update_idletasks(self):
            pass

        def destroy(self):
            pass

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("couldn't read file .../ttk/vistaTheme.tcl")
        return _Window()

    monkeypatch.setattr(tkinter, "Tk", flaky)

    assert launcher.selftest() == 0
    assert len(calls) == 2

    out = capsys.readouterr().out
    assert "tk window  : ok" in out
    assert "selftest: PASS" in out


def test_selftest_reports_a_missing_module_and_fails(capsys, monkeypatch):
    """A module the bundler missed must be caught, not silently tolerated."""
    monkeypatch.setattr(launcher, "_REQUIRED_MODULES",
                        ("cryptography", "definitely_not_installed_xyz"))

    assert launcher.selftest() == 1

    out = capsys.readouterr().out
    assert "definitely_not_installed_xyz" in out
    assert "FAILED" in out
    assert "selftest: FAIL" in out


@pytest.mark.skipif(not _tkinter_available(), reason="tkinter not available")
def test_selftest_reports_a_broken_tk(capsys, monkeypatch):
    """tkinter present but unusable is the failure mode a bundle hits."""
    import tkinter

    def boom(*_args, **_kwargs):
        raise RuntimeError("no display")

    monkeypatch.setattr(tkinter, "Tk", boom)

    assert launcher.selftest() == 1
    assert "tk window  : FAILED" in capsys.readouterr().out


def test_selftest_is_not_confused_with_a_normal_run(monkeypatch):
    calls = _record(monkeypatch)
    monkeypatch.setattr(launcher, "selftest", lambda: 7)

    assert launcher.main(["--selftest"]) == 7
    assert calls["cli"] == []
    assert calls["gui"] == []
