# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the single-file Windows x64 build.

Produces ``dist/fne.exe``: one file, no Python installation required, and it
serves as both the CLI and the GUI (see ``fne/launcher.py``).

Build with::

    pyinstaller --clean --noconfirm fne.spec

The GUI needs tkinter, which lives in a ``try``/``except ImportError`` block
in ``fne/gui.py`` so the package still imports on interpreters built without
it. PyInstaller scans bytecode rather than executing it, so it does find the
import - the hiddenimports below are belt and braces, because a silently
missing tkinter would ship an executable whose window never opens.
"""

import os
import re

ROOT = os.path.abspath(SPECPATH)

# Read rather than duplicate: the executable's Properties tab and the version
# the tool reports about itself must never be able to disagree.
with open(os.path.join(ROOT, "fne", "__init__.py"), encoding="utf-8") as _fh:
    VERSION = re.search(r'__version__ = "([^"]+)"', _fh.read()).group(1)

VERSION_QUAD = tuple(int(part) for part in VERSION.split("."))
VERSION_QUAD += (0,) * (4 - len(VERSION_QUAD))

_VERSION_FILE = os.path.join(ROOT, "build", "version_info.txt")
os.makedirs(os.path.dirname(_VERSION_FILE), exist_ok=True)
with open(_VERSION_FILE, "w", encoding="utf-8") as _fh:
    _fh.write(f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={VERSION_QUAD}, prodvers={VERSION_QUAD},
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'FNE-py contributors'),
      StringStruct('FileDescription', 'FNE-py 音乐解密转换工具'),
      StringStruct('FileVersion', '{VERSION}'),
      StringStruct('InternalName', 'fne'),
      StringStruct('OriginalFilename', 'fne.exe'),
      StringStruct('ProductName', 'FNE-py'),
      StringStruct('ProductVersion', '{VERSION}'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)""")

HIDDEN_IMPORTS = [
    # GUI - the reason this list exists at all.
    "tkinter",
    "tkinter.filedialog",
    "tkinter.messagebox",
    "tkinter.ttk",
    # Metadata writers, imported by name from submodules.
    "mutagen.flac",
    "mutagen.id3",
    # TLS trust store used by requests.
    "certifi",
]

# Nothing here is a dependency; excluding them guards against a stray
# system-wide install dragging tens of megabytes into the bundle.
EXCLUDES = [
    "numpy", "scipy", "pandas", "matplotlib", "PIL",
    "pytest", "pyflakes", "_pytest",
    "IPython", "notebook",
]

a = Analysis(
    [os.path.join(ROOT, "fne", "launcher.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN_IMPORTS,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="fne",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # Fills in the Windows "Properties -> Details" tab. Worth having on a
    # binary handed around as a download: an unsigned executable with no
    # version resource looks more like malware than it has to.
    version=_VERSION_FILE,
    # upx is not assumed to be installed; compressing a bootloader with it is
    # a well known source of antivirus false positives anyway.
    upx=False,
    runtime_tmpdir=None,
    # Console subsystem on purpose: this one file has to work from a shell
    # too. `fne.gui._hide_own_console` hides the window when the GUI takes
    # over and the console belongs to us.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
