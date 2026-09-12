"""Minimal Tk front end for the converter.

Deliberately built on ``tkinter``/``ttk`` so the tool stays dependency free.

Tk is not thread safe: the conversion therefore runs in a worker thread and
communicates through a queue, and *only* the main thread ever touches a
widget. Every callback the worker fires is a plain ``queue.put``.
"""

from __future__ import annotations

import os
import queue
import threading
import traceback

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
except ImportError:
    # Some Python builds ship without tkinter. The package still has to
    # import cleanly (the CLI is unaffected), and ``main`` has to explain
    # the problem rather than dump a traceback.
    tk = None
    filedialog = messagebox = ttk = None

from . import __version__, util
from .cli import load_config, run

POLL_MS = 80
MAX_LOG_LINES = 400

# A Tcl interpreter created straight after another one was torn down can
# intermittently refuse to initialise on Windows, reporting one of its own ttk
# theme scripts as unreadable even though the file is on disk. The attempt
# right after it works. Retrying keeps a healthy double click from being
# turned away over a one-off hiccup; a Tk that is genuinely unusable (no
# display, no Tcl data directory) fails both times and still reports itself.
_TK_ROOT_ATTEMPTS = 2


def create_root():
    """Create the Tk root, retrying once.

    Split into its own function so callers (and tests) can deal with the
    "no display / no tkinter" case without launching a window.
    """
    if tk is None:
        raise RuntimeError(
            "tkinter 未随这个 Python 安装（部分精简版发行版不带 GUI 模块）")

    error = None
    for _ in range(_TK_ROOT_ATTEMPTS):
        try:
            return tk.Tk()
        except Exception as exc:                       # noqa: BLE001
            error = exc
    raise error


