"""GUI behaviour.

The window is optional infrastructure: ``fne-gui`` has to degrade to a
usable message when tkinter is missing (some Python builds ship without it)
or when there is no display. Those paths are tested unconditionally; the
widget tree is only exercised when a display is actually available, so the
suite still passes on a headless runner.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fne import cli, gui  # noqa: E402


def _state(widget) -> str:
    """Read a ttk widget state as a plain string.

    ``cget("state")`` hands back a Tcl index object, not a ``str``, so a
    bare ``== "normal"`` comparison fails even though the value is right.
    """
    return str(widget.cget("state"))


@pytest.fixture
def root():
    """A withdrawn Tk root, skipped when Tk cannot start at all."""
    try:
        handle = gui.create_root()
    except Exception as exc:  # no display, or an incomplete Tcl/Tk install
        pytest.skip(f"Tk unavailable: {exc}")
    handle.withdraw()
    try:
        yield handle
    finally:
        handle.destroy()


def test_main_degrades_when_tkinter_cannot_start(monkeypatch, capsys):
    """A missing display or missing tkinter must give a hint, not a crash."""
    def boom():
        raise RuntimeError("no display name and no $DISPLAY environment variable")

    monkeypatch.setattr(gui, "create_root", boom)

    assert gui.main() == 1
    out = capsys.readouterr().out
    assert "无法启动图形界面" in out
    assert "fne -i" in out                      # points at the CLI fallback


def test_create_root_retries_a_transient_tcl_failure(monkeypatch):
    """One flaky Tcl init must not cost the user their window.

    Seen on Windows: a Tcl interpreter created right after another one was
    destroyed intermittently calls a ttk theme script unreadable, then works
    on the next attempt.
    """
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("couldn't read file .../ttk/vistaTheme.tcl")
        return "root"

    monkeypatch.setattr(gui, "tk", type("FakeTk", (), {"Tk": staticmethod(flaky)}))

    assert gui.create_root() == "root"
    assert len(calls) == 2


def test_create_root_gives_up_on_a_permanently_broken_tk(monkeypatch):
    """Retrying must not disguise a Tk that is really unusable."""
    calls = []

    def broken():
        calls.append(1)
        raise RuntimeError("no display name and no $DISPLAY environment variable")

    monkeypatch.setattr(gui, "tk", type("FakeTk", (), {"Tk": staticmethod(broken)}))

    with pytest.raises(RuntimeError, match="no display"):
        gui.create_root()
    assert len(calls) == gui._TK_ROOT_ATTEMPTS


def test_buttons_follow_the_running_state(root):
    app = gui.ConverterApp(root)

    app._set_running(True)
    assert _state(app.start_button) == "disabled"
    assert _state(app.stop_button) == "normal"

    app._set_running(False)
    assert _state(app.start_button) == "normal"
    assert _state(app.stop_button) == "disabled"


def test_progress_message_drives_the_bar(root):
    app = gui.ConverterApp(root)

    app._handle(("progress", 2, 5, "a.ncm", True))

    assert app.status_var.get() == "2 / 5"
    assert float(app.progress.cget("value")) == 2.0
    assert float(app.progress.cget("maximum")) == 5.0


def test_log_is_trimmed_so_a_long_run_cannot_grow_without_bound(root):
    app = gui.ConverterApp(root)

    for i in range(gui.MAX_LOG_LINES + 30):
        app._log(f"line {i}")

    assert int(app.log.index("end-1c").split(".")[0]) <= gui.MAX_LOG_LINES
    assert "line 0" not in app.log.get("1.0", "end")


@pytest.mark.parametrize("summary,expected", [
    (cli.Summary(total=3, success=3), "完成 · 成功 3"),
    (cli.Summary(total=3, success=2, errors=["x: boom"]), "完成 · 失败 1"),
    (cli.Summary(total=3, success=1, cancelled=True), "已停止 · 成功 1"),
])
def test_completion_sets_the_status_line(root, summary, expected):
    app = gui.ConverterApp(root)

    app._handle(("done", summary, "C:/in", "C:/out"))

    assert app.status_var.get() == expected
    assert _state(app.start_button) == "normal"
