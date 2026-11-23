"""Is the host application's main window minimized? Pure OS query, no `td` (any thread).

Spike S9 (2026-11-23): TouchDesigner 2025.32440 runs **no frames while its window is minimized**, also for
components with their own independent time. The component's main-thread pump then stops although nothing is
wrong. The transport uses this query only to tell that pause apart from a lost owner (see ``pyhttp``): it
never drives a TouchDesigner action and never touches another process's windows.

Windows only (``user32`` through ``ctypes``). Everywhere else, and on any error, the answer is ``None``
("unknown"), which callers must treat as "not minimized".

Review 12: a private ``WinDLL`` instance with explicit argument types is used (the shared
``ctypes.windll.user32`` is process-global inside TouchDesigner and another script could set incompatible
types on it), and any error inside the enumeration callback makes the whole answer unknown instead of a
partial list. None of these calls sends a window message, so a hung window cannot block them.
"""
from __future__ import annotations

import os
import sys
from typing import Callable, List, Optional, Tuple

MAIN_WINDOW_CLASS = "TouchDesigner Window"
_USER32 = None


def _user32():
    global _USER32
    if _USER32 is None:
        import ctypes
        from ctypes import wintypes
        u = ctypes.WinDLL("user32", use_last_error=True)
        proto = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        u.EnumWindows.argtypes, u.EnumWindows.restype = [proto, wintypes.LPARAM], wintypes.BOOL
        u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        u.GetWindowThreadProcessId.restype = wintypes.DWORD
        u.GetClassNameW.argtypes, u.GetClassNameW.restype = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int], ctypes.c_int
        u.IsWindowVisible.argtypes, u.IsWindowVisible.restype = [wintypes.HWND], wintypes.BOOL
        u.IsIconic.argtypes, u.IsIconic.restype = [wintypes.HWND], wintypes.BOOL
        _USER32 = (u, proto)
    return _USER32


def _windows(pid: int) -> List[Tuple[str, bool, bool]]:
    """(class name, visible, iconic) of the top-level windows of one process (Windows). Raises on any error."""
    import ctypes
    from ctypes import wintypes
    u, proto = _user32()
    out: List[Tuple[str, bool, bool]] = []
    failed: List[BaseException] = []

    def cb(hwnd, _):
        try:
            p = wintypes.DWORD()
            u.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value == pid:
                buf = ctypes.create_unicode_buffer(128)
                u.GetClassNameW(hwnd, buf, 128)
                out.append((buf.value, bool(u.IsWindowVisible(hwnd)), bool(u.IsIconic(hwnd))))
        except BaseException as e:               # never let it escape into the OS callback
            failed.append(e)
            return False
        return True
    ok = u.EnumWindows(proto(cb), 0)
    if failed or not ok:
        raise OSError("window enumeration failed")
    return out


def minimized(pid: Optional[int] = None, window_class: str = MAIN_WINDOW_CLASS,
              windows: Optional[Callable[[int], List[Tuple[str, bool, bool]]]] = None,
              platform: Optional[str] = None) -> Optional[bool]:
    """True: every visible main window of the process is minimized. False: at least one is not.
    None: unknown (not Windows, no such window, or the query failed)."""
    if windows is None and (platform or sys.platform) != "win32":
        return None
    try:
        rows = (windows or _windows)(os.getpid() if pid is None else int(pid))
    except Exception:
        return None
    mains = [iconic for cls, visible, iconic in rows if cls == window_class and visible]
    if not mains:
        return None
    return all(mains)
