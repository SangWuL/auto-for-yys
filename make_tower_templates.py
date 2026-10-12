"""从挑战页/结算页参考截图裁剪爬塔模式的模板图

用法（在项目根目录运行）: python yys/make_tower_templates.py

需要参考截图（与模拟器画面同分辨率，当前为 2559x1449）:
  templates/tiaozhan.png    挑战页面整页截图
  templates/tz_success.png  "获得奖励"结算页整页截图

生成:
  templates/tz_button.png   挑战按钮文字区域（点击目标）
  templates/tz_reward.png   "获得奖励"标题锚点（结算页判定/战斗结束判定）
"""
import os
import sys
import cv2
import numpy as np


def resolve_templates_dir():
    """兼容从项目根目录或 yys/ 目录运行"""
    here = os.path.dirname(os.path.abspath(__file__))
    candidates = [
        os.path.join(here, '..', 'templates'),
        os.path.join(here, 'templates'),
        'templates',
    ]
    for path in candidates:
        if os.path.exists(os.path.join(path, 'tiaozhan.png')):
            return os.path.normpath(path)
    return os.path.normpath(candidates[0])


TEMPLATES_DIR = resolve_templates_dir()

# 裁剪区域（相对参考图 2559x1449 的比例）: (x0, y0, x1, y1)
# 挑战按钮：只取"挑战"文字及其周围少量按钮底色，避开呼吸灯/外圈光效
BUTTON_RATIO = (2235 / 2559, 1190 / 1449, 2465 / 2559, 1355 / 1449)
# "获得奖励"标题：避开顶部跑马灯公告和下方会弹跳的奖励图标
REWARD_RATIO = (950 / 2559, 380 / 1449, 1615 / 2559, 560 / 1449)

JOBS = [
    ('tiaozhan.png', BUTTON_RATIO, 'tz_button.png'),
    ('tz_success.png', REWARD_RATIO, 'tz_reward.png'),
]


def crop_and_save(ref_name, ratio, out_name):
    ref_path = os.path.join(TEMPLATES_DIR, ref_name)
    img = cv2.imread(ref_path, cv2.IMREAD_UNCHANGED)
    if img is None:
        print(f"❌ 无法读取参考截图: {ref_path}")
        return False

    h, w = img.shape[:2]
    x0, y0, x1, y1 = ratio
    crop = img[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
    out_path = os.path.join(TEMPLATES_DIR, out_name)
    cv2.imwrite(out_path, crop)
    print(f"✅ {out_name}: {crop.shape[1]}x{crop.shape[0]} <- {ref_name}")

    # 自检：裁剪图应能在参考图原位置以极高分数匹配
    ref_bgr = img[:, :, :3] if img.shape[2] == 4 else img
    crop_bgr = crop[:, :, :3] if crop.shape[2] == 4 else crop
    if crop_bgr.shape[0] < ref_bgr.shape[0] and crop_bgr.shape[1] < ref_bgr.shape[1]:
        result = cv2.matchTemplate(ref_bgr, crop_bgr, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        expect = (int(x0 * w), int(y0 * h))
        ok = max_val > 0.97 and abs(max_loc[0] - expect[0]) < 8 and abs(max_loc[1] - expect[1]) < 8
        flag = '✔' if ok else '⚠'
        print(f"   {flag} 自检 conf={max_val:.4f} @ {max_loc}（期望位置 {expect}）")
        if not ok:
            print("   ⚠ 裁剪区域可能偏离预期，请检查参考截图或调整比例")
    return True


def main():
    print(f"模板目录: {TEMPLATES_DIR}")
    ok = all(crop_and_save(*job) for job in JOBS)
    if not ok:
        sys.exit(1)
    print("完成")


if __name__ == '__main__':
    main()
