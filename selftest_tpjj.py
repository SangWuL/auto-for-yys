# -*- coding: utf-8 -*-
"""离线自测：用参考截图模拟屏幕画面，验证结界突破核心识别与目标选择逻辑"""
import os
import sys
import cv2
import numpy as np


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import auto_click as ac  # noqa: E402

TPL = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates'))
ac.SCREEN_REGION = (0, 0, *ac.NORMALIZED_RENDER_SIZE)
ac.TEMPLATES_DIR = TPL  # 自测时使用绝对路径（实际运行沿用 python_start/ 为工作目录）

# 模拟当前屏幕画面（take_safe_screenshot 按区域裁剪）
_current = {'img': None}


def _fake_screenshot(region):
    left, top, width, height = region
    return _current['img'][top:top + height, left:left + width]


ac.take_safe_screenshot = _fake_screenshot

# 屏蔽真实点击（记录调用即可）
_clicks = []
ac.human_like_click = lambda x=None, y=None: _clicks.append((x, y))


def set_screen(name):
    source = cv2.imread(os.path.join(TPL, name))
    _current['img'] = cv2.resize(source, ac.NORMALIZED_RENDER_SIZE, interpolation=cv2.INTER_AREA)


def check(desc, ok):
    print(('PASS' if ok else 'FAIL'), '-', desc)
    return ok


results = []

# 1. start页：标题锚点可见
set_screen('tpjj_start.png')
pos, conf = ac.find_template(ac.TPJJ_ANCHOR, threshold=ac.TPJJ_THRESHOLD)
results.append(check(f'start页识别到标题锚点 (conf={conf:.3f})', pos is not None))

# 2. click01页：进攻按钮可见；标题被遮挡，锚点不可见
set_screen('tpjj_click01.png')
pos, conf = ac.find_template(ac.TPJJ_ATTACK_BUTTON, threshold=ac.TPJJ_THRESHOLD)
results.append(check(f'click01页识别到进攻按钮 (conf={conf:.3f})', pos is not None))
pos, conf = ac.find_template(ac.TPJJ_ANCHOR, threshold=ac.TPJJ_THRESHOLD)
results.append(check(f'click01页不误判标题锚点 (conf={conf:.3f})', pos is None))

# 3. start页不误判进攻按钮
set_screen('tpjj_start.png')
pos, conf = ac.find_template(ac.TPJJ_ATTACK_BUTTON, threshold=ac.TPJJ_THRESHOLD)
results.append(check(f'start页不误判进攻按钮 (conf={conf:.3f})', pos is None))

# 4. 卡片中心坐标
centers = ac.tpjj_get_card_centers()
results.append(check(f'8个卡片中心: {centers[0]}...{centers[-1]}', len(centers) == 8))

# 5. 标记驱动的目标选择（破印=已击破 / 金旗=打不过）：第一个无标记结界；全有标记=完成
set_screen('tpjj_finish.png')
target, marked = ac.tpjj_find_next_target()
results.append(check(f'finish页 全部有标记 -> 完成 (target={target}, marked={marked})',
                     target is None and marked == 8))

set_screen('tpjj_fail_01.png')
target, marked = ac.tpjj_find_next_target()
results.append(check(f'fail01页 第1个打不过 -> 目标=2 (target={target}, marked={marked})',
                     target == 2 and marked == 1))

set_screen('tpjj_start.png')
target, marked = ac.tpjj_find_next_target()
results.append(check(f'start页 无标记 -> 目标=1 (target={target}, marked={marked})',
                     target == 1 and marked == 0))

# 标记置信度分离度：finish页8个破印全命中、start页两种标记均不误判
set_screen('tpjj_finish.png')
stamp_confs = [ac.find_template_in_region(ac.TPJJ_STAMP, ac.tpjj_get_stamp_region(c), threshold=0)[1]
               for c in ac.tpjj_get_card_centers()]
results.append(check(f'finish页 8个破印全命中 (min conf={min(stamp_confs):.3f})',
                     min(stamp_confs) >= ac.TPJJ_MARK_THRESHOLD))