class ConverterApp:
    """The window. Owns the widgets and the polling loop."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.messages: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

        self.input_var = tk.StringVar()
        self.output_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=False)
        self.workers_var = tk.IntVar(value=8)
        self.concurrent_var = tk.IntVar(value=3)
        self.status_var = tk.StringVar(value="就绪")

        config = load_config()
        self.input_var.set(config.get("inputFolder", "") or "")
        self.output_var.set(config.get("outputFolder", "") or "")
        self.recursive_var.set(bool(config.get("recursive", False)))

        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(POLL_MS, self._poll)

    # -- layout ------------------------------------------------------------

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(5, weight=1)

        self._folder_row(outer, 0, "输入目录", self.input_var,
                         "选择包含 .ncm / .mflac / .mgg 的文件夹")
        self._folder_row(outer, 1, "输出目录", self.output_var,
                         "解密后的音频保存位置")

        options = ttk.Frame(outer)
        options.grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Checkbutton(options, text="包含子目录",
                        variable=self.recursive_var).pack(side="left")
        ttk.Label(options, text="    并行任务").pack(side="left")
        ttk.Spinbox(options, from_=1, to=32, width=4,
                    textvariable=self.workers_var).pack(side="left", padx=(4, 0))
        ttk.Label(options, text="    接口并发").pack(side="left")
        ttk.Spinbox(options, from_=1, to=8, width=4,
                    textvariable=self.concurrent_var).pack(side="left", padx=(4, 0))

        actions = ttk.Frame(outer)
        actions.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(10, 0))
        actions.columnconfigure(2, weight=1)
        self.start_button = ttk.Button(actions, text="开始转换",
                                       command=self._start)
        self.start_button.grid(row=0, column=0)
        self.stop_button = ttk.Button(actions, text="停止", state="disabled",
                                      command=self._stop)
        self.stop_button.grid(row=0, column=1, padx=(6, 12))
        self.progress = ttk.Progressbar(actions, mode="determinate",
                                        maximum=100)
        self.progress.grid(row=0, column=2, sticky="ew")

        ttk.Label(outer, textvariable=self.status_var).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(6, 6))

        log_frame = ttk.LabelFrame(outer, text="日志", padding=6)
        log_frame.grid(row=5, column=0, columnspan=3, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(log_frame, height=12, wrap="none", state="disabled")
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical",
                               command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

    def _folder_row(self, parent: ttk.Frame, row: int, label: str,
                    var: tk.StringVar, dialog_title: str) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w",
                                           pady=3)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1,
                                                 sticky="ew", padx=6)
        ttk.Button(parent, text="浏览…",
                   command=lambda: self._browse(var, dialog_title)).grid(
            row=row, column=2)

    # -- helpers -----------------------------------------------------------

    def _browse(self, var: tk.StringVar, title: str) -> None:
        chosen = filedialog.askdirectory(title=title,
                                         initialdir=var.get() or None)
        if chosen:
            var.set(os.path.normpath(chosen))

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        excess = int(self.log.index("end-1c").split(".")[0]) - MAX_LOG_LINES
        if excess > 0:
            self.log.delete("1.0", f"{excess + 1}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _set_running(self, running: bool) -> None:
        self.start_button.configure(state="disabled" if running else "normal")
        self.stop_button.configure(state="normal" if running else "disabled")

    # -- actions -----------------------------------------------------------

    def _start(self) -> None:
        input_dir = self.input_var.get().strip()
        output_dir = self.output_var.get().strip()

        if not input_dir or not os.path.isdir(input_dir):
            messagebox.showerror("输入目录无效",
                                 "请选择一个存在的输入目录。")
            return
        if not output_dir:
            messagebox.showerror("输出目录为空", "请选择输出目录。")
            return

        self.stop_event.clear()
        self._set_running(True)
        self.progress.configure(value=0, maximum=100)
        self.status_var.set("正在扫描…")
        self._log(f"开始：{input_dir}  →  {output_dir}")

        self.worker = threading.Thread(
            target=self._work, args=(input_dir, output_dir), daemon=True)
        self.worker.start()

    def _work(self, input_dir: str, output_dir: str) -> None:
        """Runs on the worker thread - must never touch a widget."""
        try:
            summary = run(
                input_dir,
                output_dir,
                recursive=self.recursive_var.get(),
                workers=max(1, int(self.workers_var.get())),
                concurrent=max(1, int(self.concurrent_var.get())),
                on_log=lambda text: self.messages.put(("log", text)),
                on_progress=lambda done, total, name, ok: self.messages.put(
                    ("progress", done, total, name, ok)),
                stop_event=self.stop_event,
            )
            self.messages.put(("done", summary, input_dir, output_dir))
        except Exception:
            self.messages.put(("crashed", traceback.format_exc()))

    def _stop(self) -> None:
        self.stop_event.set()
        self.stop_button.configure(state="disabled")
        self._log("已请求停止，正在等待已开始的文件结束…")

    def _on_close(self) -> None:
        if self.worker is not None and self.worker.is_alive():
            if not messagebox.askokcancel("退出", "转换仍在进行，确定退出吗？"):
                return
            self.stop_event.set()
        self.root.destroy()

    # -- main-thread message pump -----------------------------------------

    def _poll(self) -> None:
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break
            self._handle(message)
        self.root.after(POLL_MS, self._poll)

    def _handle(self, message: tuple) -> None:
        kind = message[0]

        if kind == "log":
            self._log(message[1])

        elif kind == "progress":
            _kind, done, total, name, ok = message
            self.progress.configure(maximum=max(total, 1), value=done)
            self.status_var.set(f"{done} / {total}")
            self._log(("  ✓ " if ok else "  ✗ ") + name)

        elif kind == "done":
            _kind, summary, input_dir, output_dir = message
            for line in summary.report(input_dir, output_dir):
                self._log(line)
            self._set_running(False)
            self.progress.configure(
                value=self.progress.cget("maximum"))
            if summary.fatal:
                self.status_var.set("失败")
            elif summary.cancelled:
                self.status_var.set(f"已停止 · 成功 {summary.success}")
            elif summary.failed:
                self.status_var.set(f"完成 · 失败 {summary.failed}")
            else:
                self.status_var.set(f"完成 · 成功 {summary.success}")

        elif kind == "crashed":
            self._set_running(False)
            self.status_var.set("崩溃")
            self._log(message[1])
            messagebox.showerror("发生异常", message[1].strip().splitlines()[-1])


def _hide_own_console() -> None:
    """Hide the console window, but only if Windows made it for us.

    The frozen build is a console-subsystem executable so that it can also be
    driven from a shell. Double clicking it therefore opens a console window
    that would sit behind the GUI for the whole session. A console inherited
    from the shell the user launched us from must be left alone - hiding the
    user's own terminal would be rude and confusing.
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        window = kernel32.GetConsoleWindow()
        if not window:
            return
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(window, ctypes.byref(owner))
        if owner.value == kernel32.GetCurrentProcessId():
            user32.ShowWindow(window, 0)          # SW_HIDE
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    # The window is not the only thing this prints: a failure to start it
    # explains itself in Chinese, and the frozen interpreter would refuse to
    # write that to a redirected stream.
    util.force_utf8_streams()

    try:
        root = create_root()
    except Exception as exc:  # no display, or tkinter not built in
        print(f"无法启动图形界面：{exc}")
        print("请改用命令行：fne -i <输入目录> -o <输出目录>")
        return 1

    root.title(f"FNE {__version__} · 音乐解密转换")
    root.minsize(680, 460)
    ConverterApp(root)
    _hide_own_console()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
