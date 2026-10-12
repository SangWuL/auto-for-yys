import os
import random
import time
from collections import deque
import ctypes
import json
import cv2
import numpy as np
import re
import shutil
import struct
import subprocess
import sys


def _configure_console_output():
    """Prevent legacy GBK consoles from failing on emoji log messages."""
    for stream in (sys.stdout, sys.stderr):
        try:
            # Keep the current console encoding, but replace characters it
            # cannot represent instead of raising UnicodeEncodeError.
            stream.reconfigure(errors='replace')
        except (AttributeError, ValueError):
            pass


_configure_console_output()

# 后台模式依赖 pywin32（pip install pywin32）
try:
    import win32api
    import win32con
    import win32gui
    import win32process
    import win32ui
    HAS_WIN32 = True
except ImportError:
    HAS_WIN32 = False
 
# 让进程声明为 DPI 感知（Per-Monitor V2 优先）

# 否则在 150% 等缩放设置下，系统会把窗口坐标虚拟化缩小 1.5 倍，
# 而 PrintWindow 仍按物理像素抓图，导致截图只覆盖窗口左上角一块、
# 模板坐标与窗口/客户区坐标不在同一尺度，点击位置整体偏移。
def _enable_dpi_awareness():
    """Enable Per-Monitor V2 DPI awareness with explicit ctypes signatures."""
    if not HAS_WIN32:
        return False

    # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 is (HANDLE)-4.
    context_value = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 4
    dpi_context = ctypes.c_void_p(context_value)

    try:
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        set_process = user32.SetProcessDpiAwarenessContext
        set_process.argtypes = [ctypes.c_void_p]
        set_process.restype = ctypes.c_bool
        set_thread = user32.SetThreadDpiAwarenessContext
        set_thread.argtypes = [ctypes.c_void_p]
        set_thread.restype = ctypes.c_void_p

        process_ok = bool(set_process(dpi_context))
        # tkinter or another imported GUI library may have changed this
        # thread's context, so set both the process and current thread.
        previous_thread = set_thread(dpi_context)
        thread_ok = previous_thread not in (None, ctypes.c_void_p(-1).value)
        if process_ok or thread_ok:
            return True
    except Exception:
        pass

    try:
        # Windows 8.1 fallback: Per-Monitor awareness.
        shcore = ctypes.WinDLL('shcore', use_last_error=True)
        set_process = shcore.SetProcessDpiAwareness
        set_process.argtypes = [ctypes.c_int]
        set_process.restype = ctypes.c_long
        if set_process(2) == 0:
            return True
    except Exception:
        pass

    try:
        # Vista fallback: System DPI awareness.
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.SetProcessDPIAware.restype = ctypes.c_bool
        return bool(user32.SetProcessDPIAware())
    except Exception:
        return False


_enable_dpi_awareness()

# === 用户可配置参数区 ===
# 模板匹配参数
TEMPLATES_DIR = 'templates'  # 模板图片目录
TOWER_CHALLENGE_BUTTON = 'tz_button.png'  # 爬塔挑战按钮图片（挑战页右下角，由 make_tower_templates.py 生成）
TOWER_REWARD_TITLE = 'tz_reward.png'  # "获得奖励"结算页标题锚点（由 make_tower_templates.py 生成）
POPUP_CLOSE = 'popup_close.png'  # 悬赏封印等弹窗右上角的关闭红叉（爬塔/结界突破通用）
POPUP_THRESHOLD = 0.80  # 关闭红叉匹配阈值（限定在窗口上部区域搜索，避免误报）
POPUP_REGION = (0.40, 0.04, 0.50, 0.40)  # 红叉搜索区域（相对窗口 x0,y0,w,h 比例，居中弹窗的右上角一带）
THRESHOLD = 0.9  # 模板匹配阈值
TOWER_BUTTON_THRESHOLD = 0.85  # 爬塔挑战按钮匹配阈值（配合多帧位置一致性校验防误报）
TOWER_BUTTON_STABLE_FRAMES = 2  # 需连续命中且位置一致的帧数，防止低阈值带来的瞬时误报
TOWER_REWARD_THRESHOLD = 0.85  # "获得奖励"标题匹配阈值
TOWER_BATTLE_DURATION = 6  # 预计单场战斗时长（秒）：点击挑战后先安静等待该时长再检测结算页，建议设为比正常战斗略短1~2秒
TOWER_BATTLE_TIMEOUT = 10  # 等待结算页出现的总上限（秒，从开始等待战斗结束起算），需大于预计战斗时长
TOWER_UNKNOWN_STATE_TIMEOUT = 90  # 无结算页也无挑战按钮时，等待已知画面的最长时间（秒）
TOWER_REWARD_MAX_CLICKS = 8  # 跳过结算页的最大连续点击次数（应对多层奖励弹窗）
TOWER_REWARD_CLICK_RATIO = (0.88, 0.79)  # 结算页跳过点击位置（相对窗口宽高的比例）
TOWER_REWARD_CLICK_OFFSET = 60  # 结算页跳过点击的随机偏移范围（像素）
TOWER_BATTLE_POLL_INTERVAL = (0.4, 0.6)  # 战斗中轮询结算页的间隔（秒），越短发现挑战结束越快
TOWER_DISMISS_CLICK_INTERVAL = (0.6, 1.0)  # 跳过结算页的点击间隔（秒）
WINDOWS_TITLE = '百崽'
# WINDOWS_TITLE = '雪之莹'

# 运行方式：后台模式（PrintWindow 截图 + adb/PostMessage 点击，模拟器窗口可被遮挡/
# 切到后台，不占用真实鼠标键盘，但窗口不能最小化）
# 命令行 --selftest 只截图自检不点击
# 后台点击实现：'adb' = 经 MuMu 自带 adb 注入安卓触摸事件（MuMu12 的 Qt 窗口收不到
# PostMessage 消息，实测必须走这条路，推荐）；'post' = PostMessage 窗口消息（仅备用）
MSG_CLICK_METHOD = 'adb'
# adb.exe 路径；留空自动检测（PATH -> MuMu 安装目录 shell/ 下 -> 常见安装路径）
ADB_PATH = ''
# adb 连接串；留空自动尝试 MuMu 默认端口 127.0.0.1:16384（多开实例每实例+32）
ADB_SERIAL = ''
# 以下仅在 MSG_CLICK_METHOD='post' 时生效：点击消息发给渲染子窗口 nemudisplay
MSG_CLICK_TO_CHILD = False
PW_RENDERFULLCONTENT = 0x00000002  # PrintWindow 标志：强制 DWM 重绘完整窗口内容

# 多开支持：--multi 一键为每个模拟器窗口各启动一个脚本实例（各自控制台）；
# --title <窗口标题> 锁定某个窗口；--port <adb端口> 锁定 adb 连接；
# --mode 1|2 / --count N 免交互预设模式和次数（多开启动器会自动传给每个实例）
CLI_MODE = None  # 命令行预设的运行模式：'tower' / 'tpjj' / 'qiling'
CLI_COUNT = None  # 命令行预设的挑战次数
EXPLICIT_TITLE = False  # 是否用 --title 显式指定了窗口标题（指定后只按该标题找窗口）

# 点击偏移参数 坐标轴以识别到的图片中心为原点，x轴向右为正，y轴向下为正
CHALLENGE_BUTTON_OFFSET_X = (-50, 50)  # 挑战按钮X轴随机偏移范围
CHALLENGE_BUTTON_OFFSET_Y = (-50, 50)  # 挑战按钮Y轴随机偏移范围
FAILURE_CLICK_OFFSET = 100  # 失败时点击的随机偏移范围
TOWER_FIXED_CLICK_POS = (1115, 398)  # 爬塔失败恢复点击位置（1280x720 归一化坐标）
FIXED_CLICK_OFFSET = 20  # 固定点击位置的随机偏移范围
 
# 其他配置参数
INITIAL_WAIT_TIME = 5  # 脚本启动后的等待时间（秒）
TOWER_MAX_CONSECUTIVE_FAILURES = 6  # 爬塔模式连续失败多少次后停止脚本（失败时会保存画面截图）
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))  # 脚本所在目录
DEBUG_SHOT_DIR = os.path.join(SCRIPT_DIR, 'debug_shots')  # 识别失败时保存画面截图的目录
POST_CHALLENGE_WAIT = (0.2, 0.5)  # 挑战完成后的随机等待时间范围（秒）

# === 归一化渲染坐标配置 ===
# 所有截图识别、ROI 和业务坐标统一在这个固定坐标系内；实际模拟器窗口只要保持同
# 比例，就会在截图时缩放到这里，点击时再反向映射回真实渲染区。
NORMALIZED_RENDER_SIZE = (1280, 720)
NORMALIZED_RENDER_WIDTH, NORMALIZED_RENDER_HEIGHT = NORMALIZED_RENDER_SIZE
# 现有模板和参考截图是 2559x1449，运行时会按该来源尺寸缩放到归一化画布。
TEMPLATE_SOURCE_SIZE = (2559, 1449)

# === 结界突破模式配置 ===
TPJJ_ANCHOR = 'tpjj_anchor.png'  # 结界突破列表页标题锚点（由 make_tpjj_templates.py 生成）
TPJJ_ATTACK_BUTTON = 'tpjj_attack.png'  # 进攻弹窗的进攻按钮
TPJJ_STAMP = 'tpjj_stamp.png'  # 卡片右上角"破"印（已击破标记）
TPJJ_FLAG_MARK = 'tpjj_flagmark.png'  # 卡片右上角金色箭旗（打不过标记）
TPJJ_WIN_BANNER = 'tpjj_win.png'  # 战斗胜利横幅文字（原生分辨率裁剪）
TPJJ_LOSE_BANNER = 'tpjj_lose.png'  # 战斗失败横幅文字（原生分辨率裁剪）
TPJJ_THRESHOLD = 0.85  # 结界突破模式模板匹配阈值
TPJJ_BANNER_THRESHOLD = 0.80  # 胜负横幅匹配阈值
TPJJ_BANNER_DEBOUNCE = 3  # 横幅判定需要在滑动窗口内命中的次数
TPJJ_BANNER_WINDOW = 6  # 滑动窗口轮询次数（结算界面背景有动画，匹配分数会波动）
TPJJ_BANNER_INSTANT_CONF = 0.85  # 单帧达到该分数直接判定（实测真横幅≈0.97，误报≈0.34，且胜负文字只停留约2秒）
TPJJ_BANNER_SCALES = (1.0,)  # 模板已按归一化画布缩放，仅需1:1匹配
TPJJ_BATTLE_DURATION = 8  # 预计单场战斗时长（秒）
TPJJ_BATTLE_TIMEOUT = 60  # 单场战斗最长等待时间（秒）
TPJJ_LIST_TIMEOUT = 120  # 等待回到列表页的最长时间（秒）
TPJJ_CARD_COUNT = 8  # 结界卡片总数
TPJJ_TAP_OFFSET = 30  # 归一化坐标中的常规点击随机偏移范围（像素）

# 参考坐标已经换算为 1280x720 归一化画布；运行时仍用标题锚点校准内容偏移。
TPJJ_REF_SIZE = NORMALIZED_RENDER_SIZE
TPJJ_ANCHOR_REF_CENTER = (638, 93)
TPJJ_CARD_REFS = [
    (581, 203), (920, 203),  # 结界1 左上 / 结界2 右上
    (581, 337), (920, 337),  # 结界3 / 结界4
    (581, 472), (920, 472),  # 结界5 / 结界6
    (581, 600), (920, 600),  # 结界7 / 结界8
]
# 卡片右上角标记中心相对卡片中心的偏移（归一化像素）。
TPJJ_STAMP_REF_OFFSET = (120, -15.0)  # 破印（已击破）
TPJJ_FLAG_REF_OFFSET = (130, -35.0)  # 金色箭旗（打不过）
TPJJ_MARK_THRESHOLD = 0.80  # 标记匹配阈值（破印与箭旗通用）
# 游戏内容原点相对归一化渲染区左上角的偏移，由标题锚点实测位置校准。
TPJJ_CONTENT_OFFSET = None

# === Yuhun mode configuration ===
# Yuhun uses the same single-battle state flow as tower mode, with its own templates.
YUHUN_CHALLENGE_BUTTON = 'yhtz_button.png'
YUHUN_VICTORY = 'shenli.png'
YUHUN_LIST_SINGLE = 'yh_tzlb.png'
YUHUN_BUTTON_THRESHOLD = 0.85
YUHUN_VICTORY_THRESHOLD = 0.85
YUHUN_LIST_SINGLE_THRESHOLD = 0.85
YUHUN_ACTIVE_MEAN_SATURATION = 70
YUHUN_ACTIVE_SATURATION_RATIO = 0.60
YUHUN_VICTORY_MAX_CLICKS = 8
YUHUN_VICTORY_CLICK_OFFSET = 60
YUHUN_VICTORY_CLICK_INTERVAL = (0.6, 1.0)
YUHUN_BATTLE_DURATION = 18  # 预计单场战斗时长（秒）：点击挑战后先安静等待该时长再检测结算页，建议设为比正常战斗略短1~2秒
YUHUN_BATTLE_TIMEOUT = 30  # 等待结算页出现的总上限（秒，从开始等待战斗结束起算），需大于预计战斗时长
FIXED_CLICK_POS = (656, 553)  # 1280x720 归一化坐标中的结算点击位置 (x, y)
YUHUN_POSITION = 'captain' #队长还是队员： 默认队长模式，有挑战按钮

# === 契灵模式配置 ===
# 契灵待挑战页沿用通用挑战按钮；只使用 fqjq.png 识别失败，成功按默认流程处理。
QILING_CHALLENGE_BUTTON = TOWER_CHALLENGE_BUTTON
QILING_FAILURE = 'fqjq.png'
QILING_BUTTON_THRESHOLD = 0.95
QILING_BUTTON_STABLE_FRAMES = 4
QILING_BUTTON_REGION = (0.84, 0.74, 0.16, 0.18)
QILING_BUTTON_TEXT_CROP = None
QILING_CHALLENGE_CLICK_OFFSET = 0
QILING_RESULT_THRESHOLD = 0.95
QILING_FAILURE_REGION = (0.30, 0.80, 0.25, 0.16)
QILING_FAILURE_COLOR_THRESHOLD = 110
QILING_BATTLE_DURATION = 25  # 预计单场战斗时长，先等待后识别结果
QILING_BATTLE_TIMEOUT = 35  # 从进入战斗开始等待结果的最长时间
QILING_RESULT_CLICK_INTERVAL = 3.0  # 结果页固定位置点击间隔
QILING_RESULT_CLICK_OFFSET = YUHUN_VICTORY_CLICK_OFFSET
QILING_FAILURE_ABANDON_POS = (492, 625)  # 失败页“放弃结契”按钮中心（1280x720）
QILING_FAILURE_ABANDON_OFFSET = 28
# ======================
 