set_screen('tpjj_start.png')
mark_confs = [max(ac.find_template_in_region(ac.TPJJ_STAMP, ac.tpjj_get_stamp_region(c), threshold=0)[1],
                  ac.find_template_in_region(ac.TPJJ_FLAG_MARK, ac.tpjj_get_flag_region(c), threshold=0)[1])
              for c in ac.tpjj_get_card_centers()]
results.append(check(f'start页 无误判标记 (max conf={max(mark_confs):.3f})',
                     max(mark_confs) < ac.TPJJ_MARK_THRESHOLD))
set_screen('tpjj_fail_01.png')
flag_confs = [ac.find_template_in_region(ac.TPJJ_FLAG_MARK, ac.tpjj_get_flag_region(c), threshold=0)[1]
              for c in ac.tpjj_get_card_centers()]
results.append(check(f'fail01页 金旗仅卡1命中 (card1={flag_confs[0]:.3f}, max其余={max(flag_confs[1:]):.3f})',
                     flag_confs[0] >= ac.TPJJ_MARK_THRESHOLD and max(flag_confs[1:]) < ac.TPJJ_MARK_THRESHOLD))

# 6. 随机偏移点击：偏移量不超过设定范围且在窗口内
_clicks.clear()
ac.tpjj_random_click(1000, 700, offset=40)
cx, cy = _clicks[-1]
results.append(check(f'随机偏移点击落点 ({cx},{cy}) 在 (960,660)-(1040,740) 内',
                     960 <= cx <= 1040 and 660 <= cy <= 740))

# 7. 胜负横幅匹配（合成 1280x720 归一化画面）
for scale in (1.0,):
    canvas = np.zeros((ac.NORMALIZED_RENDER_HEIGHT, ac.NORMALIZED_RENDER_WIDTH, 3), np.uint8)
    tpl = cv2.imread(os.path.join(TPL, ac.TPJJ_WIN_BANNER))
    tpl_s = ac.resize_template_to_normalized(tpl, scale=scale)
    canvas[100:100 + tpl_s.shape[0], 300:300 + tpl_s.shape[1]] = tpl_s
    _current['img'] = canvas
    pos, conf = ac.tpjj_find_banner(ac.TPJJ_WIN_BANNER)
    results.append(check(f'胜利横幅 scale={scale} 识别 (conf={conf:.3f})', pos is not None))

_current['img'] = np.zeros((ac.NORMALIZED_RENDER_HEIGHT, ac.NORMALIZED_RENDER_WIDTH, 3), np.uint8)
pos, conf = ac.tpjj_find_banner(ac.TPJJ_WIN_BANNER)
results.append(check(f'空画面不误判胜利横幅 (conf={conf:.3f})', pos is None))

# 8. 横幅双通道判定：单帧高分立即判定；低分靠滑动窗口；瞬时低分误报不判定
canvas_fail = np.zeros((ac.NORMALIZED_RENDER_HEIGHT, ac.NORMALIZED_RENDER_WIDTH, 3), np.uint8)
tpl_fail_source = cv2.imread(os.path.join(TPL, ac.TPJJ_LOSE_BANNER))
tpl_fail = ac.resize_template_to_normalized(tpl_fail_source)
canvas_fail[100:100 + tpl_fail.shape[0], 150:150 + tpl_fail.shape[1]] = tpl_fail

# 弱匹配画布：轻微缩放粘贴降低conf，专测滑动窗口通道；自动扫缩放比适配瞬时判定线的调整
canvas_weak = None
weak_conf = 0.0
for scale in (1.03, 1.05, 1.04, 1.07, 1.06, 1.10, 1.12):
    tpl_weak = cv2.resize(tpl_fail, None, fx=scale, fy=scale)
    canvas_weak = np.zeros((ac.NORMALIZED_RENDER_HEIGHT, ac.NORMALIZED_RENDER_WIDTH, 3), np.uint8)
    canvas_weak[100:100 + tpl_weak.shape[0], 150:150 + tpl_weak.shape[1]] = tpl_weak
    ac.take_safe_screenshot = lambda region: canvas_weak
    _, weak_conf = ac.tpjj_find_banner(ac.TPJJ_LOSE_BANNER)
    if 0.80 <= weak_conf < ac.TPJJ_BANNER_INSTANT_CONF:
        break
