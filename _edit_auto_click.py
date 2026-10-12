# -*- coding: utf-8 -*-
import sys

PATH = r"D:\Shammpool\myCode\python_start\yys\auto_click.py"

raw = open(PATH, "rb").read()
text = raw.decode("utf-8")
nl = "\r\n" if "\r\n" in text else "\n"
lines = text.split(nl)
trailing_newline = text.endswith(nl)
if trailing_newline:
    lines = lines[:-1]
print("lines:", len(lines))

EDITS = [
    (7, 7, [], "import pyautogui"),
    (10, 10, [], "import pygetwindow as gw"),
    (17, 17, ["# 后台模式依赖 pywin32（pip install pywin32）"], "# 后台模式依赖 pywin32"),
    (51, 54,
     ["# 运行方式：后台模式（PrintWindow 截图 + adb/PostMessage 点击，模拟器窗口可被遮挡/",
      "# 切到后台，不占用真实鼠标键盘，但窗口不能最小化）",
      "# 命令行 --selftest 只截图自检不点击"],
     "# 运行后端："),
    (171, 235, ["# === 后台模式：PrintWindow 截图 + PostMessage 点击 ==="], "# 主要功能函数"),
    (624, 624, ["    hold_ms = random.randint(60, 160)  # 模拟按住时长"], "    hold_ms = random.randint(60, 160)"),
    (657, 659, [], "    if BACKEND != 'msg':"),
    (666, 666, ['        print("❌ 后台截图失败，请检查模拟器窗口是否正常（可尝试切换 MuMu 渲染模式）")'], "        print(\"❌ 后台截图失败"),
    (690, 702,
     ["# 屏幕区域由 msg_refresh_window() 在启动和每轮循环时刷新（后台模式）",
      "SCREEN_REGION = None"],
     "# 重新获取屏幕区域"),
    (704, 719,
     ["def human_like_click(x=None, y=None):",
      '    """模拟人类点击行为：向模拟器窗口注入后台点击，不占用真实鼠标"""',
      "    if x is not None and y is not None:",
      "        # 添加随机偏移",
      "        offset_x = random.randint(-10, 10)",
      "        offset_y = random.randint(-10, 10)",
      "        msg_click(x + offset_x, y + offset_y)"],
     "def human_like_click(x=None, y=None):"),
    (722, 737, [], "def validate_region(region):"),
    (738, 760,
     ["def take_safe_screenshot(region):",
      '    """安全地截取屏幕区域，防止无效参数',
      "",
      "    后台模式走 PrintWindow：窗口被遮挡也能截图；",
      "    截图失败返回全黑图（模板自然匹配不上，等下一轮窗口恢复）。",
      '    """',
      "    img = msg_screenshot_region(region)",
      "    if img is None:",
      "        return np.zeros((max(1, int(region[3])), max(1, int(region[2])), 3), dtype=np.uint8)",
      "    return img"],
     "def take_safe_screenshot(region):"),
    (1386, 1389,
     ['    print(f"提示：{INITIAL_WAIT_TIME}秒后开始后台点击模拟器（不占用你的鼠标），请停在挑战页面...")'],
     "    if BACKEND == 'msg':"),
    (1459, 1465,
     ["            # 短暂等待进入战斗",
      "            challenge_start_time = time.time()",
      "            while time.time() - challenge_start_time < 1.2 and running:",
      "                remaining = 1.2 - (time.time() - challenge_start_time)",
      "                time.sleep(min(remaining, random.uniform(0.1, 0.5)))"],
     "            # 短暂等待进入战斗"),
    (2023, 2026,
     ['    print(f"The script will start clicking in {INITIAL_WAIT_TIME}s; switch to the Yuhun list page.")'],
     "    if BACKEND == 'msg':"),
    (2043, 2046,
     ['    print(f"提示：{INITIAL_WAIT_TIME}秒后开始后台点击模拟器（不占用你的鼠标），请切换到结界突破界面...")'],
     "    if BACKEND == 'msg':"),
    (2107, 2108,
     ["        cmd = [sys.executable, script,",
      "               '--title', title, '--mode', m, '--count', c]"],
     "        cmd = [sys.executable, script, '--backend', 'msg',"),
    (2116, 2131,
     ['    """解析命令行：--selftest 自检；多开：--multi 启动器、',
      "    --title <标题> 锁窗口、--port <端口>/--serial <串> 锁 adb、--mode/--count 免交互预设\"\"\"",
      "    global WINDOWS_TITLE, EXPLICIT_TITLE, ADB_SERIAL, CLI_MODE, CLI_COUNT",
      "    selftest = False",
      "    multi = False",
      "    args = sys.argv[1:]",
      "    i = 0",
      "    while i < len(args):",
      "        a = args[i]",
      "        if a == '--selftest':"],
     '    """解析命令行：--backend msg|gui'),
    (2165, 2194,
     ["    if MULTI_MODE:",
      "        if not HAS_WIN32:",
      '            print(" 多开启动器依赖 pywin32：pip install pywin32")',
      "            sys.exit(1)",
      "        run_multi_launcher()",
      "        sys.exit(0)",
      "    if not HAS_WIN32:",
      '        print(" 后台模式需要 pywin32：pip install pywin32")',
      "        sys.exit(1)",
      '    print("🛰️ 运行模式: 后台（PrintWindow + PostMessage），模拟器窗口可被遮挡，但不要最小化")',
      "    if not msg_refresh_window():",
      '        print(" 未找到模拟器窗口，请先启动 MuMu 和游戏")',
      "        sys.exit(1)",
      '    print(f"🖥️ 模拟器区域: {SCREEN_REGION}")',
      "    if SCREEN_REGION[2] < 400 or SCREEN_REGION[3] < 300:",
      '        print(" 警告：检测到的窗口尺寸过小，请确保模拟器已打开且正常显示")',
      "    if MSG_CLICK_METHOD == 'adb' and not adb_setup():",
      "        print(\" 后台点击依赖 adb；也可把文件顶部 MSG_CLICK_METHOD 改为 'post' 尝试窗口消息点击\")",
      "        sys.exit(1)",
      "",
      "    if SELFTEST_MODE:",
      "        run_backend_selftest()",
      "        sys.exit(0)",
      "",
      "    running = True"],
     "    if MULTI_MODE:"),
    (2212, 2213, [], "    except pyautogui.FailSafeException:"),
]

for start, end, new, expect in sorted(EDITS, key=lambda e: e[0], reverse=True):
    actual = lines[start - 1]
    if expect not in actual:
        print("MISMATCH at", start, "expected:", expect, "actual:", repr(actual))
        sys.exit(1)
    lines[start - 1:end] = new

out = nl.join(lines) + (nl if trailing_newline else "")
# 全局替换：refresh_window_rect -> msg_refresh_window
before = out.count("refresh_window_rect")
out = out.replace("refresh_window_rect", "msg_refresh_window")
after = out.count("msg_refresh_window")
open(PATH, "wb").write(out.encode("utf-8"))
print("OK. refresh_window_rect occurrences:", before, "-> msg_refresh_window total:", after)
print("new line count:", len(lines))
