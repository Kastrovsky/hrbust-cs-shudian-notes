# -*- coding: utf-8 -*-
"""真题精解 82 张配图重裁：整行宽 + 段落吸附 + 剔页脚（台账 C-01）。

为什么重裁（前两版失败原因）：
  · 第一版 `提取/snap_crops.py` 把左边界写死（`box[0] = orig[0]`、`LIMIT_BY_SIDE["left"]=0`），
    左边界永远不补；其余三边只找"第一个 >=4px 空白带"，常落在行距内部 -> 切掉半个字；
  · 第二版按"整行宽 + 竖缝吸附"，空白判据被扫描噪点污染（THR=170 把噪点当年墨），
    密集版面找不到空白带，照样切。

这一版：先高斯模糊再二值化压噪点；横向取整页正文的真实墨迹范围（min/max，
不用众数/分位数，那两个都会被短行带偏）；纵向从原框向外放到最近段落空白带
（连续 >=4 行近乎无墨），并且永不越过页脚横线。

依赖（都在 提取/ 下，未入库）：
  提取/locate.json           每张图的页码与原始框（由 提取/locate_figs.py 生成）
  提取/pdf37_full/pNN.jpg    真题页渲染图
跑法：python scripts/recrop_zhenti_figs.py [--apply]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil

import numpy as np
from PIL import Image, ImageFilter, ImageOps

ROOT = r"D:\文件\大二\数电"
PAGES = os.path.join(ROOT, "提取", "pdf37_full")
IMGS = os.path.join(ROOT, "试卷", "真题精解", "images")
BAK = os.path.join(ROOT, "提取", "bak_images2")
LOCATE = os.path.join(ROOT, "提取", "locate.json")

THR = 170
NOISE = 3
MERGE = 9      # 行间隔 <= 该值视为同一块（行距 / 图内空隙）
GROW = 24      # 四边各外扩的像素数（一行字的行高约 20~24）
PAD = 10
TARGET_W = 1400


def load_page(pg: int):
    im = Image.open(os.path.join(PAGES, "p%02d.jpg" % pg)).convert("L")
    sm = im.filter(ImageFilter.GaussianBlur(1.2))     # 先模糊，压掉扫描噪点
    return im, (np.array(sm) < 150)


def body_bounds(m: np.ndarray):
    H, W = m.shape
    lefts, rights = [], []
    for y in range(0, H, 3):
        row = np.where(m[y, 60:])[0]
        if len(row) >= 20:
            lefts.append(int(row[0]) + 60)
            rights.append(int(row[-1]) + 60)
    bl = int(np.min(lefts)) if lefts else 60
    br = int(np.max(rights)) if rights else W - 40
    footer = H
    for y in range(H - 1, max(0, H - 260), -1):
        if int(m[y, bl:br].sum()) > 0.55 * (br - bl):
            footer = y
            break
    return bl, br, footer


def snap_row(m, y, x0, x1, up: bool, max_ext: int = 90) -> int:
    """把边界吸到最近的段落空白带（连续 >=4 行近乎无墨）。"""
    H = m.shape[0]
    step = -1 if up else 1
    run = 0
    for d in range(max_ext):
        p = y + step * d
        if p <= 0 or p >= H - 1:
            return y
        blank = int(m[p, x0:x1].sum()) < 5
        run = run + 1 if blank else 0
        if run >= 4:
            return p + (4 if not up else -4)
    return y


def blocks(m: np.ndarray, bl: int, br: int, top: int, bottom: int):
    """按行分段，每段内求墨迹包围盒：[(y0, y1, x0, x1, area), ...]。"""
    band = m[top:bottom, bl:br]
    rows = band.sum(axis=1)
    segs, cur, gap = [], None, 0
    for i, v in enumerate(rows):
        if v > NOISE:
            if cur is None:
                cur = [i, i]
            else:
                cur[1] = i
            gap = 0
        elif cur is not None:
            gap += 1
            if gap > MERGE:
                segs.append(tuple(cur))
                cur = None
    if cur is not None:
        segs.append(tuple(cur))

    out = []
    for a, b in segs:
        sub = band[a:b + 1]
        cols = np.where(sub.sum(axis=0) > 0)[0]
        if len(cols) == 0:
            continue
        out.append((a + top, b + top, int(cols[0]) + bl, int(cols[-1]) + bl,
                    int((b - a + 1) * (cols[-1] - cols[0] + 1))))
    return out


def pick(blks, seed):
    """挑与种子框中心重叠、且面积最大的块。"""
    x0, y0, x1, y1 = seed
    cy = (y0 + y1) // 2
    best, score = None, -1
    for b in blks:
        if min(b[1], y1) - max(b[0], y0) <= 0:
            continue
        s = b[4] * (3 if b[0] <= cy <= b[1] else 1)
        if s > score:
            best, score = b, s
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    data = json.load(open(LOCATE, encoding="utf-8"))
    os.makedirs(BAK, exist_ok=True)
    cache, report = {}, []

    for it in data:
        name, pg = it["file"], it["page"]
        if pg not in cache:
            im, m = load_page(pg)
            cache[pg] = (im, m, body_bounds(m))
        im, m, (bl, br, footer) = cache[pg]
        seed = list(it.get("box_new") or it["box"])
        # 只做"四边外扩"：不改原有取景，只把被切掉的半行补回来
        # 横向：一律取正文整宽，杜绝任何一行字被竖着切断
        x0, x1 = max(0, bl - 12), min(m.shape[1] - 1, br + 12)
        # 纵向：先按 +GROW 放，再吸到最近的段落空白带（找不到就保留 +GROW）
        y0 = max(0, snap_row(m, seed[1] - GROW, x0, x1, up=True))
        y1 = min(footer - 5, snap_row(m, seed[3] + GROW, x0, x1, up=False))
        box = [x0, y0, x1, y1]
        report.append("%-24s P%02d %s -> %s" % (name, pg, tuple(seed), tuple(box)))

        if args.apply:
            src = os.path.join(IMGS, name)
            if not os.path.exists(os.path.join(BAK, name)):
                shutil.copy2(src, os.path.join(BAK, name))
            crop = ImageOps.autocontrast(im.crop(tuple(box)), cutoff=1)
            w, h = crop.size
            k = max(1.0, (TARGET_W if w < TARGET_W else 2200) / float(w))
            if k > 1.0:
                crop = crop.resize((int(w * k), int(h * k)), Image.LANCZOS)
            crop.save(src)
            it["box_v3"] = box

    open(os.path.join(ROOT, "提取", "recrop_zj_report.txt"), "w", encoding="utf-8").write("\n".join(report) + "\n")
    print("\n".join(report))
    if args.apply:
        json.dump(data, open(LOCATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
