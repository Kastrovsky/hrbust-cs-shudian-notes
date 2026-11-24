# -*- coding: utf-8 -*-
"""重裁 计算机专业真题/images 下 5 张原卷配图（2026-11-18，台账 C-01）。

背景：这批图初裁时四条边都压在内容上——底部 CP 时钟线、D0/D1/D2 引脚、+5V/接地、
      左侧 A/B/C 输入被切掉，有的还吞进题干文字（“（4）说明电路功能。”）。
本脚本从 `计算机专业真题/源卷/数电期末题.docx` 内嵌的整页扫描重新裁剪，只保留图形本身：
  1. 自动把 docx 的 word/media 解到 `提取/docx_media/`（已存在则跳过）；
  2. 按"源页 + 裁剪框 + 抹白矩形"裁图（坐标由墨迹包围盒实测，不靠目测预览）；
  3. 去灰底 → 自动对比 → LANCZOS 放大到 ≤1000 px 宽（台账 C-07）→ 轻度锐化。

源页对照：
  image1 = 2021–2022 A 卷 p1 -> fig01（填空 7 二极管-三极管门）
  image2 = 2021–2022 A 卷 p2 -> fig02（三、时序电路）、fig03（六、全加器）
  image3 = 2019–2020 A 卷 p1 -> fig04（二、TTL 与非门）
  image4 = 2019–2020 A 卷 p2 -> fig05（四、时序电路）

用法（仓库根目录）：python scripts/recrop_shijuan_figs.py
"""
from __future__ import annotations

import os
import zipfile

from PIL import Image, ImageDraw, ImageFilter, ImageOps

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCX = os.path.join(ROOT, "计算机专业真题", "源卷", "数电期末题.docx")
MEDIA = os.path.join(ROOT, "提取", "docx_media", "word", "media")
OUT = os.path.join(ROOT, "计算机专业真题", "images")

# 输出文件名: (源页, 裁剪框, 需抹白的矩形列表（相对裁剪框左上角）)
JOBS = {
    "fig01_2021_2022_diode_gate.png": ("image1", (640, 795, 995, 1100), [(0, 12, 175, 50)]),
    "fig02_2021_2022_seq_circuit.png": ("image2", (206, 256, 838, 594), [(0, 0, 196, 40)]),
    "fig03_2021_2022_fulladder_gate.png": ("image2", (243, 1032, 872, 1380), []),
    "fig04_2019_2020_ttl_nand.png": ("image3", (228, 1014, 772, 1392), []),
    "fig05_2019_2020_seq_circuit.png": ("image4", (217, 643, 1084, 969), []),
}
TARGET_W = 1000      # 文件宽度上限，见台账 C-07
MAX_SCALE = 2.6      # 源图 150 dpi，放大超过 2.6 倍只是糊，不再加分


def ensure_media() -> None:
    """把 docx 里的整页扫描解出来（只在缺文件时解压）。"""
    need = [f"{page}.png" for page, _, _ in JOBS.values()]
    if all(os.path.exists(os.path.join(MEDIA, n)) for n in need):
        return
    os.makedirs(MEDIA, exist_ok=True)
    with zipfile.ZipFile(DOCX) as zf:
        for name in zf.namelist():
            if name.startswith("word/media/") and name.lower().endswith(".png"):
                with open(os.path.join(MEDIA, os.path.basename(name)), "wb") as fh:
                    fh.write(zf.read(name))
    print(f"已从 {os.path.basename(DOCX)} 解出整页扫描到 提取/docx_media/")


def clean(img: Image.Image) -> Image.Image:
    img = img.point(lambda v: 255 if v > 232 else v)   # 浅灰扫描底拉成纯白
    return ImageOps.autocontrast(img, cutoff=1)


def main() -> None:
    ensure_media()
    for name, (page, box, patches) in JOBS.items():
        src = Image.open(os.path.join(MEDIA, f"{page}.png")).convert("L")
        crop = src.crop(box)
        if patches:
            draw = ImageDraw.Draw(crop)
            for patch in patches:
                draw.rectangle(list(patch), fill=255)
        crop = clean(crop)
        scale = min(MAX_SCALE, TARGET_W / crop.width)
        crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
        crop = crop.filter(ImageFilter.UnsharpMask(radius=1.5, percent=110, threshold=3))
        crop.save(os.path.join(OUT, name), optimize=True)
        print(f"{name} <- {page}{box}  输出 {crop.size[0]}x{crop.size[1]}")


if __name__ == "__main__":
    main()
