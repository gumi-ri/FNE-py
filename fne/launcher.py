"""Single entry point for every way this tool is started.

``python -m fne``, the installed ``fne`` console script and the frozen
single-file executable all land here, so the "which front end?" decision is
made in exactly one place.

Absolute imports are deliberate: PyInstaller runs this file as a top-level
script, where relative imports have no package to resolve against.
"""

from __future__ import annotations

import os
import sys

from fne import util
from fne.cli import main as cli_main
from fne.gui import create_root
from fne.gui import main as gui_main

GUI_FLAGS = ("-g", "--gui")
SELFTEST_FLAG = "--selftest"

_REQUIRED_MODULES = ("cryptography", "mutagen", "psutil", "requests", "tqdm")


def selftest() -> int:
    """Report what this build can actually do, then exit.

    A frozen bundle can fail in ways the source tree cannot: a module the
    analyser never saw, tkinter present but unable to find its Tcl data
    directory, a cache directory that turns out to be read-only. Those
    failures are invisible until somebody opens the window, so CI runs this
    against the built executable and the bundled interpreter gets to answer
    for itself.
    """
    import platform

    from fne import __version__, qqmusic

    ok = True
    print(f"fne {__version__} selftest")
    print(f"  frozen     : {util.is_frozen()}")
    print(f"  app dir    : {util.app_dir()}")
    print(f"  python     : {platform.python_version()} ({platform.machine()})")
    # Worth reporting: a bundle whose streams fall back to the ANSI code page
    # cannot print Chinese, which is how the frozen build once died on a pipe.
    print(f"  output enc : {sys.stdout.encoding} "
          f"(utf8 mode: {sys.flags.utf8_mode})")

    for name in _REQUIRED_MODULES:
        try:
            __import__(name)
        except Exception as exc:                       # noqa: BLE001
            ok = False
            print(f"  {name:10s} : FAILED {exc}")
        else:
            print(f"  {name:10s} : ok")

    try:
        import tkinter
    except Exception as exc:                           # noqa: BLE001
        ok = False
        print(f"  tkinter    : FAILED {exc}")
    else:
        print(f"  tkinter    : {tkinter.TkVersion}")
        root = None
        try:
            # Shares the GUI's own root factory, so the self check exercises
            # the exact code path a double click takes.
            root = create_root()
            root.withdraw()
            root.update_idletasks()
        except Exception as exc:                       # noqa: BLE001
            ok = False
            print(f"  tk window  : FAILED {exc}")
        else:
            print("  tk window  : ok")
        finally:
            if root is not None:
                try:
                    root.destroy()
                except Exception:                      # noqa: BLE001
                    pass

    directory = os.path.dirname(qqmusic.CACHE_PATH)
    try:
        os.makedirs(directory, exist_ok=True)
        probe = os.path.join(directory, ".selftest")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.unlink(probe)
    except OSError as exc:
        ok = False
        print(f"  cache dir  : FAILED {exc}")
    else:
        print(f"  cache dir  : writable ({directory})")

    print("selftest:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    """Run the CLI or the GUI.

    Rules, in order:

    * ``--selftest`` reports what this build can do and exits;
    * ``-g`` / ``--gui`` anywhere in the arguments forces the window;
    * no arguments at all means a double click, which means the window;
    * anything else is a command line invocation.

    The "no arguments" rule is what makes a single executable usable both
    ways: the same ``fne.exe`` opens the window when double clicked and
    behaves like a normal CLI tool inside a shell.
    """
    args = list(sys.argv[1:] if argv is None else argv)

    # Before anything can print: the frozen interpreter does not run in UTF-8
    # mode, so Chinese output to a pipe or a log file would crash the process.
    util.force_utf8_streams()

    if SELFTEST_FLAG in args:
        return selftest()

    forced = [a for a in args if a in GUI_FLAGS]
    if forced:
        return gui_main([a for a in args if a not in GUI_FLAGS])
    if not args:
        return gui_main([])
    return cli_main(args)


if __name__ == "__main__":
    raise SystemExit(main())
