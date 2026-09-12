"""Win32 helpers: process lookup, cross-process memory reads, file times.

Process *enumeration* goes through psutil. Reading another process's
memory and setting file creation times have no portable library, so those
two stay as direct ctypes calls -- four well-defined API functions,
nothing else.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

import psutil

PROCESS_VM_READ = 0x0010
PROCESS_QUERY_INFORMATION = 0x0400

MEM_COMMIT = 0x1000
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01

MAX_REGION_SIZE = 200 * 1024 * 1024
USER_SPACE_LIMIT = 0x7FFFFFFFFFFF

FILE_WRITE_ATTRIBUTES = 0x0100
FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
FILE_FLAG_BACKUP_SEMANTICS = 0x02000000

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("PartitionId", wintypes.WORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD),
                ("dwHighDateTime", wintypes.DWORD)]


_k32 = _kernel32
_k32.OpenProcess.restype = wintypes.HANDLE
_k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                   ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.POINTER(ctypes.c_size_t)]
_k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                ctypes.c_void_p, ctypes.c_size_t]
_k32.VirtualQueryEx.restype = ctypes.c_size_t
_k32.CloseHandle.argtypes = [wintypes.HANDLE]
_k32.CreateFileW.restype = wintypes.HANDLE
_k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                             ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                             wintypes.HANDLE]
_k32.GetFileTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(FILETIME),
                             ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)]
_k32.SetFileTime.argtypes = [wintypes.HANDLE, ctypes.POINTER(FILETIME),
                             ctypes.POINTER(FILETIME), ctypes.POINTER(FILETIME)]

INVALID_HANDLE = wintypes.HANDLE(-1).value


def find_pids_by_name(exe_name: str) -> list[int]:
    """Return the PIDs of every running process whose image name matches."""
    target = exe_name.lower()
    return [p.pid for p in psutil.process_iter(["name"])
            if (p.info["name"] or "").lower() == target]


def iter_committed_regions(pid: int):
    """Yield readable, committed memory regions of *pid* as bytes objects."""
    handle = _k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION,
                              False, pid)
    if not handle:
        raise OSError(f"OpenProcess({pid}) failed: {ctypes.get_last_error()}")
    try:
        addr = 0
        mbi = MEMORY_BASIC_INFORMATION()
        read = ctypes.c_size_t(0)
        while True:
            ret = _k32.VirtualQueryEx(handle, ctypes.c_void_p(addr),
                                      ctypes.byref(mbi), ctypes.sizeof(mbi))
            if ret == 0:
                break

            base = mbi.BaseAddress or 0
            size = mbi.RegionSize or 0
            if size == 0:
                break

            if (mbi.State == MEM_COMMIT
                    and not mbi.Protect & PAGE_GUARD
                    and not mbi.Protect & PAGE_NOACCESS
                    and 0 < size < MAX_REGION_SIZE):
                buf = ctypes.create_string_buffer(size)
                if _k32.ReadProcessMemory(handle, ctypes.c_void_p(base), buf,
                                          size, ctypes.byref(read)):
                    if read.value:
                        yield buf[:read.value]

            addr = base + size
            if addr == 0 or addr > USER_SPACE_LIMIT:
                break
    finally:
        _k32.CloseHandle(handle)


def copy_creation_time(src: str, dst: str) -> None:
    """Copy the creation timestamp of *src* onto *dst* (best effort)."""
    flags = FILE_FLAG_BACKUP_SEMANTICS
    h_src = _k32.CreateFileW(src, FILE_WRITE_ATTRIBUTES,
                             FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                             OPEN_EXISTING, flags, None)
    if h_src in (INVALID_HANDLE, 0, None):
        return
    try:
        creation = FILETIME()
        if not _k32.GetFileTime(h_src, ctypes.byref(creation), None, None):
            return
    finally:
        _k32.CloseHandle(h_src)

    h_dst = _k32.CreateFileW(dst, FILE_WRITE_ATTRIBUTES,
                             FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                             OPEN_EXISTING, flags, None)
    if h_dst in (INVALID_HANDLE, 0, None):
        return
    try:
        _k32.SetFileTime(h_dst, ctypes.byref(creation), None, None)
    finally:
        _k32.CloseHandle(h_dst)


def is_windows() -> bool:
    return os.name == "nt"
