# 只读诊断脚本：枚举 MuMu/雷电 窗口结构，测试 PrintWindow 后台截图是否可用
# 不发送任何点击消息，可放心在游戏运行时执行
import ctypes
import os
import sys
import time

import win32con
import win32gui
import win32ui
import numpy as np
import cv2

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'debug_shots', 'diag')
os.makedirs(OUT_DIR, exist_ok=True)
KEYWORDS = ['百崽', 'MuMu', 'LDPlayer']
PW_RENDERFULLCONTENT = 0x00000002


def capture_hwnd(hwnd, flags):
    """对指定窗口执行 PrintWindow，返回 (ok, img_bgr)；窗口太小返回 None"""
    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    w, h = right - left, bottom - top
    if w <= 1 or h <= 1:
        return None
    hwndDC = win32gui.GetWindowDC(hwnd)
    mfcDC = win32ui.CreateDCFromHandle(hwndDC)
    saveDC = mfcDC.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    try:
        bmp.CreateCompatibleBitmap(mfcDC, w, h)
        saveDC.SelectObject(bmp)
        ok = ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), flags)
        info = bmp.GetInfo()
        bits = bmp.GetBitmapBits(True)
        img = np.frombuffer(bits, np.uint8).reshape(
            info['bmHeight'], info['bmWidth'], 4)[:, :, :3].copy()
        return ok, img
    except Exception as e:
        print(f'    capture 异常: {e}')
        return None
    finally:
        try:
            win32gui.DeleteObject(bmp.GetHandle())
            saveDC.DeleteDC()
            mfcDC.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwndDC)
        except Exception:
            pass


found = []


def enum_cb(hwnd, _):
    title = win32gui.GetWindowText(hwnd)
    if title and any(k in title for k in KEYWORDS):
        found.append(hwnd)


win32gui.EnumWindows(enum_cb, None)

if not found:
    print('未找到 MuMu/雷电 窗口，请先启动模拟器和游戏后重试')
    sys.exit(1)

for hwnd in found:
    title = win32gui.GetWindowText(hwnd)
    cls = win32gui.GetClassName(hwnd)
    rect = win32gui.GetWindowRect(hwnd)
    iconic = win32gui.IsIconic(hwnd)
    print(f'\n[顶层] hwnd={hwnd} class={cls!r} title={title!r} rect={rect} minimized={iconic}')
    if iconic:
        print('  已最小化，先恢复窗口再测...')
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        time.sleep(1.5)

    try:
        cl, ct, cr, cb = win32gui.GetClientRect(hwnd)
        ox, oy = win32gui.ClientToScreen(hwnd, (0, 0))
        print(f'  客户区: {cr - cl}x{cb - ct}, 客户区原点屏幕坐标: ({ox}, {oy})')
    except Exception:
        pass

    children = []

    def child_cb(ch, _):
        try:
            children.append((ch, win32gui.GetClassName(ch), win32gui.GetWindowText(ch),
                             win32gui.GetWindowRect(ch)))
        except Exception:
            pass
        return True

    win32gui.EnumChildWindows(hwnd, child_cb, None)
    for ch, c, t, r in children:
        print(f'  [子] hwnd={ch} class={c!r} title={t!r} rect={r}')

    # 对顶层窗口和较大的子窗口分别测试两种 PrintWindow 标志
    cands = [(hwnd, 'top')] + [(ch, 'child') for ch, _, _, r in children
                               if r[2] - r[0] >= 200 and r[3] - r[1] >= 200]
    for cand, kind in cands:
        for flags, tag in ((PW_RENDERFULLCONTENT, 'full'), (0, 'plain')):
            res = capture_hwnd(cand, flags)
            if not res:
                continue
            ok, img = res
            mean, std = float(img.mean()), float(img.std())
            path = os.path.join(OUT_DIR, f'{kind}_{cand}_{tag}.png')
            cv2.imwrite(path, img)
            print(f'  [probe] hwnd={cand}({kind}) flags={flags} ok={ok} '
                  f'size={img.shape[1]}x{img.shape[0]} mean={mean:.1f} std={std:.1f} -> {path}')

print('\n诊断完成：std 很小(≈0)说明截到黑屏/纯色；正常游戏画面 std 一般 > 30')