# 初始化全局变量
challenge_count = 0
script_start_time = time.time()
running = True  # 全局运行状态标志
total_challenges = 0  # 总挑战次数（0表示无限循环）
current_mode = None  # 当前运行模式：'tower'-爬塔 / 'tpjj'-结界突破 / 'qiling'-契灵
TPJJ_STATS = {'battle': 0, 'win': 0, 'lose': 0}  # 结界突破统计
TARGET_HWND = None  # 后台模式：模拟器顶层窗口句柄（每轮由 msg_refresh_window 刷新）
RENDER_CHILD = None  # 后台模式：渲染子窗口 nemudisplay 句柄
WINDOW_REGION = None  # 顶层窗口实际屏幕区域（left, top, width, height）
RENDER_REGION = None  # 渲染子窗口客户区实际屏幕区域（left, top, width, height）
_PW_FAILED_WARNED = False  # PrintWindow 失败只告警一次，避免刷屏
 
 
# PyInstaller打包支持
def resource_path(relative_path):
    """获取资源的绝对路径，始终以脚本/打包程序所在目录为基准。

    开发环境：使用脚本所在目录（SCRIPT_DIR），不受当前工作目录影响；
    PyInstaller 打包：优先使用可执行文件所在目录（templates/ 部署在程序旁边），
    若该处不存在则回退到解包目录 sys._MEIPASS。
    """
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        if os.path.exists(os.path.join(exe_dir, relative_path)):
            return os.path.join(exe_dir, relative_path)
        return os.path.join(getattr(sys, '_MEIPASS', exe_dir), relative_path)
    return os.path.join(SCRIPT_DIR, relative_path)
 
 
# 调整模板目录路径（支持打包环境）
TEMPLATES_DIR = resource_path(TEMPLATES_DIR)
 
 
# === 后台模式：PrintWindow 截图 + PostMessage 点击 ===
def find_target_hwnd(restore_minimized=False):
    """按标题列表查找模拟器窗口句柄（后台模式用），取面积最大者

    跳过最小化窗口；restore_minimized=True 且只有最小化窗口时，尝试恢复后再返回。
    """
    titles = [WINDOWS_TITLE] if EXPLICIT_TITLE else \
        [WINDOWS_TITLE, "MuMu安卓设备", "LDPlayer", "LDPlayer0"]
    seen, candidates, minimized = set(), [], []

    def _cb(hwnd, _):
        title = win32gui.GetWindowText(hwnd)
        if not title or hwnd in seen or not any(t and t in title for t in titles):
            return True
        seen.add(hwnd)
        try:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        except Exception:
            return True
        if win32gui.IsIconic(hwnd) or left <= -30000 or top <= -30000:
            minimized.append(hwnd)
        elif right - left >= 100 and bottom - top >= 100:
            candidates.append(((right - left) * (bottom - top), hwnd))
        return True

    win32gui.EnumWindows(_cb, None)
    if candidates:
        return max(candidates)[1]

    if minimized and restore_minimized:
        hwnd = minimized[0]
        print(f" 模拟器窗口已最小化，尝试恢复: {win32gui.GetWindowText(hwnd)}")
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            time.sleep(1.5)
        except Exception:
            pass
        if not win32gui.IsIconic(hwnd):
            try:
                left, top, right, bottom = win32gui.GetWindowRect(hwnd)
                if right - left >= 100 and bottom - top >= 100:
                    return hwnd
            except Exception:
                pass
    return None


def msg_find_render_child(hwnd):
    """查找模拟器的渲染子窗口（nemudisplay，类名 nemuwin），找不到返回 None"""
    hits = []

    def _cb(child, _):
        try:
            if win32gui.GetClassName(child) == 'nemuwin':
                hits.append(child)
        except Exception:
            pass
        return True

    try:
        win32gui.EnumChildWindows(hwnd, _cb, None)
    except Exception:
        pass
    return hits[0] if hits else None


def msg_get_render_region(hwnd, render_child=None):
    """返回渲染客户区的实际屏幕区域（left, top, width, height）。

    优先使用 nemuwin/nemudisplay 子窗口，避免把模拟器工具栏、边框或侧栏
    当成游戏画布；没有渲染子窗口时退回顶层窗口客户区。
    """
    target = render_child or msg_find_render_child(hwnd) or hwnd
    try:
        left, top = win32gui.ClientToScreen(target, (0, 0))
        client_rect = win32gui.GetClientRect(target)
        width, height = int(client_rect[2]), int(client_rect[3])
        if width > 1 and height > 1:
            return (int(left), int(top), width, height)
    except Exception:
        pass

    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        return (left, top, right - left, bottom - top)
    except Exception:
        return None


def msg_refresh_window():
    """刷新窗口/渲染区；识别统一使用 1280x720，窗口移动或缩放后自动跟随。"""
    global TARGET_HWND, RENDER_CHILD, WINDOW_REGION, RENDER_REGION, SCREEN_REGION
    hwnd = find_target_hwnd()
    if hwnd is None:
        return False

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    window_rect = (left, top, right - left, bottom - top)
    render_child = msg_find_render_child(hwnd)
    render_rect = msg_get_render_region(hwnd, render_child)
    if render_rect is None:
        return False

    if window_rect != WINDOW_REGION or render_rect != RENDER_REGION:
        print(f" 模拟器窗口区域: {window_rect}，渲染区域: {render_rect}，识别画布归一化为 "
              f"{NORMALIZED_RENDER_WIDTH}x{NORMALIZED_RENDER_HEIGHT}")
        render_aspect = render_rect[2] / render_rect[3]
        normalized_aspect = NORMALIZED_RENDER_WIDTH / NORMALIZED_RENDER_HEIGHT
        aspect_dev = abs(render_aspect - normalized_aspect) / normalized_aspect
        if aspect_dev > 0.02:
            print(f" ⚠️ 渲染区比例 {render_aspect:.4f} 与 1280x720 偏差 "
                  f"{aspect_dev * 100:.1f}%，请保持模拟器窗口同比例")
    WINDOW_REGION = window_rect
    RENDER_REGION = render_rect
    # 业务层只看到固定画布；物理区域仅用于截图和点击反向映射。
    SCREEN_REGION = (0, 0, NORMALIZED_RENDER_WIDTH, NORMALIZED_RENDER_HEIGHT)

    TARGET_HWND = hwnd
    RENDER_CHILD = render_child
    if RENDER_CHILD and MSG_CLICK_TO_CHILD:
        print(f" 后台点击目标: 渲染子窗口 nemudisplay (hwnd={RENDER_CHILD})")
    return True


def normalized_to_screen_point(x, y):
    """将 1280x720 归一化坐标反向映射为渲染区的屏幕绝对坐标。"""
    region = RENDER_REGION or WINDOW_REGION
    if region is None:
        return int(x), int(y)
    left, top, width, height = region
    screen_x = left + float(x) / NORMALIZED_RENDER_WIDTH * width
    screen_y = top + float(y) / NORMALIZED_RENDER_HEIGHT * height
    return int(round(screen_x)), int(round(screen_y))


def _normalized_region_to_screen(region):
    """将归一化 ROI 映射为实际渲染区中的屏幕区域。"""
    left, top, width, height = region
    x0, y0 = normalized_to_screen_point(left, top)
    x1, y1 = normalized_to_screen_point(left + width, top + height)
    return x0, y0, max(1, x1 - x0), max(1, y1 - y0)


def msg_capture_window():
    """PrintWindow 截取整个模拟器窗口（含边框），窗口被遮挡也能拿到画面

    返回 (BGR图像, 窗口left, top, width, height)；失败返回 None。
    """
    global _PW_FAILED_WARNED
    hwnd = TARGET_HWND
    if not hwnd:
        return None
    hwndDC = mfcDC = saveDC = bmp = None
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        w, h = right - left, bottom - top
        if w <= 1 or h <= 1:
            return None
        hwndDC = win32gui.GetWindowDC(hwnd)
        mfcDC = win32ui.CreateDCFromHandle(hwndDC)
        saveDC = mfcDC.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(mfcDC, w, h)
        saveDC.SelectObject(bmp)
        ok = ctypes.windll.user32.PrintWindow(hwnd, saveDC.GetSafeHdc(), PW_RENDERFULLCONTENT)
        info = bmp.GetInfo()
        bits = bmp.GetBitmapBits(True)
        img = np.frombuffer(bits, np.uint8).reshape(
            info['bmHeight'], info['bmWidth'], 4)[:, :, :3].copy()
        if not ok and not _PW_FAILED_WARNED:
            _PW_FAILED_WARNED = True
            print(" PrintWindow 返回失败，画面可能不完整（黑屏时可尝试切换 MuMu 渲染模式）")
        return img, left, top, w, h
    except Exception as e:
        print(f" 后台截图异常: {e}")
        return None
    finally:
        try:
            if bmp is not None:
                win32gui.DeleteObject(bmp.GetHandle())
            if saveDC is not None:
                saveDC.DeleteDC()
            if mfcDC is not None:
                mfcDC.DeleteDC()
            if hwndDC is not None:
                win32gui.ReleaseDC(hwnd, hwndDC)
        except Exception:
            pass


def msg_screenshot_region(region):
    """截图归一化 ROI，并返回与 ROI 同尺寸的图像。

    ``region`` 使用 1280x720 归一化坐标；实际截图先从渲染客户区裁剪，再
    缩放回归一化尺寸，因此模板匹配不会受模拟器窗口大小影响。
    """
    cap = msg_capture_window()
    if cap is None:
        return None
    img, wl, wt, ww, wh = cap
    if RENDER_REGION is None and WINDOW_REGION is None:
        return None

    sx, sy, sw, sh = _normalized_region_to_screen(region)
    x0, y0 = sx - wl, sy - wt
    x1, y1 = x0 + sw, y0 + sh
    ix0, iy0 = max(0, int(x0)), max(0, int(y0))
    ix1, iy1 = min(ww, int(x1)), min(wh, int(y1))
    out_w, out_h = max(1, int(region[2])), max(1, int(region[3]))
    if ix1 <= ix0 or iy1 <= iy0:
        return None

    crop = img[iy0:iy1, ix0:ix1]
    if crop.shape[1] == out_w and crop.shape[0] == out_h:
        return crop.copy()
    return cv2.resize(crop, (out_w, out_h), interpolation=cv2.INTER_AREA)


def msg_resolve_click_target(rx, ry):
    """决定点击消息发给谁，返回 (hwnd, 客户区x, 客户区y)

    MSG_CLICK_TO_CHILD=True 时发给渲染子窗口 nemudisplay（真实点击也落在它上面），
    找不到该子窗口时退回顶层窗口。
    """
    if not MSG_CLICK_TO_CHILD or not RENDER_CHILD:
        return TARGET_HWND, int(rx), int(ry)
    # 顶层客户区坐标 -> 屏幕坐标 -> 渲染子窗口客户区坐标
    sx, sy = win32gui.ClientToScreen(TARGET_HWND, (int(rx), int(ry)))
    ox, oy = win32gui.ClientToScreen(RENDER_CHILD, (0, 0))
    return RENDER_CHILD, int(sx - ox), int(sy - oy)


# --- adb 点击通道：MuMu12 的 Qt 窗口收不到 PostMessage，改在安卓层直接注入触摸 ---
_ADB = {'exe': None, 'serial': None, 'size': None, 'tap_size': None, 'fail_time': 0.0,
        'manager': None}
_ADB_ASPECT_WARNED = False


def _query_process_image(pid):
    """返回进程 exe 的完整路径，失败返回 None"""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.windll.kernel32
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


def adb_exe():
    """定位 adb.exe：配置项 -> PATH -> 模拟器进程同目录（MuMu 的 shell 目录）-> 常见安装路径"""
    if _ADB['exe']:
        return _ADB['exe']
    candidates = [ADB_PATH, shutil.which('adb')]
    if TARGET_HWND:
        try:
            _, pid = win32process.GetWindowThreadProcessId(TARGET_HWND)
            exe_path = _query_process_image(pid)
            if exe_path:
                candidates.append(os.path.join(os.path.dirname(exe_path), 'adb.exe'))
        except Exception:
            pass
    for fixed in (r"C:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
                  r"C:\Program Files\Netease\MuMuPlayerGlobal-12.0\shell\adb.exe",
                  r"D:\Program Files\Netease\MuMuPlayer-12.0\shell\adb.exe",
                  r"C:\Program Files (x86)\Nemu\EmulatorShell\adb.exe"):
        candidates.append(fixed)
    for c in candidates:
        if c and os.path.isfile(c):
            _ADB['exe'] = c
            return c
    return None


def mumu_manager_exe():
    """定位 MuMuManager.exe：adb 同目录 -> 设备进程目录 -> 安装根目录 nx_main/shell 下"""
    if _ADB['manager']:
        return _ADB['manager']
    cands = []
    adb = adb_exe()
    if adb:
        cands.append(os.path.join(os.path.dirname(adb), 'MuMuManager.exe'))
    if TARGET_HWND:
        try:
            _, pid = win32process.GetWindowThreadProcessId(TARGET_HWND)
            exe_path = _query_process_image(pid)
            if exe_path:
                d = os.path.dirname(exe_path)
                cands.append(os.path.join(d, 'MuMuManager.exe'))
                root = d.split(os.sep + 'nx_device' + os.sep)[0]
                cands.append(os.path.join(root, 'nx_main', 'MuMuManager.exe'))
                cands.append(os.path.join(root, 'shell', 'MuMuManager.exe'))
        except Exception:
            pass
    for c in cands:
        if c and os.path.isfile(c):
            _ADB['manager'] = c
            return c
    return None


