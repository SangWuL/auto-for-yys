# -*- coding: utf-8 -*-
"""从结界突破参考截图中裁剪出小模板。

模板可继续从 2559x1449 参考图生成，运行时会自动缩放到 1280x720 归一化画布。

生成模板（基于 2559x1449 参考图）：
  tpjj_anchor.png  列表页标题「结界突破」  <- tpjj_start.png   (990,133)-(1563,243)
  tpjj_attack.png  弹窗「进攻」按钮        <- tpjj_click01.png (1185,705)-(1460,835)
  tpjj_win.png     战斗胜利文字            <- success.png      (1395,205)-(2010,585)
  tpjj_lose.png    战斗失败文字            <- fail.png         (1370,210)-(1990,575)
  tpjj_stamp.png   卡片右上角"破"已击破印记 <- tpjj_finish.png  (1315,285)-(1487,470)
  tpjj_flagmark.png 卡片右上角金色箭旗(打不过) <- tpjj_fail_01.png (1330,264)-(1512,410)
"""
import os
import cv2

BASE = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.normpath(os.path.join(BASE, '..', 'templates'))

CROPS = [
    ('tpjj_start.png',   'tpjj_anchor.png', (990, 133, 1563, 243)),
    ('tpjj_click01.png', 'tpjj_attack.png', (1185, 705, 1460, 835)),
    ('success.png',      'tpjj_win.png',    (1395, 205, 2010, 585)),
    ('fail.png',         'tpjj_lose.png',   (1370, 210, 1990, 575)),
    ('tpjj_finish.png',  'tpjj_stamp.png',  (1315, 285, 1487, 470)),
    ('tpjj_fail_01.png', 'tpjj_flagmark.png', (1330, 264, 1512, 410)),
]


def main():
    for src, dst, (x0, y0, x1, y1) in CROPS:
        img = cv2.imread(os.path.join(TPL_DIR, src))
        assert img is not None, f'无法读取参考图 {src}'
        crop = img[y0:y1, x0:x1]
        out = os.path.join(TPL_DIR, dst)
        cv2.imwrite(out, crop)
        print(f'{src} ({x0},{y0})-({x1},{y1}) -> {dst} {crop.shape[1]}x{crop.shape[0]}')


if __name__ == '__main__':
    main()