results.append(check(f'弱匹配画布 conf={weak_conf:.3f} 落在[0.80,{ac.TPJJ_BANNER_INSTANT_CONF})',
                     0.80 <= weak_conf < ac.TPJJ_BANNER_INSTANT_CONF))

# 8a. 瞬时低分误报：前2次轮询出现后消失 -> 不判定
_counter = {'n': 0}


def _transient_weak_fake(region):
    _counter['n'] += 1
    return canvas_weak if _counter['n'] <= 4 else np.zeros_like(canvas_weak)


ac.take_safe_screenshot = _transient_weak_fake
results.append(check('瞬时低分误报不误判战斗结束',
                     ac.tpjj_wait_for_battle_end(5) == (None, None)))

# 8b. 低分但持续出现 -> 滑动窗口判定
ac.take_safe_screenshot = lambda region: canvas_weak
res = ac.tpjj_wait_for_battle_end(15)
results.append(check(f'持续低分横幅经窗口判定为失败 (res={res[0]})', res[0] == 'lose'))

# 8c. 瞬时高分（真横幅形态）：仅第1次轮询出现 -> 立即判定
_counter['n'] = 0


def _instant_fake(region):
    _counter['n'] += 1
    return canvas_fail if _counter['n'] <= 2 else np.zeros_like(canvas_fail)


ac.take_safe_screenshot = _instant_fake
res = ac.tpjj_wait_for_battle_end(10)
results.append(check(f'瞬时高分横幅立即判定为失败 (res={res[0]})', res[0] == 'lose'))

# 8d. 稳定高分
ac.take_safe_screenshot = lambda region: canvas_fail
res = ac.tpjj_wait_for_battle_end(15)
results.append(check(f'稳定横幅判定为失败 (res={res[0]})', res[0] == 'lose'))

ac.take_safe_screenshot = _fake_screenshot  # 恢复

# 9. 内容偏移校准：模拟内容整体下移36px时卡片坐标与标记识别仍需准确
_toolbar = np.zeros((ac.NORMALIZED_RENDER_HEIGHT, ac.NORMALIZED_RENDER_WIDTH, 3), np.uint8)
_toolbar_source = cv2.resize(cv2.imread(os.path.join(TPL, 'tpjj_fail_01.png')),
                             ac.NORMALIZED_RENDER_SIZE, interpolation=cv2.INTER_AREA)
_toolbar[36:, :] = _toolbar_source[:-36, :]
ac.SCREEN_REGION = (0, 0, *ac.NORMALIZED_RENDER_SIZE)
ac.TPJJ_CONTENT_OFFSET = None
_current['img'] = _toolbar
_anchor_pos, anchor_conf = ac.find_template(ac.TPJJ_ANCHOR, threshold=ac.TPJJ_THRESHOLD)
_ok = _anchor_pos is not None
_flag_conf = 0.0
if _ok:
    ac.tpjj_update_content_offset(_anchor_pos)
    _cal_centers = ac.tpjj_get_card_centers()
    _flag_conf = ac.find_template_in_region(ac.TPJJ_FLAG_MARK,
                                            ac.tpjj_get_flag_region(_cal_centers[0]), threshold=0)[1]
results.append(check(f'带工具栏窗口校准偏移{ac.TPJJ_CONTENT_OFFSET}，卡1金旗命中 (conf={_flag_conf:.3f})',
                     _ok and ac.TPJJ_CONTENT_OFFSET == (0, 36)
                     and _flag_conf >= ac.TPJJ_MARK_THRESHOLD))

# 10. 归一化点击坐标反向映射：不同大小但同比例的渲染区应落到对应物理像素
ac.RENDER_REGION = (100, 200, 2560, 1440)
_mapping_ok = (ac.normalized_to_screen_point(0, 0) == (100, 200)
               and ac.normalized_to_screen_point(640, 360) == (1380, 920)
               and ac.normalized_to_screen_point(1280, 720) == (2660, 1640))
results.append(check('1280x720 坐标反向映射到同比例渲染区', _mapping_ok))

print('\n%d/%d 通过' % (sum(results), len(results)))
sys.exit(0 if all(results) else 1)