def mumu_instances(manager):
    """运行 MuMuManager info -v all，返回 {index: 实例信息}（含 name/adb_port/pid/main_wnd）"""
    try:
        result = subprocess.run([manager, 'info', '-v', 'all'], capture_output=True,
                                timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
        text = result.stdout.decode('utf-8', 'ignore')
        start, end = text.find('{'), text.rfind('}')
        if start < 0 or end <= start:
            return {}
        data = json.loads(text[start:end + 1])
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def adb_pick_serial():
    """确定本窗口对应的 adb 连接串（多开的关键：窗口与实例端口必须一一对应）

    优先级：ADB_SERIAL 配置 > MuMuManager 按窗口句柄(main_wnd)/进程 pid 精确映射
    > 返回 None（由调用方按常用端口扫描，仅单开时安全）。
    """
    if ADB_SERIAL:
        return ADB_SERIAL
    if not TARGET_HWND:
        return None
    hwnd_pid = win32process.GetWindowThreadProcessId(TARGET_HWND)[1]
    manager = mumu_manager_exe()
    if manager:
        for info in mumu_instances(manager).values():
            try:
                main_wnd = int(str(info.get('main_wnd') or '0'), 16)
            except ValueError:
                main_wnd = 0
            if main_wnd == TARGET_HWND or (hwnd_pid and info.get('pid') == hwnd_pid):
                port = info.get('adb_port')
                if port:
                    host = info.get('adb_host_ip') or '127.0.0.1'
                    return f"{host}:{port}"
    return None


def adb_run(args, serial=None, timeout=10):
    """执行 adb 命令并返回 stdout；失败返回 None"""
    exe = adb_exe()
    if not exe:
        return None
    cmd = [exe]
    use_serial = serial or _ADB['serial']
    if use_serial:
        cmd += ['-s', use_serial]
    cmd += args
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        return result.stdout.decode('utf-8', 'ignore').strip()
    except Exception as e:
        print(f" adb 命令执行失败: {e}")
        return None


def adb_tap_size():
    """用一次 screencap 读取当前旋转下的实际画面尺寸（PNG 头里取宽高）

    横屏游戏时 wm size 报的是竖屏尺寸（如 720x1280），而 input tap/swipe 的坐标系
    跟随当前旋转，必须以实际画面为准（如 1280x720）。失败返回 None。
    """
    exe = adb_exe()
    if not exe or not _ADB['serial']:
        return None
    try:
        result = subprocess.run([exe, '-s', _ADB['serial'], 'exec-out', 'screencap', '-p'],
                                capture_output=True, timeout=20,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        data = result.stdout
        if len(data) > 24 and data[:8] == b'\x89PNG\r\n\x1a\n':
            w, h = struct.unpack('>II', data[16:24])
            return (int(w), int(h))
    except Exception:
        pass
    return None


def adb_setup():
    """连接模拟器 adb 并读取安卓分辨率，成功返回 True（失败后 2 分钟内不重复尝试）"""
    if _ADB['serial'] and _ADB['size']:
        return True
    if time.time() - _ADB['fail_time'] < 120:
        return False
    if not adb_exe():
        print("❌ 未找到 adb.exe：请把 MuMu 安装目录 shell/adb.exe 填到 ADB_PATH，或加入 PATH")
        _ADB['fail_time'] = time.time()
        return False
    picked = adb_pick_serial()
    if picked:
        serials = [picked]
    else:
        serials = [f"127.0.0.1:{port}" for port in (16384, 16416, 16448, 16480, 5555, 5557)]
        print("ℹ️ 未能从 MuMuManager 精确映射本窗口的 adb 端口，按常用端口顺序尝试"
              "（单开无影响；多开请用 --port 明确指定，避免连到其他实例）")
    for serial in serials:
        try:
            subprocess.run([_ADB['exe'], 'connect', serial], capture_output=True, timeout=10,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        except Exception:
            pass
        out = adb_run(['shell', 'wm', 'size'], serial=serial) or ''
        match = re.search(r'Physical size:\s*(\d+)x(\d+)', out)
        if match:
            _ADB['serial'] = serial
            _ADB['size'] = (int(match.group(1)), int(match.group(2)))
            tap_size = adb_tap_size()
            _ADB['tap_size'] = tap_size or _ADB['size']
            extra = (f"，点击坐标系 {tap_size[0]}x{tap_size[1]}（按当前旋转）"
                     if tap_size else "（screencap 失败，点击坐标系退回 wm size）")
            print(f"🛰️ adb 已连接 {serial}，安卓分辨率 {_ADB['size'][0]}x{_ADB['size'][1]}{extra}")
            return True
    print("❌ adb 连不上模拟器：请确认 MuMu 已启动且 adb 调试开启（默认端口 16384）")
    _ADB['fail_time'] = time.time()
    return False


def adb_click(x, y):
    """把 1280x720 归一化坐标反向映射为安卓坐标并注入点击。"""
    global _ADB_ASPECT_WARNED
    if not adb_setup():
        return False
    region = RENDER_REGION
    if region is None:
        return False
    _, _, client_w, client_h = region
    if client_w <= 0 or client_h <= 0:
        return False
    android_w, android_h = _ADB['tap_size'] or _ADB['size']
    ratio_dev = abs(client_w / client_h - android_w / android_h) / (android_w / android_h)
    if ratio_dev > 0.02 and not _ADB_ASPECT_WARNED:
        _ADB_ASPECT_WARNED = True
        print(f"⚠️ 渲染区宽高比与安卓分辨率偏差 {ratio_dev * 100:.1f}%，点击可能少量偏移，"
              f"建议把模拟器窗口调整为与 {android_w}x{android_h} 同比例")

    # 先从归一化画布反向映射到渲染区，再按安卓当前旋转后的分辨率注入。
    render_x = float(x) / NORMALIZED_RENDER_WIDTH * client_w
    render_y = float(y) / NORMALIZED_RENDER_HEIGHT * client_h
    tap_x = int(render_x / client_w * android_w)
    tap_y = int(render_y / client_h * android_h)
    tap_x = max(0, min(android_w - 1, tap_x))
    tap_y = max(0, min(android_h - 1, tap_y))
    hold_ms = random.randint(60, 160)  # 模拟按住时长
    out = adb_run(['shell', 'input', 'swipe',
                   str(tap_x), str(tap_y), str(tap_x), str(tap_y), str(hold_ms)])
    return out is not None


def msg_click(x, y):
    """后台点击（坐标为 1280x720 归一化坐标）。"""
    if MSG_CLICK_METHOD == 'adb' and adb_click(x, y):
        return
    hwnd = TARGET_HWND
    if not hwnd:
        print(" 后台点击失败：未找到模拟器窗口句柄")
        return
    try:
        screen_x, screen_y = normalized_to_screen_point(x, y)
        origin_x, origin_y = win32gui.ClientToScreen(hwnd, (0, 0))
    except Exception as e:
        print(f" 后台点击失败：{e}")
        return
    rx, ry = int(screen_x) - origin_x, int(screen_y) - origin_y
    if rx < 0 or ry < 0 or rx > 32000 or ry > 32000:
        print(f" 后台点击坐标越界: 归一化({int(x)}, {int(y)}) -> 客户区({rx}, {ry})，本次不点击")
        return
    target, cx, cy = msg_resolve_click_target(rx, ry)
    lparam = win32api.MAKELONG(cx, cy)
    win32gui.PostMessage(target, win32con.WM_LBUTTONDOWN, win32con.MK_LBUTTON, lparam)
    time.sleep(0.05 + random.random() * 0.15)
    win32gui.PostMessage(target, win32con.WM_LBUTTONUP, 0, lparam)


def run_backend_selftest():
    """后台模式自检：只做 PrintWindow 截图并保存，不做任何点击，供人工确认画面有效"""
    print("=== 后台模式自检（只截图，不点击）===")
    if not msg_refresh_window():
        print("❌ 未找到模拟器窗口，请先启动 MuMu 和游戏")
        return
    print(f"顶层窗口句柄: {TARGET_HWND}, 渲染子窗口: {RENDER_CHILD}, 区域: {SCREEN_REGION}")
    cap = msg_capture_window()
    if cap is None:
        print("❌ 后台截图失败，请检查模拟器窗口是否正常（可尝试切换 MuMu 渲染模式）")
        return
    img, _, _, _, _ = cap
    os.makedirs(DEBUG_SHOT_DIR, exist_ok=True)
    path = os.path.join(DEBUG_SHOT_DIR, 'backend_selftest.png')
    cv2.imwrite(path, img)
    print(f"整窗截图 {img.shape[1]}x{img.shape[0]}，亮度均值 {img.mean():.1f}，"
          f"标准差 {img.std():.1f}，已保存: {path}")
    if img.std() < 5:
        print("⚠️ 画面接近纯色（疑似黑屏）：可尝试切换 MuMu 渲染模式（设置→显卡渲染模式）")
    else:
        print("✅ 截图链路正常，请打开图片确认能看到完整游戏画面（含模拟器边框/侧边栏）")

    if MSG_CLICK_METHOD == 'adb':
        if adb_setup():
            print(" 点击通道(adb)连通性测试：音量+/- 各一次（仅验证注入通道，画面无副作用）...")
            adb_run(['shell', 'input', 'keyevent', '24'])
            time.sleep(0.3)
            adb_run(['shell', 'input', 'keyevent', '25'])
            print("✅ adb 点击通道就绪")
        else:
            print("⚠️ adb 不可用，后台模式将无法点击，请按上方提示配置")


# SCREEN_REGION 是归一化坐标系，不是物理屏幕坐标；物理区域保存在 WINDOW_REGION/RENDER_REGION。
SCREEN_REGION = (0, 0, NORMALIZED_RENDER_WIDTH, NORMALIZED_RENDER_HEIGHT)
 

def human_like_click(x=None, y=None, jitter=10):
    """模拟人类点击行为：向模拟器窗口注入后台点击，不占用真实鼠标"""
    if x is not None and y is not None:
        # 添加随机偏移
        offset_x = random.randint(-jitter, jitter)
        offset_y = random.randint(-jitter, jitter)
        msg_click(x + offset_x, y + offset_y)
 
 
def take_safe_screenshot(region):
    """安全地截取屏幕区域，防止无效参数

    后台模式走 PrintWindow：窗口被遮挡也能截图；
    截图失败返回全黑图（模板自然匹配不上，等下一轮窗口恢复）。
    """
    img = msg_screenshot_region(region)
    if img is None:
        return np.zeros((max(1, int(region[3])), max(1, int(region[2])), 3), dtype=np.uint8)
    return img
 
 
def resize_template_to_normalized(template, scale=1.0):
    """将来源于 2559x1449 参考图的模板缩放到归一化画布尺寸。"""
    source_w, source_h = TEMPLATE_SOURCE_SIZE
    target_w = max(1, int(round(template.shape[1] * NORMALIZED_RENDER_WIDTH / source_w * scale)))
    target_h = max(1, int(round(template.shape[0] * NORMALIZED_RENDER_HEIGHT / source_h * scale)))
    if target_w == template.shape[1] and target_h == template.shape[0]:
        return template
    interpolation = cv2.INTER_AREA if target_w <= template.shape[1] else cv2.INTER_CUBIC
    return cv2.resize(template, (target_w, target_h), interpolation=interpolation)


def find_template(template_filename, threshold=THRESHOLD, return_best=False):
    """在屏幕上查找模板图像（增强错误处理）

    return_best=True 时即使低于阈值也返回最高分位置及其置信度，供失败日志排查使用。
    """
    template_path = os.path.join(TEMPLATES_DIR, template_filename)
    if not os.path.exists(template_path):
        print(f" 模板图片未找到: {template_path}")
        return None, 0
 
    try:
        # 读取模板图片
        template = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
        if template is None:
            print(f" 无法读取模板图片: {template_path}")
            return None, 0
 
        # 安全截图
        screen_cv = take_safe_screenshot(SCREEN_REGION)
 
        # 屏幕截图已是归一化画布，模板也必须先换算到同一坐标系。
        screen_height, screen_width = screen_cv.shape[:2]
        scaled_template = resize_template_to_normalized(template)

        # 检查模板尺寸是否过大
        if scaled_template.shape[0] > screen_height or scaled_template.shape[1] > screen_width:
            print(
                f" 模板尺寸({scaled_template.shape[1]}x{scaled_template.shape[0]})超过屏幕尺寸({screen_width}x{screen_height})，跳过匹配")
            return None, 0

        h, w = scaled_template.shape[:2]
 
        # 处理透明通道
        if scaled_template.shape[2] == 4:
            alpha = scaled_template[:, :, 3]
            template_rgb = scaled_template[:, :, :3]
            result = cv2.matchTemplate(screen_cv, template_rgb, cv2.TM_CCORR_NORMED, mask=alpha)
        else:
            result = cv2.matchTemplate(screen_cv, scaled_template, cv2.TM_CCOEFF_NORMED)
 
        # 获取匹配结果
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
 
        if max_val > threshold:
            center_x = SCREEN_REGION[0] + max_loc[0] + w // 2
            center_y = SCREEN_REGION[1] + max_loc[1] + h // 2
            return (center_x, center_y), max_val

        if return_best:
            center_x = SCREEN_REGION[0] + max_loc[0] + w // 2
            center_y = SCREEN_REGION[1] + max_loc[1] + h // 2
            return (center_x, center_y), max_val

        return None, max_val
 
    except Exception as e:
        print(f" 模板匹配错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return None, 0
 
 
def find_template_in_region(template_filename, region, threshold=THRESHOLD,
                            return_best=False, template_crop=None,
                            normalize_template=True):
    """在归一化渲染区域（left, top, width, height）内查找模板图像

    return_best=True 时即使低于阈值也返回最高分位置及其置信度，供失败日志排查使用。
    """
    template_path = os.path.join(TEMPLATES_DIR, template_filename)
    if not os.path.exists(template_path):
        print(f" 模板图片未找到: {template_path}")
        return None, 0

    try:
        template = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
        if template is None:
            print(f" 无法读取模板图片: {template_path}")
            return None, 0

        region_cv = take_safe_screenshot(region)
        region_height, region_width = region_cv.shape[:2]
        scaled_template = (resize_template_to_normalized(template)
                           if normalize_template else template)
        if template_crop is not None:
            x0, y0, x1, y1 = template_crop
            template_height, template_width = scaled_template.shape[:2]
            scaled_template = scaled_template[
                int(template_height * y0):int(template_height * y1),
                int(template_width * x0):int(template_width * x1)]

        if scaled_template.shape[0] > region_height or scaled_template.shape[1] > region_width:
            return None, 0

        h, w = scaled_template.shape[:2]

        if scaled_template.shape[2] == 4:
            alpha = scaled_template[:, :, 3]
            template_rgb = scaled_template[:, :, :3]
            result = cv2.matchTemplate(region_cv, template_rgb, cv2.TM_CCORR_NORMED, mask=alpha)
        else:
            result = cv2.matchTemplate(region_cv, scaled_template, cv2.TM_CCOEFF_NORMED)

        result[~np.isfinite(result)] = 0  # 屏幕纯色区域会得到 inf/nan，置零防止误报
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        if max_val > threshold:
            center_x = region[0] + max_loc[0] + w // 2
            center_y = region[1] + max_loc[1] + h // 2
            return (center_x, center_y), max_val

        if return_best:
            center_x = region[0] + max_loc[0] + w // 2
            center_y = region[1] + max_loc[1] + h // 2
            return (center_x, center_y), max_val

        return None, max_val

    except Exception as e:
        print(f" 区域模板匹配错误: {str(e)}")
        return None, 0


def clamp_to_window(x, y):
    """将坐标限制在模拟器窗口范围内"""
    left, top, width, height = SCREEN_REGION
    return (max(left, min(left + width - 1, int(x))),
            max(top, min(top + height - 1, int(y))))


def tower_get_button_region():
    """挑战按钮的搜索区域（窗口右下角，比例制），避免匹配到画面上其他"挑战"字样"""
    left, top, width, height = SCREEN_REGION
    return (int(left + width * 0.70), int(top + height * 0.60),
            int(width * 0.30), int(height * 0.40))


def tower_find_challenge_button(verbose=True):
    """在右下角区域查找挑战按钮：阈值放宽，连续两帧位置一致才认定有效

    按钮带动画时置信度会波动，固定高阈值容易漏检；低阈值带来的瞬时误报
    由多帧位置一致性校验挡住。未找到时打印最高置信度及其落点，便于排查。
    返回 (位置, 置信度)，未确认时位置为None。
    """
    region = tower_get_button_region()
    pos, conf = find_template_in_region(TOWER_CHALLENGE_BUTTON, region,
                                        threshold=TOWER_BUTTON_THRESHOLD, return_best=True)
    if not pos or conf < TOWER_BUTTON_THRESHOLD:
        if verbose:
            print(f" 未找到挑战按钮 (最高置信度: {conf:.4f}" + (f" @ {pos}" if pos else "") + ")")
        return None, conf

    for _ in range(TOWER_BUTTON_STABLE_FRAMES - 1):
        time.sleep(0.25)
        pos2, conf2 = find_template_in_region(TOWER_CHALLENGE_BUTTON, region,
                                              threshold=TOWER_BUTTON_THRESHOLD, return_best=True)
        # if not pos2 or conf2 < TOWER_BUTTON_THRESHOLD \
        #         or abs(pos2[0] - pos[0]) > 40 or abs(pos2[1] - pos[1]) > 40:
        #     if verbose:
        #         print(f" 挑战按钮识别不稳定 (conf={conf2:.4f} @ {pos2})，本帧不点击")
        #     return None, conf
        pos, conf = pos2, conf2
    return pos, conf


def tower_find_reward_title():
    """检测"获得奖励"结算页标题是否在画面上，返回 (位置, 置信度)"""
    return find_template(TOWER_REWARD_TITLE, threshold=TOWER_REWARD_THRESHOLD)


def close_popup_if_open():
    """检测并关闭挡画面的弹窗（悬赏封印邀请等）：点击其右上角关闭红叉（爬塔/结界突破通用）

    红叉只在窗口上部区域搜索（POPUP_REGION），避免误匹配其他画面的相似元素。
    关闭成功返回True，画面上没有弹窗返回False。
    """
    left, top, width, height = SCREEN_REGION
    x0, y0, w0, h0 = POPUP_REGION
    region = (int(left + x0 * width), int(top + y0 * height),
              int(w0 * width), int(h0 * height))
    pos, conf = find_template_in_region(POPUP_CLOSE, region,
                                        threshold=POPUP_THRESHOLD)
    if not pos:
        return False
    click_x, click_y = clamp_to_window(
        pos[0] + random.randint(-12, 12), pos[1] + random.randint(-12, 12))
    print(f"🚪 检测到弹窗关闭红叉 @ {pos} (置信度: {conf:.4f})，点击关闭...")
    human_like_click(click_x, click_y)
    time.sleep(random.uniform(0.8, 1.2))  # 等弹窗收起
    return True


def tower_wait_for_reward_title(timeout=None, battle_duration=None):
    """战斗进行中：先安静等待预计战斗时长，再开始轮询"获得奖励"结算页，超时返回None

    预计时长内不做任何检测以减少开销；战斗提前结束时结算页会停在画面上，
    最多晚发现到预计时长结束。timeout 从进入本函数起算，需大于预计战斗时长。
    """
    timeout = TOWER_BATTLE_TIMEOUT if timeout is None else timeout
    battle_duration = TOWER_BATTLE_DURATION if battle_duration is None else battle_duration
    settle_wait = float(battle_duration)
    print(f"⚔️ 战斗进行中，预计 {settle_wait:.1f}秒 后结束，届时开始检测结算页（上限 {timeout}秒）...")
    start_time = time.time()

    # 预计战斗时长内安静等待（分片睡眠，保持可中断）
    while running and time.time() - start_time < settle_wait:
        remaining = settle_wait - (time.time() - start_time)
        time.sleep(max(0.05, min(0.5, remaining)))

    print("🔍 开始检测结算页...")
    while running and time.time() - start_time < timeout:
        # 弹窗会挡住结算页标题，先关掉再继续检测
        if close_popup_if_open():
            continue
        pos, _ = tower_find_reward_title()
        if pos:
            print(f" 挑战完成，结算页出现 @ {pos}")
            return pos
        time.sleep(random.uniform(*TOWER_BATTLE_POLL_INTERVAL))
    print("⏰ 等待结算页超时")
    return None


def tower_wait_for_any_anchor(timeout=TOWER_UNKNOWN_STATE_TIMEOUT):
    """画面状态未知（战斗中/转场动画/残留界面）：等待结算页或挑战按钮任一出现

    期间不做任何点击。返回 'reward' / 'button'，超时返回None。
    """
    print(f"🔍 画面上暂无已知锚点，等待 {timeout}秒内出现结算页或挑战按钮...")
    start_time = time.time()
    while running and time.time() - start_time < timeout:
        # 弹窗会挡住锚点，先关掉
        close_popup_if_open()
        pos, _ = tower_find_reward_title()
        if pos:
            return 'reward'
        pos, _ = tower_find_challenge_button(verbose=False)
        if pos:
            return 'button'
        time.sleep(random.uniform(0.6, 0.9))
    return None


def tower_dismiss_reward_screens():
    """连续点击跳过"获得奖励"结算页，直到标题锚点消失（应对多层奖励弹窗）

    点击窗口右下区域（与旧版福袋后的点击位置一致），每次点击前重新检测。
    返回True表示结算页已全部关闭。
    """
    for _ in range(TOWER_REWARD_MAX_CLICKS):
        if not running:
            return False
        pos, _ = tower_find_reward_title()
        if not pos:
            return True
        left, top, width, height = SCREEN_REGION
        base_x = left + TOWER_REWARD_CLICK_RATIO[0] * width
        base_y = top + TOWER_REWARD_CLICK_RATIO[1] * height
        click_x, click_y = clamp_to_window(
            int(base_x) + random.randint(-TOWER_REWARD_CLICK_OFFSET, TOWER_REWARD_CLICK_OFFSET),
            int(base_y) + random.randint(-TOWER_REWARD_CLICK_OFFSET, TOWER_REWARD_CLICK_OFFSET))
        print(f" 跳过结算页点击 @ ({click_x}, {click_y})")
        human_like_click(click_x, click_y)
        time.sleep(random.uniform(*TOWER_DISMISS_CLICK_INTERVAL))
    pos, _ = tower_find_reward_title()
    return not pos


def save_debug_screenshot(tag='tower_fail'):
    """保存当前模拟器画面，便于排查识别失败时的游戏状态"""
    try:
        os.makedirs(DEBUG_SHOT_DIR, exist_ok=True)
        path = os.path.join(DEBUG_SHOT_DIR, f"{tag}_{time.strftime('%Y%m%d_%H%M%S')}.png")
        cv2.imwrite(path, take_safe_screenshot(SCREEN_REGION))
        print(f" 📸 已保存调试截图: {path}")
    except Exception as e:
        print(f" 保存调试截图失败: {e}")


def tower_recovery_click():
    """失败恢复：在固定位置附近随机偏移点击，尝试关闭残留弹窗"""
    offset_x = random.randint(-FIXED_CLICK_OFFSET, FIXED_CLICK_OFFSET)
    offset_y = random.randint(-FIXED_CLICK_OFFSET, FIXED_CLICK_OFFSET)
    click_x, click_y = clamp_to_window(TOWER_FIXED_CLICK_POS[0] + offset_x,
                                       TOWER_FIXED_CLICK_POS[1] + offset_y)
    print(f" 执行固定位置点击: ({click_x}, {click_y})")
    human_like_click(click_x, click_y)
 
 
def yuhun_button_is_active(position, region):
    """Reject the grey/disabled Yuhun button after template matching."""
    template_path = os.path.join(TEMPLATES_DIR, YUHUN_CHALLENGE_BUTTON)
    template = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
    if template is None:
        return False

    template = resize_template_to_normalized(template)
    screenshot = take_safe_screenshot(region)
    height, width = template.shape[:2]
    x = int(round(position[0] - region[0] - width / 2))
    y = int(round(position[1] - region[1] - height / 2))
    x2, y2 = x + width, y + height
    if x < 0 or y < 0 or x2 > screenshot.shape[1] or y2 > screenshot.shape[0]:
        return False

    patch = screenshot[y:y2, x:x2]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    if template.ndim == 3 and template.shape[2] == 4:
        mask = template[:, :, 3] > 30
    else:
        mask = np.ones((height, width), dtype=bool)
    saturation = hsv[:, :, 1][mask]
    if saturation.size == 0:
        return False

    mean_saturation = float(np.mean(saturation))
    saturated_ratio = float(np.mean(saturation > 45))
    return (mean_saturation >= YUHUN_ACTIVE_MEAN_SATURATION and
            saturated_ratio >= YUHUN_ACTIVE_SATURATION_RATIO)


def yuhun_find_challenge_button(verbose=True):
    """Find the Yuhun start-battle button in the same lower-right area as tower mode."""
    region = tower_get_button_region()
    pos, conf = find_template_in_region(
        YUHUN_CHALLENGE_BUTTON, region,
        threshold=YUHUN_BUTTON_THRESHOLD, return_best=True)
    if not pos or conf < YUHUN_BUTTON_THRESHOLD:
        if verbose:
            print(f"Yuhun start button not found (best confidence: {conf:.4f})")
        return None, conf, None

    if not yuhun_button_is_active(pos, region):
        if verbose:
            print("Yuhun start button is greyed out; waiting without clicking")
        return None, conf, "list"

    # Require the same number of consecutive hits used by tower mode.
    for _ in range(TOWER_BUTTON_STABLE_FRAMES - 1):
        time.sleep(0.25)
        pos2, conf2 = find_template_in_region(
            YUHUN_CHALLENGE_BUTTON, region,
            threshold=YUHUN_BUTTON_THRESHOLD, return_best=True)
        if not pos2 or conf2 < YUHUN_BUTTON_THRESHOLD:
            return None, conf, None
        if not yuhun_button_is_active(pos2, region):
            if verbose:
                print("Yuhun start button became greyed out; waiting without clicking")
            return None, conf, "list"
        pos, conf = pos2, conf2
    return pos, conf, "list"


def yuhun_find_victory():
    """Find the Yuhun victory screen used to advance back to the list."""
    return find_template(YUHUN_VICTORY, threshold=YUHUN_VICTORY_THRESHOLD)

def yuhun_back_list():
    """make sure be back to the list."""
    return find_template(YUHUN_LIST_SINGLE, threshold=YUHUN_LIST_SINGLE_THRESHOLD)


def yuhun_wait_for_victory(timeout=None, battle_duration=None):
    """Wait for the Yuhun victory screen after starting a battle."""
    timeout = YUHUN_BATTLE_TIMEOUT if timeout is None else timeout
    battle_duration = YUHUN_BATTLE_DURATION if battle_duration is None else battle_duration
    settle_wait = float(battle_duration)
    print(f"Yuhun battle running; waiting about {settle_wait:.1f}s before detection...")
    start_time = time.time()

    while running and time.time() - start_time < settle_wait:
        remaining = settle_wait - (time.time() - start_time)
        time.sleep(max(0.05, min(0.5, remaining)))

    while running and time.time() - start_time < timeout:
        if close_popup_if_open():
            continue
        pos, conf = yuhun_find_victory()
        if pos:
            print(f"Yuhun victory detected at {pos} (confidence: {conf:.4f})")
            return pos
        time.sleep(random.uniform(*TOWER_BATTLE_POLL_INTERVAL))

    print("Timed out waiting for Yuhun victory screen")
    return None


def yuhun_return_to_list():
    """Click the victory screen until it disappears and the list becomes available."""
    for _ in range(YUHUN_VICTORY_MAX_CLICKS):
        if not running:
            return False
        pos, _ = yuhun_back_list()
        if pos:
            return True
        
        click_x, click_y = clamp_to_window(
            FIXED_CLICK_POS[0] + random.randint(-YUHUN_VICTORY_CLICK_OFFSET,
                                    YUHUN_VICTORY_CLICK_OFFSET),
            FIXED_CLICK_POS[1] + random.randint(-YUHUN_VICTORY_CLICK_OFFSET,
                                    YUHUN_VICTORY_CLICK_OFFSET))
        print(f"Clicking Yuhun victory screen at ({click_x}, {click_y})")
        human_like_click(click_x, click_y)
        time.sleep(random.uniform(0.12, 0.25))
        human_like_click(click_x, click_y)
        time.sleep(random.uniform(1.5, 1.8))
        # time.sleep(random.uniform(*YUHUN_VICTORY_CLICK_INTERVAL))

    # pos, _ = yuhun_find_victory()
    # return not pos
    return False


def yuhun_wait_for_any_anchor(timeout=TOWER_UNKNOWN_STATE_TIMEOUT):
    """Wait until either the Yuhun start button or victory screen is visible."""
    start_time = time.time()
    while running and time.time() - start_time < timeout:
        close_popup_if_open()
        pos, _ = yuhun_find_victory()
        if pos:
            return 'victory'
        pos, _, _ = yuhun_find_challenge_button(verbose=False)
        if pos:
            return 'button'
        time.sleep(random.uniform(0.6, 0.9))
    return None


def qiling_find_challenge_button(verbose=True):
    """在契灵待挑战页查找通用挑战按钮。"""
    left, top, width, height = SCREEN_REGION
    x_ratio, y_ratio, w_ratio, h_ratio = QILING_BUTTON_REGION
    region = (int(left + width * x_ratio), int(top + height * y_ratio),
              int(width * w_ratio), int(height * h_ratio))
    pos, conf = find_template_in_region(
        QILING_CHALLENGE_BUTTON, region,
        threshold=QILING_BUTTON_THRESHOLD, return_best=True,
        template_crop=QILING_BUTTON_TEXT_CROP,
        normalize_template=False)
    if not pos or conf < QILING_BUTTON_THRESHOLD:
        if verbose:
            print(f"未找到契灵挑战按钮 (最高置信度: {conf:.4f})")
        return None, conf

    for _ in range(QILING_BUTTON_STABLE_FRAMES - 1):
        time.sleep(0.25)
        pos2, conf2 = find_template_in_region(
            QILING_CHALLENGE_BUTTON, region,
            threshold=QILING_BUTTON_THRESHOLD, return_best=True,
            template_crop=QILING_BUTTON_TEXT_CROP,
            normalize_template=False)
        if (not pos2 or conf2 < QILING_BUTTON_THRESHOLD
                or abs(pos2[0] - pos[0]) > 25
                or abs(pos2[1] - pos[1]) > 25):
            if verbose:
                print(f"契灵挑战按钮位置不稳定 (conf={conf2:.4f} @ {pos2})")
            return None, conf
        pos, conf = pos2, conf2
    return pos, conf


def qiling_find_result():
    """只识别契灵失败结果；未识别到失败时返回 None。"""
    left, top, width, height = SCREEN_REGION
    x_ratio, y_ratio, w_ratio, h_ratio = QILING_FAILURE_REGION
    failure_region = (int(left + width * x_ratio), int(top + height * y_ratio),
                      int(width * w_ratio), int(height * h_ratio))
    failure_pos, failure_conf = find_template_in_region(
        QILING_FAILURE, failure_region, threshold=QILING_RESULT_THRESHOLD)
    if failure_pos:
        fx, fy = QILING_FAILURE_ABANDON_POS
        color_region = (int(left + fx - 45), int(top + fy - 25), 90, 50)
        color_sample = take_safe_screenshot(color_region)
        mean_value = cv2.cvtColor(color_sample, cv2.COLOR_BGR2HSV)[:, :, 2].mean()
        if mean_value < QILING_FAILURE_COLOR_THRESHOLD:
            failure_pos = None
    if failure_pos:
        return 'failure', failure_pos, failure_conf
    return None, None, failure_conf


def qiling_wait_for_result(timeout=None, battle_duration=None):
    """先等待预估战斗时长，再识别契灵成功/失败结果。"""
    timeout = QILING_BATTLE_TIMEOUT if timeout is None else timeout
    battle_duration = QILING_BATTLE_DURATION if battle_duration is None else battle_duration
    settle_wait = float(battle_duration)
    print(f"契灵战斗进行中，预计 {settle_wait:.1f} 秒后开始识别结果（上限 {timeout} 秒）...")
    start_time = time.time()

    while running and time.time() - start_time < settle_wait:
        remaining = settle_wait - (time.time() - start_time)
        time.sleep(max(0.05, min(0.5, remaining)))

    print("开始检测契灵失败结果...")
    while running and time.time() - start_time < timeout:
        if close_popup_if_open():
            continue
        result, pos, conf = qiling_find_result()
        if result:
            print(f"契灵结契{result} @ {pos} (置信度: {conf:.4f})")
            return result
        time.sleep(random.uniform(0.4, 0.6))

    if running:
        print("未检测到契灵结契失败，按成功处理")
        return 'success'
    return None


def qiling_click_fixed_position():
    """沿用御魂结算页固定位置，点击一次并加入少量随机偏移。"""
    click_x, click_y = clamp_to_window(
        FIXED_CLICK_POS[0] + random.randint(-QILING_RESULT_CLICK_OFFSET,
                                             QILING_RESULT_CLICK_OFFSET),
        FIXED_CLICK_POS[1] + random.randint(-QILING_RESULT_CLICK_OFFSET,
                                             QILING_RESULT_CLICK_OFFSET))
    print(f"契灵结果页点击固定位置: ({click_x}, {click_y})")
    human_like_click(click_x, click_y)


def qiling_click_failure_abandon():
    """点击失败页左下方“放弃结契”按钮，返回契灵待挑战页。"""
    click_x, click_y = clamp_to_window(
        QILING_FAILURE_ABANDON_POS[0] + random.randint(
            -QILING_FAILURE_ABANDON_OFFSET, QILING_FAILURE_ABANDON_OFFSET),
        QILING_FAILURE_ABANDON_POS[1] + random.randint(
            -QILING_FAILURE_ABANDON_OFFSET, QILING_FAILURE_ABANDON_OFFSET))
    print(f"契灵失败页点击“放弃结契”: ({click_x}, {click_y})")
    human_like_click(click_x, click_y)


def qiling_return_to_list(result):
    """失败等待后点击“放弃结契”；成功每隔3秒点击固定位置三次。"""
    if result == 'failure':
        print("契灵结契失败，点击“放弃结契”...")
        if running:
            qiling_click_failure_abandon()
            print("已点击“放弃结契”，等待 3 秒返回待挑战页...")
            time.sleep(3)
            if running:
                qiling_click_fixed_position()
        return

    if result == 'success':
        print("契灵结契成功，按 3 秒间隔点击固定位置三次...")
        for _ in range(3):
            if not running:
                return
            time.sleep(QILING_RESULT_CLICK_INTERVAL)
            qiling_click_fixed_position()


def qiling_wait_for_any_anchor(timeout=TOWER_UNKNOWN_STATE_TIMEOUT):
    """等待契灵待挑战按钮或结契结果锚点出现。"""
    start_time = time.time()
    while running and time.time() - start_time < timeout:
        close_popup_if_open()
        pos, _ = qiling_find_challenge_button(verbose=False)
        if pos:
            return 'button'
        result, _, _ = qiling_find_result()
        if result:
            return result
        time.sleep(random.uniform(0.6, 0.9))
    return None


def ensure_qiling_templates():
    """检查契灵模式所需模板。"""
    if not os.path.exists(TEMPLATES_DIR):
        print(f"模板目录不存在: {TEMPLATES_DIR}")
        return False

    required_templates = [
        QILING_CHALLENGE_BUTTON, QILING_FAILURE, POPUP_CLOSE
    ]
    missing_files = [
        name for name in required_templates
        if not os.path.exists(os.path.join(TEMPLATES_DIR, name))
    ]
    if missing_files:
        print("缺少契灵模板图片:")
        for name in missing_files:
            print(f"  - {name}")
        return False
    return True


def qiling_main_loop():
    """契灵模式：待挑战页 -> 战斗 -> 结契结果 -> 待挑战页。"""
    global challenge_count, running, total_challenges

    if not ensure_qiling_templates():
        return
    get_challenge_count()

    print("\n=== 契灵自动挑战助手启动 ===")
    print(f"模拟器区域: {SCREEN_REGION}")
    print(f"挑战次数: {'无限循环' if total_challenges == 0 else total_challenges}次")
    print(f"预计战斗时长: {QILING_BATTLE_DURATION}秒")
    print(f"结果页固定点击位置: {FIXED_CLICK_POS}，成功点击两次，间隔 {QILING_RESULT_CLICK_INTERVAL}秒")

    for i in range(INITIAL_WAIT_TIME, 0, -1):
        print(f"开始倒计时: {i}秒...")
        time.sleep(1)

    consecutive_failures = 0
    while running:
        if total_challenges > 0 and challenge_count >= total_challenges:
            print(f"已完成设定的 {total_challenges} 次契灵挑战")
            running = False
            break
        if consecutive_failures >= TOWER_MAX_CONSECUTIVE_FAILURES:
            print(f"连续 {consecutive_failures} 次契灵流程异常，脚本停止")
            running = False
            break

        if not msg_refresh_window():
            print("未检测到模拟器窗口，5秒后重试...")
            time.sleep(5)
            continue
        if close_popup_if_open():
            continue

        position, confidence = qiling_find_challenge_button()
        if position and running:
            consecutive_failures = 0
            challenge_count += 1
            progress = (f" ({challenge_count}/{total_challenges})"
                        if total_challenges > 0 else "")
            print(f"\n=== 第 {challenge_count}{progress} 次契灵挑战开始 ===")
            print(f"发现挑战按钮 @ {position} (置信度: {confidence:.4f})")

            offset_x = random.randint(-QILING_CHALLENGE_CLICK_OFFSET,
                                      QILING_CHALLENGE_CLICK_OFFSET)
            offset_y = random.randint(-QILING_CHALLENGE_CLICK_OFFSET,
                                      QILING_CHALLENGE_CLICK_OFFSET)
            click_x, click_y = clamp_to_window(
                position[0] + offset_x, position[1] + offset_y)
            human_like_click(click_x, click_y, jitter=0)
            time.sleep(0.5)
            human_like_click(click_x, click_y, jitter=0)

            result = qiling_wait_for_result()
            if not result:
                consecutive_failures += 1
                save_debug_screenshot(tag='qiling_result_timeout')
                continue

            qiling_return_to_list(result)
            post_wait = random.uniform(*POST_CHALLENGE_WAIT)
            wait_start = time.time()
            while running and time.time() - wait_start < post_wait:
                time.sleep(0.1)
            continue

        # 待挑战按钮优先于结果锚点，避免待挑战页的背景纹理误匹配结果图。
        result, _, result_conf = qiling_find_result()
        if result:
            print(f"检测到残留契灵结契结果: {result} (置信度: {result_conf:.4f})")
            qiling_return_to_list(result)
            continue

        if qiling_wait_for_any_anchor() is None:
            consecutive_failures += 1
            save_debug_screenshot(tag='qiling_unknown_state')
            qiling_click_fixed_position()
            time.sleep(random.uniform(1.5, 3.0))


def ensure_yuhun_templates():
    """Check templates required by Yuhun mode."""
    if not os.path.exists(TEMPLATES_DIR):
        print(f"Template directory not found: {TEMPLATES_DIR}")
        return False

    required_templates = [YUHUN_CHALLENGE_BUTTON, YUHUN_VICTORY, POPUP_CLOSE]
    missing_files = [
        name for name in required_templates
        if not os.path.exists(os.path.join(TEMPLATES_DIR, name))
    ]
    if missing_files:
        print("Missing Yuhun templates:")
        for name in missing_files:
            print(f"  - {name}")
        return False
    return True


def yuhun_main_loop():
    """Run the Yuhun start-battle -> victory -> list loop."""
    global challenge_count, running, total_challenges

    if not ensure_yuhun_templates():
        return
    get_challenge_count()

    print("\n=== Yuhun auto battle started ===")
    print(f"Simulator region: {SCREEN_REGION}")
    print(f"Battle count: {'unlimited' if total_challenges == 0 else total_challenges}")
    print(f"Battle duration estimate: {YUHUN_BATTLE_DURATION}s")

    for i in range(INITIAL_WAIT_TIME, 0, -1):
        print(f"Starting in {i}s...")
        time.sleep(1)

    consecutive_failures = 0
    while running:
        if total_challenges > 0 and challenge_count >= total_challenges:
            print(f"Completed the configured {total_challenges} Yuhun battles")
            running = False
            break
        if consecutive_failures >= TOWER_MAX_CONSECUTIVE_FAILURES:
            print("Too many consecutive Yuhun failures; stopping")
            running = False
            break

        if not msg_refresh_window():
            print("Simulator window unavailable; retrying in 5s...")
            time.sleep(5)
            continue

        if close_popup_if_open():
            continue

        # A victory screen can remain after the previous battle; dismiss it first.
        victory_pos, victory_conf = yuhun_find_victory()
        if victory_pos:
            print(f"Found Yuhun victory screen (confidence: {victory_conf:.4f})")
            if not yuhun_return_to_list():
                consecutive_failures += 1
                save_debug_screenshot(tag='yuhun_return_fail')
            continue

        if YUHUN_POSITION == 'captain': 
            position, confidence, _ = yuhun_find_challenge_button()
            if position and running:
                consecutive_failures = 0
                challenge_count += 1
                progress = (f" ({challenge_count}/{total_challenges})"
                            if total_challenges > 0 else "")
                print(f"\n=== Yuhun battle {challenge_count}{progress} ===")
                print(f"Start button: {position} (confidence: {confidence:.4f})")

                offset_x = random.randint(*CHALLENGE_BUTTON_OFFSET_X)
                offset_y = random.randint(*CHALLENGE_BUTTON_OFFSET_Y)
                click_x, click_y = clamp_to_window(
                    position[0] + offset_x, position[1] + offset_y)
                human_like_click(click_x, click_y)
                time.sleep(0.5)
                human_like_click(click_x, click_y)

                # Give the game time to enter battle before polling for victory.
                time.sleep(1.2)
                if not yuhun_wait_for_victory():
                    consecutive_failures += 1
                    save_debug_screenshot(tag='yuhun_battle_timeout')
                    continue

                if not yuhun_return_to_list():
                    consecutive_failures += 1
                    save_debug_screenshot(tag='yuhun_return_fail')
                    continue

                time.sleep(random.uniform(*POST_CHALLENGE_WAIT))
                continue

        # Unknown state: wait without clicking, then use the existing recovery path.
        if yuhun_wait_for_any_anchor() is None:
            consecutive_failures += 1
            save_debug_screenshot(tag='yuhun_unknown_state')
            tower_recovery_click()
            time.sleep(random.uniform(1.5, 3.0))


def ensure_template_directory():
    """确保模板目录存在并包含必要的图片"""
    if not os.path.exists(TEMPLATES_DIR):
        os.makedirs(TEMPLATES_DIR)
        print(f" 创建了模板目录: {TEMPLATES_DIR}")
 
        # 给用户提供指引
        print("请将以下图片放入模板目录：")
        print(f"  - 爬塔挑战按钮图片: {TOWER_CHALLENGE_BUTTON}")
        print(f"  - 获得奖励标题锚点: {TOWER_REWARD_TITLE}")
        print(f"  - 弹窗关闭红叉: {POPUP_CLOSE}")
        print("（可运行 python yys/make_tower_templates.py 从参考截图生成）")
        print("然后重新启动程序")
        return False
 
    # 检查必要模板文件
    required_templates = [
        TOWER_CHALLENGE_BUTTON,
        TOWER_REWARD_TITLE,
        POPUP_CLOSE
    ]
 
    missing_files = []
    for template in required_templates:
        template_path = os.path.join(TEMPLATES_DIR, template)
        if not os.path.exists(template_path):
            missing_files.append(template)
 
    if missing_files:
        print("缺少必要的模板图片:")
        for file in missing_files:
            print(f"  - {file}")
        print(f"请将以上图片放入 {TEMPLATES_DIR} 目录，然后重新启动程序")
        return False
 
    return True
 
 
def get_challenge_count():
    """获取用户设置的挑战次数（--count 预设时跳过询问）"""
    global total_challenges

    if CLI_COUNT is not None:
        total_challenges = CLI_COUNT
        print(f"挑战次数设置为: {CLI_COUNT}次")
        return

    print("\n=== 挑战次数设置 ===")
    print("请输入要挑战的次数（0表示无限循环）：")
 
    while True:
        try:
            input_str = input("挑战次数: ").strip()
 
            # 如果用户直接按回车，默认无限循环
            if input_str == "":
                total_challenges = 0
                print("设置为无限循环模式")
                return
 
            # 尝试转换为整数
            count = int(input_str)
 
            if count < 0:
                print("请输入非负整数")
                continue
 
            total_challenges = count
            print(f"挑战次数设置为: {count}次")
            return
 
        except ValueError:
            print("输入无效，请输入一个整数")
 
 
def main_loop():
    """爬塔模式主循环（按画面状态同步，不依赖固定时序）：

    每轮先识别当前画面属于哪种状态，再做对应处理：
      1. "获得奖励"结算页 → 连续点击跳过，直到消失；
      2. 挑战按钮 → 双击进入战斗，安静等待结算页出现（战斗打多久都等）；
      3. 两者都没有（战斗进行中/转场动画/未知界面）→ 安静等待锚点出现，
         长时间无锚点才计失败并保存截图。
    """
    global challenge_count, running, total_challenges

    # 检查并确保模板目录存在且有必要的图片
    if not ensure_template_directory():
        return

    # 获取用户设置的挑战次数
    get_challenge_count()

    print("\n=== 爬塔模式自动挑战助手启动 ===")
    print(f"模拟器区域: {SCREEN_REGION}")
    print(f"初始等待时间: {INITIAL_WAIT_TIME}秒")
    print(f"挑战次数: {'无限循环' if total_challenges == 0 else total_challenges}次")
    print(f"挑战按钮偏移范围: X{CHALLENGE_BUTTON_OFFSET_X} Y{CHALLENGE_BUTTON_OFFSET_Y}")
    print(f"提示：{INITIAL_WAIT_TIME}秒后开始后台点击模拟器（不占用你的鼠标），请停在挑战页面...")
    print(f"失败时将点击位置: ({TOWER_FIXED_CLICK_POS[0]}, {TOWER_FIXED_CLICK_POS[1]}) ± {FIXED_CLICK_OFFSET}")
    print(f"预计战斗时长: {TOWER_BATTLE_DURATION}秒（先安静等待再检测结算页）")
    print(f"战斗结算最长等待: {TOWER_BATTLE_TIMEOUT}秒; 连续失败{TOWER_MAX_CONSECUTIVE_FAILURES}次后停止（截图存于 {DEBUG_SHOT_DIR}/）")
 
    # 倒计时显示
    for i in range(INITIAL_WAIT_TIME, 0, -1):
        print(f"开始倒计时: {i}秒...")
        time.sleep(1)
 
    # 主循环
    consecutive_failures = 0
    while running:
        # 检查是否达到设定的挑战次数
        if total_challenges > 0 and challenge_count >= total_challenges:
            print(f"\n 已完成设定的 {total_challenges} 次爬塔挑战，脚本将停止")
            running = False
            break
        if consecutive_failures >= TOWER_MAX_CONSECUTIVE_FAILURES:
            print(f"\n 连续 {consecutive_failures} 次未能完成爬塔挑战，脚本停止，"
                  f"请查看 {DEBUG_SHOT_DIR}/ 下的截图排查画面状态")
            running = False
            break

        # 每轮重新检测模拟器窗口（处理窗口被移动/最小化）
        if not msg_refresh_window():
            print("⚠️ 未检测到可用的模拟器窗口（可能被最小化），5秒后重试...")
            time.sleep(5)
            continue

        # 0. 弹窗（悬赏封印等）会挡住按钮导致点击无效，先关闭再进入正常流程
        if close_popup_if_open():
            continue

        # 1. 优先检查是否停在"获得奖励"结算页（上一轮跳结算不彻底/启动时残留）
        reward_pos, reward_conf = tower_find_reward_title()
        if reward_pos:
            print(f"🔍 检测到获得奖励结算页 @ {reward_pos} (置信度: {reward_conf:.4f})，点击跳过...")
            if not tower_dismiss_reward_screens():
                consecutive_failures += 1
                print(f" 结算页未能跳过，连续异常次数: {consecutive_failures}/{TOWER_MAX_CONSECUTIVE_FAILURES}")
                save_debug_screenshot(tag='dismiss_fail')
            continue

        # 2. 查找挑战按钮
        position, confidence = tower_find_challenge_button()

        if position and running:
            consecutive_failures = 0
            challenge_count += 1

            # 显示挑战进度
            progress = ""
            if total_challenges > 0:
                progress = f" ({challenge_count}/{total_challenges})"

            print(f"\n=== 第 {challenge_count}{progress} 次爬塔挑战开始 ===")
            print(f" 发现挑战按钮 @ {position} (置信度: {confidence:.4f})")

            # 应用挑战按钮随机偏移
            offset_x = random.randint(*CHALLENGE_BUTTON_OFFSET_X)
            offset_y = random.randint(*CHALLENGE_BUTTON_OFFSET_Y)
            click_x, click_y = clamp_to_window(position[0] + offset_x, position[1] + offset_y)

            # 3. 点击挑战按钮（双击确保生效）
            print(f" 添加偏移: X{offset_x} Y{offset_y}，实际点击位置: ({click_x}, {click_y})")
            human_like_click(click_x, click_y)
            time.sleep(0.5)
            human_like_click(click_x, click_y)

            # 短暂等待进入战斗
            challenge_start_time = time.time()
            while time.time() - challenge_start_time < 1.2 and running:
                remaining = 1.2 - (time.time() - challenge_start_time)
                time.sleep(min(remaining, random.uniform(0.1, 0.5)))

            if not running:
                break

            # 4. 战斗进行中：安静等待"获得奖励"结算页出现，战斗打多久都等，超时才计失败
            if not tower_wait_for_reward_title():
                consecutive_failures += 1
                print(f" 连续异常次数: {consecutive_failures}/{TOWER_MAX_CONSECUTIVE_FAILURES}")
                save_debug_screenshot(tag='battle_timeout')
                continue

            # 5. 跳过结算页，回到挑战页
            if not tower_dismiss_reward_screens():
                consecutive_failures += 1
                print(f" 结算页未能跳过，连续异常次数: {consecutive_failures}/{TOWER_MAX_CONSECUTIVE_FAILURES}")
                save_debug_screenshot(tag='dismiss_fail')
                continue

            # 6. 挑战完成冷却
            post_wait = random.uniform(*POST_CHALLENGE_WAIT)
            print(f" 第 {challenge_count}{progress} 次挑战完成，冷却等待 {post_wait:.1f}秒...")
            post_start = time.time()
            while time.time() - post_start < post_wait and running:
                time.sleep(0.1)
            continue

        # 7. 既无结算页也无挑战按钮：战斗进行中（如启动时恰好在打）或转场动画/未知界面
        #    安静等待锚点出现即可，超时才计失败——不再在战斗中盲目找按钮、点按钮
        if not running:
            break
        if tower_wait_for_any_anchor() is None:
            consecutive_failures += 1
            wait_time = random.uniform(0.5, 2.0)
            print(f" 长时间未识别到任何已知画面，等待 {wait_time:.1f}秒后重试...")
            print(f"连续失败次数: {consecutive_failures}/{TOWER_MAX_CONSECUTIVE_FAILURES}")
            if consecutive_failures >= 2:
                save_debug_screenshot(tag='unknown_state')
            print(f" 尝试在固定位置点击: ({TOWER_FIXED_CLICK_POS[0]}, {TOWER_FIXED_CLICK_POS[1]})")
            tower_recovery_click()
            wait_time += 1.0
            wait_start = time.time()
            while time.time() - wait_start < wait_time and running:
                time.sleep(0.1)
 
 
def print_statistics():
    """打印挑战统计信息"""
    global challenge_count, script_start_time, total_challenges
 
    # 计算脚本运行时间
    end_time = time.time()
    total_time = end_time - script_start_time
    hours = int(total_time // 3600)
    minutes = int((total_time % 3600) // 60)
    seconds = int(total_time % 60)
 
    # 显示挑战统计信息
    print("\n=== 爬塔挑战统计 ===")
    print(f" 总挑战次数: {challenge_count}")
    print(f" 总运行时间: {hours}小时 {minutes}分钟 {seconds}秒")
 
    # 显示目标挑战次数（如果设置了）
    if total_challenges > 0:
        print(f" 目标挑战次数: {total_challenges}次")
        if challenge_count >= total_challenges:
            print(" 成功完成所有挑战！")
        else:
            print(f" 只完成了 {challenge_count}/{total_challenges} 次挑战")
 
    if challenge_count > 0:
        avg_time = total_time / challenge_count
        avg_min = int(avg_time // 60)
        avg_sec = int(avg_time % 60)
        print(f" 平均每次挑战耗时: {avg_min}分 {avg_sec}秒")
 
        # 计算效率
        challenges_per_hour = challenge_count / (total_time / 3600)
        print(f"⚡ 挑战效率: {challenges_per_hour:.1f} 次/小时")
 
 
# === 结界突破模式功能函数 ===
def ensure_tpjj_templates():
    """检查结界突破模式所需的模板图片"""
    if not os.path.exists(TEMPLATES_DIR):
        print(f" 未找到模板目录: {TEMPLATES_DIR}")
        return False

    required_templates = [
        TPJJ_ANCHOR,
        TPJJ_ATTACK_BUTTON,
        TPJJ_STAMP,
        TPJJ_FLAG_MARK,
        TPJJ_WIN_BANNER,
        TPJJ_LOSE_BANNER,
        POPUP_CLOSE
    ]

    missing_files = [f for f in required_templates
                     if not os.path.exists(os.path.join(TEMPLATES_DIR, f))]
    if missing_files:
        print("缺少必要的模板图片:")
        for file in missing_files:
            print(f"  - {file}")
        print(f"可运行 yys/make_tpjj_templates.py 从参考截图重新生成")
        return False

    return True


def tpjj_update_content_offset(anchor_center):
    """用列表页标题锚点的实测位置校准游戏内容相对窗口左上角的偏移

    窗口自带工具栏/边框时窗口尺寸≠内容尺寸，而内容与参考图是1:1平移关系，
    按窗口比例换算会整体偏移；以每次回到列表页都会识别的锚点为基准最可靠。
    """
    global TPJJ_CONTENT_OFFSET
    offset = (int(round(anchor_center[0] - SCREEN_REGION[0] - TPJJ_ANCHOR_REF_CENTER[0])),
              int(round(anchor_center[1] - SCREEN_REGION[1] - TPJJ_ANCHOR_REF_CENTER[1])))
    if TPJJ_CONTENT_OFFSET != offset:
        print(f" 游戏内容偏移校准: {offset}")
    TPJJ_CONTENT_OFFSET = offset


def tpjj_ref_point(ref_x, ref_y):
    """参考图坐标 -> 归一化渲染坐标（尚未校准时按 1280x720 画布换算）。"""
    left, top, width, height = SCREEN_REGION
    if TPJJ_CONTENT_OFFSET is None:
        return (int(left + ref_x / TPJJ_REF_SIZE[0] * width),
                int(top + ref_y / TPJJ_REF_SIZE[1] * height))
    return (int(left + TPJJ_CONTENT_OFFSET[0] + ref_x),
            int(top + TPJJ_CONTENT_OFFSET[1] + ref_y))


def tpjj_get_card_centers():
    """按参考图像素坐标+内容偏移计算8个结界卡片的屏幕中心坐标"""
    return [tpjj_ref_point(x, y) for x, y in TPJJ_CARD_REFS]


def tpjj_random_click(x, y, offset=None):
    """常规点击：在目标点附近添加随机偏移，模拟手动点击的真实感"""
    if offset is None:
        offset = TPJJ_TAP_OFFSET
    click_x, click_y = clamp_to_window(
        int(x) + random.randint(-offset, offset),
        int(y) + random.randint(-offset, offset))
    print(f" 随机偏移点击 ({int(x)},{int(y)}) -> ({click_x}, {click_y})")
    human_like_click(click_x, click_y)


def tpjj_get_stamp_region(center):
    """结界卡片右上角"破"印（已击破标记）的检测区域（绝对坐标，偏移为参考图像素）"""
    cx = center[0] + TPJJ_STAMP_REF_OFFSET[0]
    cy = center[1] + TPJJ_STAMP_REF_OFFSET[1]
    return (int(cx - 60), int(cy - 55), 120, 110)


def tpjj_get_flag_region(center):
    """结界卡片右上角金色箭旗（打不过标记）的检测区域（绝对坐标，偏移为参考图像素）"""
    cx = center[0] + TPJJ_FLAG_REF_OFFSET[0]
    cy = center[1] + TPJJ_FLAG_REF_OFFSET[1]
    return (int(cx - 58), int(cy - 45), 116, 90)


def tpjj_card_mark_kind(center):
    """识别结界右上角的标记类型：'击破'(破印) / '打不过'(金色箭旗) / None(未标记)"""
    _, conf = find_template_in_region(
        TPJJ_STAMP, tpjj_get_stamp_region(center), threshold=TPJJ_MARK_THRESHOLD)
    if conf > TPJJ_MARK_THRESHOLD:
        return '击破'
    _, conf = find_template_in_region(
        TPJJ_FLAG_MARK, tpjj_get_flag_region(center), threshold=TPJJ_MARK_THRESHOLD)
    if conf > TPJJ_MARK_THRESHOLD:
        return '打不过'
    return None


def tpjj_find_next_target():
    """返回第一个没有标记（未击破且非打不过）的结界序号及已标记数量

    全部有标记时返回 (None, 8)。
    """
    marked = 0
    target = None
    for idx, center in enumerate(tpjj_get_card_centers(), start=1):
        if tpjj_card_mark_kind(center):
            marked += 1
        elif target is None:
            target = idx
    return target, marked


def tpjj_find_stable(template_filename, threshold, streak_needed, poll=0.3):
    """连续多帧识别到模板才认定有效，返回最后一次位置，否则返回None（防瞬时误报）"""
    pos = None
    for _ in range(streak_needed):
        pos, _ = find_template(template_filename, threshold=threshold)
        if pos is None:
            return None
        time.sleep(poll)
    return pos


def tpjj_wait_for_list_page(timeout):
    """等待回到结界突破列表页（标题锚点连续出现），超时返回None"""
    print("🔍 等待回到结界突破列表页...")
    start_time = time.time()
    while running and time.time() - start_time < timeout:
        position = tpjj_find_stable(TPJJ_ANCHOR, TPJJ_THRESHOLD, 2)
        if position:
            tpjj_update_content_offset(position)
            return position
        # 弹窗会挡住列表页锚点，先关掉
        close_popup_if_open()
        time.sleep(0.4)
    print("⏰ 等待列表页超时")
    return None


def tpjj_close_popup_if_open():
    """若上一局残留的进攻弹窗还开着，点击弹窗左侧空白处尝试关闭"""
    for _ in range(3):
        if not running:
            return
        position, _ = find_template(TPJJ_ATTACK_BUTTON, threshold=TPJJ_THRESHOLD)
        if not position:
            return
        left, top, width, height = SCREEN_REGION
        print(" 检测到残留的进攻弹窗，点击空白处关闭...")
        tpjj_random_click(left + width * 0.13, top + height * 0.5)
        time.sleep(1.2)


def tpjj_attack_target(index, center):
    """点击指定结界卡片，等待弹窗出现后点击进攻"""
    print(f" 点击第 {index} 个结界")
    tpjj_random_click(center[0], center[1], offset=40)

    # 等待进攻弹窗出现
    start_time = time.time()
    attack_pos = None
    while running and time.time() - start_time < 10:
        attack_pos, _ = find_template(TPJJ_ATTACK_BUTTON, threshold=TPJJ_THRESHOLD)
        if attack_pos:
            break
        time.sleep(0.3)

    if not attack_pos:
        print("⏰ 未检测到进攻弹窗，跳过本轮")
        return False

    # 点击进攻按钮（带随机偏移，避开按钮边缘）
    print(" 点击进攻按钮")
    time.sleep(random.uniform(0.3, 0.6))
    tpjj_random_click(attack_pos[0], attack_pos[1], offset=25)
    time.sleep(random.uniform(1.5, 2.5))  # 等待进入战斗
    return True


def tpjj_find_banner(template_filename, threshold=TPJJ_BANNER_THRESHOLD):
    """在 1280x720 归一化画布中查找胜负横幅，返回 (位置, 置信度)。"""
    template_path = os.path.join(TEMPLATES_DIR, template_filename)
    if not os.path.exists(template_path):
        print(f" 模板图片未找到: {template_path}")
        return None, 0

    template = cv2.imread(template_path, cv2.IMREAD_UNCHANGED)
    if template is None:
        print(f" 无法读取模板图片: {template_path}")
        return None, 0

    screen_cv = take_safe_screenshot(SCREEN_REGION)
    best_pos, best_conf = None, 0
    for scale in TPJJ_BANNER_SCALES:
        scaled = resize_template_to_normalized(template, scale=scale)
        h, w = scaled.shape[:2]
        if h > screen_cv.shape[0] or w > screen_cv.shape[1]:
            continue
        if scaled.shape[2] == 4:
            result = cv2.matchTemplate(screen_cv, scaled[:, :, :3], cv2.TM_CCORR_NORMED,
                                       mask=scaled[:, :, 3])
        else:
            result = cv2.matchTemplate(screen_cv, scaled, cv2.TM_CCOEFF_NORMED)
        result[~np.isfinite(result)] = 0  # 屏幕纯色区域会得到 inf/nan，置零防止误报
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)
        if max_val > best_conf:
            best_conf = max_val
            best_pos = (SCREEN_REGION[0] + max_loc[0] + w // 2,
                        SCREEN_REGION[1] + max_loc[1] + h // 2)

    if best_conf > threshold:
        return best_pos, best_conf
    return None, best_conf


def tpjj_wait_for_battle_end(timeout):
    """等待战斗结束（胜利/失败横幅），返回 ('win'|'lose', 横幅位置) 或 (None, None)

    双通道判定：
    1. 单帧 conf >= TPJJ_BANNER_INSTANT_CONF 直接判定（胜负文字只停留约2秒，必须快）；
    2. 兜底滑动窗口：近 TPJJ_BANNER_WINDOW 次轮询命中 TPJJ_BANNER_DEBOUNCE 次，
       应对结算背景动画导致的分数波动；一闪而过的低分误报依然被挡住。
    """
    print(f"⚔️ 战斗中，最长等待 {timeout}秒...")
    start_time = time.time()
    win_hits = deque(maxlen=TPJJ_BANNER_WINDOW)
    lose_hits = deque(maxlen=TPJJ_BANNER_WINDOW)
    win_pos = lose_pos = None
    while running and time.time() - start_time < timeout:
        win_pos, win_conf = tpjj_find_banner(TPJJ_WIN_BANNER)
        lose_pos, lose_conf = tpjj_find_banner(TPJJ_LOSE_BANNER)

        # 通道1：单帧高分立即判定
        if win_pos and win_conf >= TPJJ_BANNER_INSTANT_CONF:
            return 'win', win_pos
        if lose_pos and lose_conf >= TPJJ_BANNER_INSTANT_CONF:
            return 'lose', lose_pos

        # 通道2：滑动窗口
        win_hits.append(1 if win_pos else 0)
        lose_hits.append(1 if lose_pos else 0)
        if (win_pos or lose_pos) and sum(win_hits) + sum(lose_hits) == 1:
            print(f" 检测到疑似战斗结果 (胜利 conf={win_conf:.3f} / "
                  f"失败 conf={lose_conf:.3f})，等待达到{TPJJ_BANNER_INSTANT_CONF}"
                  f"或近{TPJJ_BANNER_WINDOW}帧命中{TPJJ_BANNER_DEBOUNCE}次")

        if sum(win_hits) >= TPJJ_BANNER_DEBOUNCE:
            return 'win', win_pos
        if sum(lose_hits) >= TPJJ_BANNER_DEBOUNCE:
            return 'lose', lose_pos
        # 横幅只停留约2秒，检测完横幅才处理弹窗，避免抢占胜负判定
        close_popup_if_open()
        time.sleep(0.25)
    return None, None


def tpjj_return_to_list(last_click_pos, fast=False):
    """连续点击跳过战斗结算界面，直到回到列表页

    fast=True（胜利后）使用快速双击节奏：胜利界面不会出现列表页标题，
    锚点单次检测即可；双击间隔与轮次间隔都缩短，尽快回到列表。
    """
    left, top, width, height = SCREEN_REGION
    fallback_pos = (left + width // 2, top + height // 2)
    for _ in range(30 if fast else 25):
        if fast:
            anchor_pos, _ = find_template(TPJJ_ANCHOR, threshold=TPJJ_THRESHOLD)
        else:
            anchor_pos = tpjj_find_stable(TPJJ_ANCHOR, TPJJ_THRESHOLD, 2)
        if anchor_pos:
            tpjj_update_content_offset(anchor_pos)
            return True

        # 点击前先关弹窗，避免点击落到弹窗的"接受"等按钮上
        close_popup_if_open()

        click_pos = last_click_pos or fallback_pos
        tpjj_random_click(click_pos[0], click_pos[1], offset=40)
        if fast:
            # 快速双击：第二下紧跟着点在附近，跳过结算更快
            time.sleep(random.uniform(0.12, 0.25))
            tpjj_random_click(click_pos[0], click_pos[1], offset=25)
            time.sleep(random.uniform(1.7, 2.2))
        else:
            time.sleep(1.3)
        last_click_pos = None  # 后续点击屏幕中心
    return False


def tpjj_confirm_mark(index, timeout=3.0):
    """战斗结束后等待该结界卡片右上角出现标记，返回'击破'/'打不过'或None

    刚回到列表页时卡片可能还在播入场动画，标记会比标题锚点晚就绪，
    因此识别不到时轮询至多timeout秒；仍没有则打印两类标记的最高置信度
    并保存调试截图，便于判断是阈值、偏移区域还是模板本身需要重新校准。
    按游戏规则战斗无论胜负必留破/旗其一，若确实没有标记，视为该结界
    未被消耗，下一轮会继续进攻它。
    """
    center = tpjj_get_card_centers()[index - 1]
    deadline = time.time() + timeout
    while running and time.time() < deadline:
        kind = tpjj_card_mark_kind(center)
        if kind:
            return kind
        time.sleep(0.4)
    stamp_conf = find_template_in_region(
        TPJJ_STAMP, tpjj_get_stamp_region(center), threshold=0.0, return_best=True)[1]
    flag_conf = find_template_in_region(
        TPJJ_FLAG_MARK, tpjj_get_flag_region(center), threshold=0.0, return_best=True)[1]
    print(f"⚠️ 第 {index} 个结界战斗结束后卡片上未见标记 (破印最高置信度: {stamp_conf:.3f}, "
          f"箭旗最高置信度: {flag_conf:.3f})，下一轮将继续进攻该结界")
    # save_debug_screenshot(tag=f'tpjj_mark_missing_{index}')
    return None


def tpjj_main_loop():
    """结界突破主循环：进攻结界 -> 等待战斗结束 -> 回到列表页 -> 识别标记选下一个结界

    目标选择规则（卡片标记驱动）：
      每轮在列表页上识别各结界右上角的标记（"破"印=已击破 / 金色箭旗=打不过，
      战斗无论胜负必留其一），进攻编号最小的无标记结界；
      战斗结束却未见标记时，视为该结界未被消耗，下一轮继续进攻它；
      8个结界全部处理完毕时，突破任务完成，停止脚本。
    """
    card_centers = []
    on_list = False  # 上一轮刚确认过列表页时为True，跳过重复检测
    consecutive_list_timeout = 0
    consecutive_attack_fail = 0

    print(f" 提示：优先进攻编号最小的无标记结界；{TPJJ_CARD_COUNT}个全部处理完毕后停止")

    while running:
        # 检查是否达到设定的战斗次数
        if total_challenges > 0 and TPJJ_STATS['battle'] >= total_challenges:
            print(f"\n 已完成设定的 {total_challenges} 次结界突破战斗，脚本将停止")
            break

        # 0. 每轮重新检测模拟器窗口（处理窗口被移动/最小化）
        if not msg_refresh_window():
            print("⚠️ 未检测到可用的模拟器窗口（可能被最小化），5秒后重试...")
            time.sleep(5)
            continue
        # 悬赏封印等弹窗会挡住列表页和按钮，先关闭
        close_popup_if_open()

        # 1. 等待回到列表页（上一轮返回列表时刚确认过锚点，跳过重复检测以加快节奏）
        if on_list:
            on_list = False
        else:
            tpjj_close_popup_if_open()
            if not tpjj_wait_for_list_page(TPJJ_LIST_TIMEOUT):
                consecutive_list_timeout += 1
                if consecutive_list_timeout >= 3:
                    print("❌ 多次等待列表页超时，请检查游戏画面后重新运行")
                    break
                continue
            consecutive_list_timeout = 0

        # 列表页就绪、内容偏移已按标题锚点校准后，再计算卡片/标记的屏幕坐标
        card_centers = tpjj_get_card_centers()

        # 2. 选择目标：第一个无标记（既未击破也非打不过）的结界；全部有标记 = 任务完成
        target, marked = tpjj_find_next_target()
        if target is None:
            print(f"🎉 {TPJJ_CARD_COUNT}个结界全部处理完毕（击破/打不过），突破任务完成！")
            break
        if marked:
            print(f" 当前进度：已标记 {marked}/{TPJJ_CARD_COUNT}，进攻第 {target} 个结界")

        attempt = TPJJ_STATS['battle'] + 1
        progress = f" ({attempt}/{total_challenges})" if total_challenges > 0 else ""
        print(f"\n=== 第 {attempt}{progress} 次结界突破：进攻第 {target} 个结界 ===")

        # 2. 点击结界并进攻
        if not tpjj_attack_target(target, card_centers[target - 1]):
            consecutive_attack_fail += 1
            if consecutive_attack_fail >= 5:
                print("❌ 连续多次无法进入战斗，脚本将停止")
                break
            on_list = False  # 状态不确定，下一轮重新检测列表页
            continue
        consecutive_attack_fail = 0

        # 3. 等待战斗结束（不论胜负）
        result, banner_pos = tpjj_wait_for_battle_end(TPJJ_BATTLE_TIMEOUT)
        if result is None:
            print("⏰ 等待战斗结果超时，请确认战斗为自动进行后重试")
            left, top, width, height = SCREEN_REGION
            tpjj_random_click(left + width // 2, top + height // 2)
            on_list = False  # 状态不确定，下一轮重新检测列表页
            continue

        TPJJ_STATS['battle'] += 1
        if result == 'win':
            TPJJ_STATS['win'] += 1
            print("🏆 战斗胜利")
        else:
            TPJJ_STATS['lose'] += 1
            print("💀 战斗失败")

        # 5. 点击回到列表页（胜利后奖励界面较多，用快速双击节奏跳过）
        if not tpjj_return_to_list(banner_pos, fast=(result == 'win')):
            print("⚠️ 未能确认回到列表页，将在下一轮重新检测")
            on_list = False
            continue

        # 6. 确认该结界卡片上的标记已出现（顺带等卡片动画结束）；
        #    未见标记时下一轮会重新进攻该结界（正常情况胜负必留破/旗其一）
        mark = tpjj_confirm_mark(target)
        if mark:
            print(f" 第 {target} 个结界标记确认: {mark}")

        # 战斗间隔随机冷却（缩短，尽快开始下一轮）
        time.sleep(random.uniform(0.2, 0.5))
        on_list = True  # 刚确认过列表页，下一轮直接进攻


def print_yuhun_statistics():
    """Print Yuhun battle statistics."""
    total_time = time.time() - script_start_time
    hours = int(total_time // 3600)
    minutes = int((total_time % 3600) // 60)
    seconds = int(total_time % 60)
    print("\n=== Yuhun statistics ===")
    print(f"Battles: {challenge_count}")
    print(f"Runtime: {hours}h {minutes}m {seconds}s")
    if challenge_count > 0:
        print(f"Average: {total_time / challenge_count:.1f}s per battle")


def print_tpjj_statistics():
    """打印结界突破统计信息"""
    total_time = time.time() - script_start_time
    hours = int(total_time // 3600)
    minutes = int((total_time % 3600) // 60)
    seconds = int(total_time % 60)

    print("\n=== 结界突破统计 ===")
    print(f" 总战斗次数: {TPJJ_STATS['battle']} "
          f"(胜利 {TPJJ_STATS['win']} / 失败 {TPJJ_STATS['lose']})")
    if TPJJ_STATS['battle'] > 0:
        print(f" 胜率: {TPJJ_STATS['win'] / TPJJ_STATS['battle'] * 100:.1f}%")
    print(f" 总运行时间: {hours}小时 {minutes}分钟 {seconds}秒")


def select_mode():
    """选择要运行的功能模式（--mode 预设时跳过询问）"""
    if CLI_MODE:
        return CLI_MODE
    print("Select 1=tower, 2=tpjj, 3=yuhun, 5=qiling, 0=exit")
    print("\n=== 阴阳师自动助手 ===")
    print(" 1. 爬塔模式自动挑战")
    print(" 2. 结界突破自动挑战")
    print(" 5. 契灵自动挑战")
    print(" 0. 退出")

    while True:
        choice = input("请选择功能 (1/2/0): ").strip()
        if choice == '1':
            return 'tower'
        if choice == '2':
            return 'tpjj'
        if choice == '3':
            return 'yuhun_captain'
        if choice == '4':
            return 'yuhun_member'
        if choice == '5':
            return 'qiling'
        if choice in ('0', 'q', 'Q', 'exit'):
            return None
        print("输入无效，请重新选择")


def run_yuhun_mode():
    """Yuhun mode entry point."""
    if not ensure_yuhun_templates():
        return

    print("\n=== Yuhun auto battle helper started ===")
    print(f"Simulator region: {SCREEN_REGION}")
    print(f"current position: {YUHUN_POSITION}")
    print(f"The script will start clicking in {INITIAL_WAIT_TIME}s; switch to the Yuhun list page.")

    # yuhun_main_loop owns the count prompt and countdown, matching tower mode behavior.
    yuhun_main_loop()


def run_qiling_mode():
    """契灵模式入口"""
    if not ensure_qiling_templates():
        return
    print("\n=== 契灵自动挑战助手启动 ===")
    print(f"模拟器区域: {SCREEN_REGION}")
    print(f"提示：{INITIAL_WAIT_TIME}秒后开始后台点击模拟器，请切换到契灵待挑战页面...")
    qiling_main_loop()


def run_tpjj_mode():
    """结界突破模式入口"""
    if not ensure_tpjj_templates():
        return

    get_challenge_count()

    print("\n=== 结界突破自动挑战助手启动 ===")
    print(f"模拟器区域: {SCREEN_REGION}")
    print(f"战斗次数: {'无限循环' if total_challenges == 0 else total_challenges}次")
    print(f"单场战斗最长等待: {TPJJ_BATTLE_TIMEOUT}秒")
    print(f"提示：{INITIAL_WAIT_TIME}秒后开始后台点击模拟器（不占用你的鼠标），请切换到结界突破界面...")

    for i in range(INITIAL_WAIT_TIME, 0, -1):
        print(f"开始倒计时: {i}秒...")
        time.sleep(1)

    tpjj_main_loop()


def find_all_device_windows():
    """找出所有模拟器设备窗口：带 nemuwin 渲染子窗口、且属于设备进程（排除模拟器主界面）"""
    cands = []

    def _cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title and msg_find_render_child(hwnd):
                cands.append((hwnd, title))
        return True

    win32gui.EnumWindows(_cb, None)
    device_marks = ('mumunxdevice', 'dnplayer', 'ldboxheadless', 'ld9boxheadless', 'ldplayer')
    result = []
    for hwnd, title in cands:
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        exe = (_query_process_image(pid) or '').lower()
        if any(mark in exe for mark in device_marks):
            result.append((hwnd, title))
    return result


def run_multi_launcher():
    """多开启动器：给每个模拟器窗口各启动一个独立脚本实例（独立控制台）

    每个子实例会通过 MuMuManager 把自己的窗口精确映射到对应实例的 adb 端口，
    因此这里只传 --title，不需要猜端口。模式和次数可统一或按窗口顺序逗号分隔。
    """
    print("=== 多开启动器 ===")
    wins = find_all_device_windows()
    if len(wins) < 2:
        titles = '、'.join(t for _, t in wins) or '无'
        print(f"当前只检测到 {len(wins)} 个模拟器窗口（{titles}），无需多开；"
              f"直接运行 python yys/auto_click.py 即可")
        return
    print(f"检测到 {len(wins)} 个模拟器窗口: " + '、'.join(f'「{t}」' for _, t in wins))

    if CLI_MODE:
        mode = {'tower': '1', 'tpjj': '2', 'yuhun': '3', 'qiling': '5'}[CLI_MODE]
    else:
        mode = input("每窗口运行模式，可统一 (1=爬塔 2=结界突破) 或按窗口顺序逗号分隔 (如 1,2): ").strip() or '1'
    if CLI_COUNT is not None:
        count = str(CLI_COUNT)
    else:
        count = input("挑战次数 (0=无限，可统一或按窗口顺序逗号分隔，直接回车=无限): ").strip() or '0'

    modes = [m.strip() for m in mode.split(',') if m.strip()] or ['1']
    counts = [c.strip() for c in count.split(',') if c.strip()] or ['0']
    script = os.path.abspath(__file__)
    for i, (hwnd, title) in enumerate(wins):
        m = modes[i % len(modes)]
        c = counts[i % len(counts)]
        cmd = [sys.executable, script,
               '--title', title, '--mode', m, '--count', c]
        subprocess.Popen(cmd, creationflags=subprocess.CREATE_NEW_CONSOLE)
        print(f" 已启动第 {i + 1} 个实例: 窗口「{title}」，模式 {m}，次数 {c}")
    print("\n提示：请保持各窗口为 1280x720 同比例渲染区，脚本会自动归一化识别并反向映射点击；"
          "窗口可遮挡但不要最小化。")
    print("每个实例有独立控制台，关闭控制台即停止对应实例。")


def parse_cli():
    """解析命令行：--selftest 自检；多开：--multi 启动器、
    --title <标题> 锁窗口、--port <端口>/--serial <串> 锁 adb、--mode/--count 免交互预设"""
    global WINDOWS_TITLE, EXPLICIT_TITLE, ADB_SERIAL, CLI_MODE, CLI_COUNT
    selftest = False
    multi = False
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--selftest':
            selftest = True
            i += 1
        elif a == '--multi':
            multi = True
            i += 1
        elif a == '--title' and i + 1 < len(args):
            WINDOWS_TITLE = args[i + 1]
            EXPLICIT_TITLE = True
            i += 2
        elif a == '--port' and i + 1 < len(args):
            ADB_SERIAL = f"127.0.0.1:{args[i + 1]}"
            i += 2
        elif a == '--serial' and i + 1 < len(args):
            ADB_SERIAL = args[i + 1]
            i += 2
        elif a == '--mode' and i + 1 < len(args):
            CLI_MODE = {'1': 'tower', '2': 'tpjj', '3': 'yuhun_captain', '4': 'yuhun_member',
                        '5': 'qiling', 'tower': 'tower', 'tpjj': 'tpjj',
                        'yuhun': 'yuhun', 'qiling': 'qiling'}.get(
                            args[i + 1].strip().lower())
            i += 2
        elif a == '--count' and i + 1 < len(args):
            try:
                CLI_COUNT = max(0, int(args[i + 1]))
            except ValueError:
                CLI_COUNT = None
            i += 2
        else:
            i += 1
    return selftest, multi



def prepare_backend():
    """GUI/CLI 共用的启动前检查。

    依次验证：pywin32 可用 -> 能刷新到模拟器窗口 -> adb/PostMessage 点击通道就绪。
    成功返回 None；失败返回用户可读的错误信息（此时不要进入主循环）。
    """
    if not HAS_WIN32:
        return "后台模式需要 pywin32：pip install pywin32"
    print("🛰️ 运行模式: 后台（PrintWindow + PostMessage），模拟器窗口可被遮挡，但不要最小化")
    if not msg_refresh_window():
        return "未找到模拟器窗口，请先启动 MuMu 和游戏"
    print(f"🖥️ 模拟器区域: {SCREEN_REGION}")
    if SCREEN_REGION[2] < 400 or SCREEN_REGION[3] < 300:
        print(" 警告：检测到的窗口尺寸过小，请确保模拟器已打开且正常显示")
    if MSG_CLICK_METHOD == 'adb' and not adb_setup():
        return "后台点击依赖 adb；也可把文件顶部 MSG_CLICK_METHOD 改为 'post' 尝试窗口消息点击"
    return None


def set_run_options(mode, count=0, wait_time=None,
                    battle_duration=None, battle_timeout=None):
    """供 GUI 设置本次运行参数（不影响命令行模式）。

    mode: 'tower' / 'tpjj' / 'yuhun_captain' / 'yuhun_member' / 'qiling'
    count: 挑战次数，0 表示无限循环
    wait_time: 启动前倒计时秒数；None 表示使用模块默认 INITIAL_WAIT_TIME
    battle_duration: 预计单场战斗时长（秒），使用单个数字，可带小数
    """
    global CLI_MODE, CLI_COUNT, total_challenges, current_mode, INITIAL_WAIT_TIME
    global TOWER_BATTLE_DURATION, TOWER_BATTLE_TIMEOUT
    global YUHUN_BATTLE_DURATION, YUHUN_BATTLE_TIMEOUT
    global TPJJ_BATTLE_DURATION, TPJJ_BATTLE_TIMEOUT
    global QILING_BATTLE_DURATION, QILING_BATTLE_TIMEOUT
    CLI_MODE = mode
    CLI_COUNT = max(0, int(count)) if count is not None else None
    total_challenges = CLI_COUNT if CLI_COUNT is not None else 0
    current_mode = mode
    if wait_time is not None:
        INITIAL_WAIT_TIME = max(0, int(wait_time))
    if battle_duration is not None:
        duration = float(battle_duration)
        if mode == 'tower':
            TOWER_BATTLE_DURATION = duration
        elif mode == 'tpjj':
            TPJJ_BATTLE_DURATION = duration
        elif mode in ('yuhun_captain', 'yuhun_member'):
            YUHUN_BATTLE_DURATION = duration
        elif mode == 'qiling':
            QILING_BATTLE_DURATION = duration
    if battle_timeout is not None:
        timeout = max(1.0, float(battle_timeout))
        if mode == 'tower':
            TOWER_BATTLE_TIMEOUT = timeout
        elif mode == 'tpjj':
            TPJJ_BATTLE_TIMEOUT = timeout
        elif mode in ('yuhun_captain', 'yuhun_member'):
            YUHUN_BATTLE_TIMEOUT = timeout
        elif mode == 'qiling':
            QILING_BATTLE_TIMEOUT = timeout


def request_stop():
    """让正在运行的自动化线程在下一个检查点退出（可被 GUI 的停止按钮调用）。"""
    global running
    running = False


def gui_run_once(mode):
    """GUI 模式：在后台线程中运行指定模式的完整流程（含倒计时与统计打印）。

    与 __main__ 的 CLI 流程共享同一套模式分发；模式无效或 prepare_backend 未
    通过时返回 False，可由 GUI 显示错误。
    """
    global running, current_mode, YUHUN_POSITION, challenge_count, script_start_time
    global TPJJ_STATS
    if mode not in ('tower', 'tpjj', 'yuhun_captain', 'yuhun_member', 'qiling'):
        return False
    # 重置本轮统计，保证 GUI 停止后再次启动时数据从零开始
    challenge_count = 0
    script_start_time = time.time()
    TPJJ_STATS = {'battle': 0, 'win': 0, 'lose': 0}
    running = True
    current_mode = mode
    try:
        if mode == 'tower':
            main_loop()
        elif mode == 'tpjj':
            run_tpjj_mode()
        elif mode == 'yuhun_captain':
            YUHUN_POSITION = 'captain'
            run_yuhun_mode()
        elif mode == 'yuhun_member':
            YUHUN_POSITION = 'member'
            run_yuhun_mode()
        elif mode == 'qiling':
            run_qiling_mode()
    finally:
        running = False
        if current_mode == 'tower':
            print_statistics()
        elif current_mode == 'tpjj':
            print_tpjj_statistics()
        elif current_mode in ('yuhun_captain', 'yuhun_member'):
            print_yuhun_statistics()
        print("=== 脚本执行结束 ===")
    return True


if __name__ == "__main__":
    SELFTEST_MODE, MULTI_MODE = parse_cli()
    if MULTI_MODE:
        if not HAS_WIN32:
            print(" 多开启动器依赖 pywin32：pip install pywin32")
            sys.exit(1)
        run_multi_launcher()
        sys.exit(0)
    err = prepare_backend()
    if err:
        print(err)
        sys.exit(1)

    if SELFTEST_MODE:
        run_backend_selftest()
        sys.exit(0)

    running = True

    try:
        current_mode = select_mode()
        if current_mode == 'tower':
            main_loop()
        elif current_mode == 'tpjj':
            run_tpjj_mode()
        elif current_mode == 'yuhun_captain':
            YUHUN_POSITION = 'captain'
            run_yuhun_mode()
        elif current_mode == 'yuhun_member':
            YUHUN_POSITION = 'member'
            run_yuhun_mode()
        elif current_mode == 'qiling':
            run_qiling_mode()
        else:
            print(" 已退出")
    except KeyboardInterrupt:
        print("\n 用户中断程序")
    except Exception as e:
        print(f"\n 发生错误: {str(e)}")
        import traceback

        traceback.print_exc()
    finally:
        running = False  # 确保循环停止
        if current_mode == 'tower':
            print_statistics()
        elif current_mode == 'tpjj':
            print_tpjj_statistics()
        elif current_mode == 'yuhun':
            print_yuhun_statistics()
        print("=== 脚本执行结束 ===")
        input("按回车键退出...")
