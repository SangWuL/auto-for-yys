# 探测：多开时如何把模拟器窗口对应到各自的 adb 端口（只读，不点击）
import ctypes
import os
import subprocess
import sys

import win32con
import win32gui
import win32process

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
kernel32 = ctypes.windll.kernel32


def query_image(pid):
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = ctypes.c_ulong(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return None
    finally:
        kernel32.CloseHandle(handle)


def has_nemu_child(hwnd):
    hits = []

    def cb(child, _):
        try:
            if win32gui.GetClassName(child) == 'nemuwin':
                hits.append(child)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, cb, None)
    except Exception:
        pass
    return bool(hits)


def process_cmdline(pid):
    try:
        ps = (f'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine')
        r = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                           capture_output=True, timeout=20)
        return r.stdout.decode('gbk', 'ignore').strip()
    except Exception as e:
        return f'<失败: {e}>'


# 1. 找出所有带 nemuwin 渲染子窗口的顶层窗口 = 模拟器设备窗口
devices = []


def cb(hwnd, _):
    if win32gui.IsWindowVisible(hwnd) and has_nemu_child(hwnd):
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        devices.append((hwnd, title, pid))
    return True


win32gui.EnumWindows(cb, None)
print(f'=== 设备窗口（带 nemuwin 子窗口）共 {len(devices)} 个 ===')
shell_dir = None
for hwnd, title, pid in devices:
    exe = query_image(pid)
    print(f'\nhwnd={hwnd} title={title!r} pid={pid}')
    print(f'  进程: {exe}')
    if exe:
        shell_dir = os.path.dirname(exe)
    print(f'  命令行: {process_cmdline(pid)}')

# 2. 找 MuMuManager.exe / adb.exe 并查询实例信息
if shell_dir:
    print(f'\n=== shell 目录: {shell_dir} ===')
    print('文件:', [f for f in os.listdir(shell_dir)
                 if f.lower().endswith('.exe')][:20])
    manager = os.path.join(shell_dir, 'MuMuManager.exe')
    if os.path.isfile(manager):
        for args in (['api', '-v', 'all'], ['info', '-v', 'all']):
            try:
                r = subprocess.run([manager] + args, capture_output=True, timeout=20)
                out = r.stdout.decode('utf-8', 'ignore')
                print(f'\n--- MuMuManager {" ".join(args)} ---')
                print(out[:3000])
            except Exception as e:
                print(f'MuMuManager {args} 失败: {e}')
else:
    print('\n未找到设备窗口进程目录')
