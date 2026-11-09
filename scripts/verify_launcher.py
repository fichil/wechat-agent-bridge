"""Exercise the actual Windows launcher against disposable, non-business RPC fixtures.

Requires an interactive Windows desktop for tray assertions. A headless CI host
still checks visible fallback, single instance, busy shutdown and clean exit.
Explorer restart requires an explicit flag on a disposable GitHub Windows runner.
Never touches a real installation's data or processes.
"""
import argparse
import ctypes as c
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bridge_portable.deploy import close_launcher


def wait_for(fn, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = fn()
        if value: return value
        time.sleep(.05)
    raise AssertionError('Launcher condition timed out')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--restart-explorer', action='store_true')
    args = parser.parse_args()
    if args.restart_explorer and (os.environ.get('GITHUB_ACTIONS') != 'true' or os.environ.get('RUNNER_OS') != 'Windows'):
        raise RuntimeError('Explorer restart is restricted to disposable GitHub Windows runners')
    user = c.WinDLL('user32', use_last_error=True)
    callback_type = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    user.EnumWindows.argtypes = [callback_type, w.LPARAM]
    user.GetWindowThreadProcessId.argtypes = [w.HWND, c.POINTER(w.DWORD)]
    user.GetPropW.argtypes = [w.HWND, w.LPCWSTR]; user.GetPropW.restype = w.HANDLE
    user.IsWindowVisible.argtypes = [w.HWND]
    user.IsWindowEnabled.argtypes = [w.HWND]
    user.GetDlgItem.argtypes = [w.HWND, c.c_int]; user.GetDlgItem.restype = w.HWND
    user.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
    user.ShowWindow.argtypes = [w.HWND, c.c_int]
    user.RegisterWindowMessageW.argtypes = [w.LPCWSTR]
    user.GetShellWindow.restype = w.HWND
    class IconIdentifier(c.Structure):
        _fields_ = [('cbSize', w.DWORD), ('hWnd', w.HWND), ('uID', w.UINT), ('guid', c.c_byte * 16)]
    shell = c.WinDLL('shell32')
    shell.Shell_NotifyIconGetRect.argtypes = [c.POINTER(IconIdentifier), c.POINTER(w.RECT)]
    def icon_exists(window):
        identifier = IconIdentifier(c.sizeof(IconIdentifier), window, 1)
        rectangle = w.RECT()
        result = shell.Shell_NotifyIconGetRect(c.byref(identifier), c.byref(rectangle))
        # S_FALSE is a successful result for an icon in Explorer's overflow area.
        return result >= 0 and rectangle.right > rectangle.left and rectangle.bottom > rectangle.top

    def window_for(pid):
        found = []
        @callback_type
        def visit(window, unused):
            owner = w.DWORD(); user.GetWindowThreadProcessId(window, c.byref(owner))
            if owner.value == pid and user.GetPropW(window, 'WeChatAgentBridge.LauncherProtocol'):
                found.append(window)
            return True
        user.EnumWindows(visit, 0)
        return found[0] if found else None

    with tempfile.TemporaryDirectory(prefix='bridge-launcher-') as directory:
        root = Path(directory).resolve()
        shutil.copy2(args.root / 'WeChatAgentBridge.exe', root)
        shutil.copytree(args.root / 'runtime', root / 'runtime')
        (root / 'bridge_portable').mkdir()
        (root / 'bridge_portable/__init__.py').write_text('', encoding='utf-8')
        (root / 'bridge_portable/rpc.py').write_text(
            "import json,sys,time\nr=json.load(sys.stdin)\n"
            "if r['action']=='setup': time.sleep(2)\n"
            "print('continue' if r['action']=='bootstrap' else 'UPDATE:\\nFixture ready' if r['action'].startswith('update-') else 'done')\n", encoding='utf-8')
        data = root / 'data'; data.mkdir()
        (data / 'portable-config.json').write_text('{}', encoding='utf-8')
        (data / 'auth.sqlite3').touch()  # Native startup checks presence only; no real credentials.
        results = {}
        try:
            startup = subprocess.STARTUPINFO()
            startup.dwFlags = subprocess.STARTF_USESHOWWINDOW
            startup.wShowWindow = 0
            process = subprocess.Popen([str(root / 'WeChatAgentBridge.exe'), '--background'], cwd=root, startupinfo=startup)
            window = wait_for(lambda: window_for(process.pid))
            # The protocol property exists before addTray/ShowWindow finish.
            wait_for(lambda: icon_exists(window) or user.IsWindowVisible(window), timeout=30)
            wait_for(lambda: user.IsWindowEnabled(user.GetDlgItem(window, 100)))
            tray_available = not bool(user.IsWindowVisible(window))
            results['tray_available'] = tray_available
            if tray_available: assert icon_exists(window)
            results['background_or_visible_fallback'] = True
            duplicate = subprocess.Popen([str(root / 'WeChatAgentBridge.exe')], cwd=root)
            assert duplicate.wait(20) == 0
            wait_for(lambda: user.IsWindowVisible(window))
            results['duplicate_reveals_same_process'] = process.poll() is None
            user.ShowWindow(window, 6)  # Minimize.
            if tray_available: wait_for(lambda: not user.IsWindowVisible(window))
            else: wait_for(lambda: user.IsWindowVisible(window))
            user.PostMessageW(window, 0x10, 0, 0)
            time.sleep(.2)
            assert process.poll() is None
            results['close_preserves_console'] = True
            # Simulate Explorer's notification only on our disposable window.
            user.PostMessageW(window, user.RegisterWindowMessageW('TaskbarCreated'), 0, 0)
            time.sleep(.2)
            if tray_available:
                wait_for(lambda: icon_exists(window) or user.IsWindowVisible(window))
                results['explorer_notification_restores_icon'] = icon_exists(window)
                results['explorer_notification_visible_fallback'] = bool(user.IsWindowVisible(window))
            if args.restart_explorer:
                shell_window = user.GetShellWindow()
                shell_pid = w.DWORD()
                assert shell_window, 'No desktop shell in disposable runner'
                user.GetWindowThreadProcessId(shell_window, c.byref(shell_pid))
                explorer = psutil.Process(shell_pid.value)
                assert Path(explorer.exe()).resolve() == (Path(os.environ['WINDIR']) / 'explorer.exe').resolve()
                explorer.terminate(); explorer.wait(10)
                deadline = time.monotonic() + 10
                while not user.GetShellWindow() and time.monotonic() < deadline: time.sleep(.1)
                if not user.GetShellWindow():
                    subprocess.Popen([str(Path(os.environ['WINDIR']) / 'explorer.exe')], startupinfo=startup)
                wait_for(lambda: user.GetShellWindow(), timeout=30)
                replacement = w.DWORD()
                user.GetWindowThreadProcessId(user.GetShellWindow(), c.byref(replacement))
                assert replacement.value != shell_pid.value
                try:
                    wait_for(lambda: icon_exists(window), timeout=30)
                except AssertionError:
                    user.FindWindowW.argtypes = [w.LPCWSTR, w.LPCWSTR]; user.FindWindowW.restype = w.HWND
                    identifier = IconIdentifier(c.sizeof(IconIdentifier), window, 1)
                    rectangle = w.RECT()
                    print(json.dumps({'diagnostics': {'visible': bool(user.IsWindowVisible(window)), 'process_alive': process.poll() is None, 'shell': user.GetShellWindow(), 'taskbar': user.FindWindowW('Shell_TrayWnd', None), 'get_rect': shell.Shell_NotifyIconGetRect(c.byref(identifier), c.byref(rectangle)), 'properties': {name:user.GetPropW(window,name) for name in ['Tray.AddCount','Tray.AddResult','Tray.Broadcast','Tray.TimerCount','Tray.ModifyResult']}}}), flush=True)
                    raise
                assert process.poll() is None
                results['real_explorer_restart_restores_icon'] = True
                identifier = IconIdentifier(c.sizeof(IconIdentifier), window, 1)
                rectangle = w.RECT()
                result = shell.Shell_NotifyIconGetRect(c.byref(identifier), c.byref(rectangle))
                results['restored_icon_geometry'] = {'hresult':result, 'rect':[rectangle.left,rectangle.top,rectangle.right,rectangle.bottom]}
            user.PostMessageW(window, user.RegisterWindowMessageW('WeChatAgentBridge.ShowConsole.v1'), 0, 0)
            wait_for(lambda: user.IsWindowVisible(window))
            user.PostMessageW(window, 0x111, 100, 0)  # Synthetic slow setup, no database write.
            wait_for(lambda: not user.IsWindowEnabled(user.GetDlgItem(window, 100)))
            try: close_launcher(root, timeout=.3)
            except RuntimeError: pass
            else: raise AssertionError('Busy operation was interrupted')
            assert process.poll() is None
            wait_for(lambda: user.IsWindowEnabled(user.GetDlgItem(window, 100)))
            results['busy_operation_preserved'] = True
            user.PostMessageW(window, 0x10, 0, 0)
            close_launcher(root)
            assert process.wait(10) == 0
            results['hidden_upgrade_exit'] = True
            (data / 'portable-config.json').unlink()
            process = subprocess.Popen([str(root / 'WeChatAgentBridge.exe'), '--background'], cwd=root, startupinfo=startup)
            window = wait_for(lambda: window_for(process.pid))
            wait_for(lambda: user.IsWindowVisible(window))
            wait_for(lambda: user.IsWindowEnabled(user.GetDlgItem(window, 100)))
            results['first_configuration_visible'] = True
            user.PostMessageW(window, 0x111, 305, 0)
            assert process.wait(10) == 0
            results['explicit_exit'] = True
        finally:
            close_launcher(root)
        print(json.dumps(results), flush=True)


if __name__ == '__main__': main()
