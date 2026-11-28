"""Compare one disposable file from regular and low-integrity standard-user processes."""

import argparse
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile


PROBE = r"""@echo off
if "%1"=="low" (
    whoami /groups /fo csv /nh | findstr /l "S-1-16-4096" >nul || exit /b 23
)
type canary.txt >nul 2>nul || exit /b 21
echo write-marker>>canary.txt 2>nul || exit /b 22
exit /b 0
"""


class Label(c.Structure):
    _fields_ = [("sid", w.LPVOID), ("attributes", w.DWORD)]


class Startup(c.Structure):
    _fields_ = [("cb", w.DWORD), ("reserved", w.LPWSTR), ("desktop", w.LPWSTR), ("title", w.LPWSTR),
                ("x", w.DWORD), ("y", w.DWORD), ("width", w.DWORD), ("height", w.DWORD),
                ("cols", w.DWORD), ("rows", w.DWORD), ("fill", w.DWORD), ("flags", w.DWORD),
                ("show", w.WORD), ("reserved_size", w.WORD), ("reserved_bytes", w.LPVOID),
                ("stdin", w.HANDLE), ("stdout", w.HANDLE), ("stderr", w.HANDLE)]


class Process(c.Structure):
    _fields_ = [("process", w.HANDLE), ("thread", w.HANDLE), ("pid", w.DWORD), ("tid", w.DWORD)]


def restricted_child(shell, run, environment):
    kernel = c.WinDLL("kernel32", use_last_error=True)
    security = c.WinDLL("advapi32", use_last_error=True)

    def api(dll, name, result, *args):
        method = getattr(dll, name)
        method.restype, method.argtypes = result, args
        return method

    close = api(kernel, "CloseHandle", w.BOOL, w.HANDLE)
    process = api(kernel, "GetCurrentProcess", w.HANDLE)
    free = api(kernel, "LocalFree", w.LPVOID, w.LPVOID)
    token_pointer = c.POINTER(w.HANDLE)
    open_token = api(security, "OpenProcessToken", w.BOOL, w.HANDLE, w.DWORD, token_pointer)
    restrict = api(security, "CreateRestrictedToken", w.BOOL, w.HANDLE, w.DWORD, w.DWORD, w.LPVOID,
                   w.DWORD, w.LPVOID, w.DWORD, w.LPVOID, token_pointer)
    sid_from_text = api(security, "ConvertStringSidToSidW", w.BOOL, w.LPCWSTR, c.POINTER(w.LPVOID))
    sid_length = api(security, "GetLengthSid", w.DWORD, w.LPVOID)
    set_token = api(security, "SetTokenInformation", w.BOOL, w.HANDLE, c.c_int, w.LPVOID, w.DWORD)
    spawn = api(security, "CreateProcessAsUserW", w.BOOL, w.HANDLE, w.LPCWSTR, w.LPWSTR, w.LPVOID, w.LPVOID,
                w.BOOL, w.DWORD, w.LPVOID, w.LPCWSTR, c.POINTER(Startup), c.POINTER(Process))
    wait = api(kernel, "WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
    terminate = api(kernel, "TerminateProcess", w.BOOL, w.HANDLE, w.UINT)
    exit_code = api(kernel, "GetExitCodeProcess", w.BOOL, w.HANDLE, c.POINTER(w.DWORD))
    original, reduced, sid, child = w.HANDLE(), w.HANDLE(), w.LPVOID(), Process()

    def require(success, stage):
        if not success:
            raise OSError(c.get_last_error(), stage)

    try:
        require(open_token(process(), 0x8B, c.byref(original)), "OpenProcessToken")
        require(restrict(original, 1, 0, None, 0, None, 0, None, c.byref(reduced)), "CreateRestrictedToken")
        require(sid_from_text("S-1-16-4096", c.byref(sid)), "ConvertStringSidToSidW")
        label = Label(sid, 0x20)
        require(set_token(reduced, 25, c.byref(label), c.sizeof(label) + sid_length(sid)), "SetTokenInformation")
        startup = Startup(cb=c.sizeof(Startup))
        args = c.create_unicode_buffer(subprocess.list2cmdline([str(shell), "/d", "/c", "probe.cmd", "low"]))
        env = c.create_unicode_buffer("\0".join(key + "=" + value for key, value in sorted(environment.items())) + "\0\0")
        require(spawn(reduced, str(shell), args, None, None, False, 0x08000400, env, str(run),
                      c.byref(startup), c.byref(child)), "CreateProcessAsUserW")
        if wait(child.process, 15000) != 0:
            terminate(child.process, 2)
            wait(child.process, 5000)
            raise TimeoutError("Restricted child did not exit.")
        result = w.DWORD()
        require(exit_code(child.process, c.byref(result)), "GetExitCodeProcess")
        return result.value
    finally:
        for handle in (child.process, child.thread, original, reduced):
            if handle:
                close(handle)
        if sid:
            free(sid)


def sample(system, run):
    regular = system / "cmd.exe"
    canary = run / "canary.txt"
    canary.write_bytes(b"disposable research canary\n")
    (run / "probe.cmd").write_text(PROBE, encoding="ascii")
    env = {"SystemRoot": str(system.parent), "PATH": str(system)}

    def invoke(exe, cmd):
        return subprocess.run([str(exe), "/d", "/c", cmd], cwd=run, env=env, capture_output=True,
                              timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)

    host_sid = invoke(regular, "whoami /groups /fo csv /nh")
    baseline = invoke(regular, "probe.cmd")
    before_low = canary.read_bytes()
    low_exit = None
    error = None
    try:
        low_exit = restricted_child(regular, run, env)
    except OSError as exc:
        error = {"stage": exc.strerror, "code": exc.errno}
    after_low = canary.read_bytes()
    return {"host_integrity": sorted({int(s) for s in re.findall(rb"S-1-16-(\d+)", host_sid.stdout)}),
            "restricted_launch_error": error, "host_canary_exit": baseline.returncode, "low_canary_exit": low_exit,
            "low_integrity_verified": low_exit in (0, 21, 22),
            "ordinary_write_succeeded": baseline.returncode == 0 and b"write-marker" in before_low,
            "low_can_read_but_write_denied": low_exit == 22 and after_low == before_low,
            "low_write_succeeded": low_exit == 0 and after_low.count(b"write-marker") == 2}


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    if os.name != "nt":
        raise SystemExit("This Windows-only experiment requires Windows.")
    scratch = Path(__file__).resolve().parents[2] / ".tmp"
    scratch.mkdir(exist_ok=True)
    run = Path(tempfile.mkdtemp(prefix="v17-integrity-", dir=scratch))
    system = Path(os.environ["SystemRoot"]) / "System32"
    with tempfile.TemporaryDirectory(prefix="tdflow-v17-integrity-") as second:
        evidence = {"workspace": sample(system, run), "system_temp": sample(system, Path(second)),
                    "windows_sandbox_launcher_present": any((system / name).is_file() for name in (
                        "WindowsSandbox.exe", "WindowsSandboxClient.exe"))}
    (run / "integrity.json").write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence))
    return 0 if all(row["ordinary_write_succeeded"] and row["low_integrity_verified"] for row in (
        evidence["workspace"], evidence["system_temp"])) and evidence["system_temp"]["low_can_read_but_write_denied"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
