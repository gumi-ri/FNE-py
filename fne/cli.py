"""Command line entry point, configuration and the conversion pipeline.

The pipeline lives here and is shared with the GUI (``fne.gui``): ``run``
talks to its caller through callbacks rather than printing directly, so the
CLI and the windowed front end report the same numbers without duplicating
the threading, skipping or error handling.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from tqdm import tqdm

from . import __version__, ncm, qmc2, qqmusic

CONFIG_NAME = "config.json"
AUDIO_EXTENSIONS = {".mp3", ".flac", ".ogg"}


# --------------------------------------------------------------------------
# configuration
# --------------------------------------------------------------------------

def load_config(path: str | None = None) -> dict:
    """Load config.json from next to the executable or the given path."""
    if path is None:
        base = os.path.dirname(os.path.abspath(sys.argv[0]))
        path = os.path.join(base, CONFIG_NAME)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"[warn] could not read {path}: {exc}")
        return {}


# --------------------------------------------------------------------------
# folder selection
# --------------------------------------------------------------------------

def pick_folder(title: str) -> str:
    """Show a native folder dialog, falling back to a text prompt."""
    try:
        import tkinter  # noqa: F401  (availability probe)
        from tkinter import filedialog
        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        selected = filedialog.askdirectory(title=title)
        root.destroy()
        if selected:
            return os.path.normpath(selected)
    except Exception:
        pass
    return input(f"{title}: ").strip().strip('"')


# --------------------------------------------------------------------------
# scanning
# --------------------------------------------------------------------------

def scan_files(directory: str, recursive: bool) -> tuple[list[str], list[str]]:
    ncm_files: list[str] = []
    qmc2_files: list[str] = []

    if recursive:
        for root, _dirs, files in os.walk(directory):
            for name in files:
                _classify(os.path.join(root, name), ncm_files, qmc2_files)
    else:
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if os.path.isfile(path):
                _classify(path, ncm_files, qmc2_files)

    return sorted(ncm_files), sorted(qmc2_files)


def _classify(path: str, ncm_files: list[str], qmc2_files: list[str]) -> None:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".ncm":
        ncm_files.append(path)
    elif qmc2.is_qmc2(ext):
        qmc2_files.append(path)


def build_existing_set(directory: str) -> set[str]:
    """Stems already present in the output folder, for incremental runs.

    Zero-length files are ignored: they are the debris a killed run leaves
    behind, and counting them as converted would silently skip a track.
    """
    found: set[str] = set()
    if not os.path.isdir(directory):
        return found
    for name in os.listdir(directory):
        base, ext = os.path.splitext(name)
        if ext.lower() not in AUDIO_EXTENSIONS:
            continue
        try:
            if os.path.getsize(os.path.join(directory, name)) > 0:
                found.add(base)
        except OSError:
            continue
    return found


# --------------------------------------------------------------------------
# conversion
# --------------------------------------------------------------------------

@dataclass
class Summary:
    """Outcome of one ``run``.

    Returned instead of printed so the CLI and the GUI can present the same
    numbers their own way.
    """
    total: int = 0
    success: int = 0
    skipped: int = 0
    counts: dict[str, int] = field(
        default_factory=lambda: {"flac": 0, "mp3": 0, "ogg": 0})
    errors: list[str] = field(default_factory=list)
    elapsed: float = 0.0
    cancelled: bool = False
    fatal: str | None = None

    @property
    def failed(self) -> int:
        return len(self.errors)

    @property
    def attempted(self) -> int:
        return self.success + self.failed

    @property
    def exit_code(self) -> int:
        return 1 if (self.fatal or self.errors) else 0

    def report(self, input_folder: str, output_folder: str) -> list[str]:
        """The human-readable report, shared by both front ends."""
        lines = [
            "=" * 60,
            f"  Input:     {input_folder}",
            f"  Output:    {output_folder}",
            f"  Total:     {self.total}",
            f"  Success:   {self.success} (FLAC: {self.counts['flac']}, "
            f"MP3: {self.counts['mp3']}, OGG: {self.counts['ogg']})",
            f"  Failed:    {self.failed}",
            f"  Skipped:   {self.skipped}",
        ]
        if self.elapsed > 0:
            rate = f" ({self.attempted / self.elapsed:.1f} files/s)"
            lines.append(f"  Time:      {self.elapsed:.1f}s{rate}")
        if self.cancelled:
            lines.append("  Cancelled: yes, remaining files were not converted")
        if self.fatal:
            lines.append(f"  Error:     {self.fatal}")
        if self.errors:
            lines.append("-" * 60)
            lines.append("  Failed files:")
            lines.extend(f"    - {err}" for err in self.errors)
        lines.append("=" * 60)
        return lines


def convert_one(path: str, output_dir: str,
                verbose: bool = False) -> tuple[str, str | None]:
    """Convert one file. Returns (output format, error message or None)."""
    ext = os.path.splitext(path)[1].lower()
    try:
        if qmc2.is_qmc2(ext):
            return qmc2.convert_file(path, output_dir), None
        return ncm.convert_file(path, output_dir), None
    except Exception as exc:
        if verbose:
            traceback.print_exc()
        return "", f"{os.path.basename(path)}: {exc}"


def run(input_folder: str, output_folder: str, recursive: bool = False,
        workers: int = 8, concurrent: int = 3,
        delay_min: int = 200, delay_max: int = 800,
        verbose: bool = False, on_log=None, on_progress=None,
        stop_event: threading.Event | None = None) -> Summary:
    """Convert every supported file found under *input_folder*.

    Status goes to stdout unless ``on_log`` is given. Progress goes to a
    tqdm bar unless ``on_progress`` is given, in which case it is called as
    ``on_progress(done, total, filename, succeeded)``. ``stop_event`` lets a
    caller abort; already converted files are kept either way.
    """
    log = on_log if on_log is not None else print
    qqmusic.configure(concurrent, delay_min, delay_max)

    if not os.path.isdir(input_folder):
        message = f"input folder does not exist: {input_folder}"
        log(message)
        return Summary(fatal=message)
    os.makedirs(output_folder, exist_ok=True)

    ncm_files, qmc2_files = scan_files(input_folder, recursive)
    existing = build_existing_set(output_folder)

    def not_converted(path: str) -> bool:
        return os.path.splitext(os.path.basename(path))[0] not in existing

    before = len(ncm_files) + len(qmc2_files)
    ncm_files = [p for p in ncm_files if not_converted(p)]
    qmc2_files = [p for p in qmc2_files if not_converted(p)]
    skipped = before - (len(ncm_files) + len(qmc2_files))
    if skipped > 0:
        log(f"Skipped {skipped} already converted files.")

    jobs = ncm_files + qmc2_files
    total = len(jobs)
    summary = Summary(total=total, skipped=skipped)
    if total == 0:
        log("No files to convert.")
        return summary

    results: list[tuple[str, str | None]] = []
    start = time.time()
    bar = None if on_progress else tqdm(
        total=total, desc="Converting", unit="file", ncols=80)

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(convert_one, path, output_folder, verbose): path
                       for path in jobs}
            done = 0
            for future in as_completed(futures):
                if stop_event is not None and stop_event.is_set():
                    summary.cancelled = True
                    for pending in futures:
                        pending.cancel()
                    break
                path = futures[future]
                result = future.result()
                results.append(result)
                done += 1
                if bar is not None:
                    bar.update(1)
                else:
                    on_progress(done, total, os.path.basename(path),
                                result[1] is None)
    finally:
        if bar is not None:
            bar.close()

    summary.success = sum(1 for _fmt, err in results if err is None)
    summary.errors = [err for _fmt, err in results if err]
    for fmt, err in results:
        if err is None and fmt in summary.counts:
            summary.counts[fmt] += 1
    summary.elapsed = time.time() - start
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="fne", description="Convert NCM and QMC2 encrypted music to "
                                "standard audio formats.")
    parser.add_argument("-i", "--input", help="input folder")
    parser.add_argument("-o", "--output", help="output folder")
    parser.add_argument("-c", "--config", help=f"path to {CONFIG_NAME}")
    parser.add_argument("-r", "--recursive", action="store_true",
                        help="scan subfolders too")
    parser.add_argument("-j", "--jobs", type=int, default=8,
                        help="parallel conversion workers (default: 8)")
    parser.add_argument("--api-concurrent", type=int, default=None)
    parser.add_argument("--api-delay-min", type=int, default=None)
    parser.add_argument("--api-delay-max", type=int, default=None)
    parser.add_argument("--verbose", action="store_true",
                        help="print a full traceback for every failure")
    parser.add_argument("-V", "--version", action="version",
                        version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    config = load_config(args.config)

    input_folder = args.input or config.get("inputFolder") or ""
    output_folder = args.output or config.get("outputFolder") or ""
    recursive = args.recursive or bool(config.get("recursive", False))

    if input_folder and not os.path.isdir(input_folder):
        print(f"[warn] configured input folder is not valid: {input_folder}")
        input_folder = ""
    if not input_folder:
        input_folder = pick_folder("选择输入文件夹")
        if not input_folder:
            print("Input folder selection cancelled.")
            return 0
    if not output_folder:
        output_folder = pick_folder("选择输出文件夹")
        if not output_folder:
            print("Output folder selection cancelled.")
            return 0

    summary = run(
        input_folder,
        output_folder,
        recursive=recursive,
        workers=args.jobs,
        concurrent=args.api_concurrent or config.get("apiConcurrent", 3),
        delay_min=args.api_delay_min or config.get("apiDelayMin", 200),
        delay_max=args.api_delay_max or config.get("apiDelayMax", 800),
        verbose=args.verbose,
    )
    for line in summary.report(input_folder, output_folder):
        print(line)
    return summary.exit_code
