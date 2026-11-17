# -*- coding: utf-8 -*-
"""给试卷文档里的「知识点 / 章节 / 考点」补上指向讲义的直达链接。

背景：练习题集_逐题精析.md 的每道题、期末习题集每道大题的【考点】，都写明了
      "章节：第 N 章 §x.y 某某"，但一直是纯文本，读者无法一键跳到讲义。
      本脚本把这些引用原地追加成 Markdown 相对链接。

设计要点：
  1. 只增不删：只在行尾追加 `　→　[直达 §x.y 标题](../第N章_xxx/x.y_xxx.md)`，
     原行文字一个字符都不改。
  2. 链接文字取自目标讲义的真实 H1（去掉英文括注），因此**读者看到的就是点开后的节号**；
     文档沿用的课堂节号与仓库文件序号不一致时，以链接文字为准。
  3. 幂等：已经带 `→　[直达` 的行直接跳过，可以反复运行。
  4. 未命中即报错：映射表里没有的引用会打印出来并以退出码 1 结束，绝不静默跳过。

用法：
  python scripts/link_lecture.py --check     # 只报告，不写盘
  python scripts/link_lecture.py --apply     # 写盘（同时同步折叠前备份）
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZJ = os.path.join(ROOT, "试卷", "练习题集_逐题精析.md")
QM = os.path.join(ROOT, "试卷", "数字电路与逻辑_期末习题集_含答案与解析.md")
BACKUP = os.path.join(ROOT, "提取", "习题集_折叠前备份.md")

# ---------------------------------------------------------------- 目标讲义清单
CH1 = "第1章_模拟电子元件与分立电子元件"
CH2 = "第2章_数字电路基础"
CH3 = "第3章_逻辑代数基础"
CH4 = "第4章_组合逻辑电路"
CH5 = "第5章_触发器"
CH6 = "第6章_时序逻辑电路"
CH7 = "第7章_集成运算放大器和脉冲波形的产生与变换"

TARGETS = {
    "1.2": f"{CH1}/1.2_二极管.md",
    "1.3": f"{CH1}/1.3_晶体管BJT.md",
    "1.6": f"{CH1}/1.6_分立元件组成的基本门电路.md",
    "2.1": f"{CH2}/2.1_数字电路及其分类与特点.md",
    "2.2": f"{CH2}/2.2_数制与数制转换.md",
    "2.3": f"{CH2}/2.3_码制.md",
    "2.4": f"{CH2}/2.4_机器数与码制.md",
    "3.3": f"{CH3}/3.3_公理定理与规则.md",
    "3.5": f"{CH3}/3.5_两种标准形式.md",
    "3.6": f"{CH3}/3.6_公式化简法.md",
    "3.7": f"{CH3}/3.7_卡诺图化简法.md",
    "4.1": f"{CH4}/4.1_组合逻辑电路的分析方法.md",
    "4.2": f"{CH4}/4.2_组合逻辑电路的设计方法.md",
    "4.4": f"{CH4}/4.4_译码器.md",
    "4.5": f"{CH4}/4.5_数据选择器与数据分配器.md",
    "4.7": f"{CH4}/4.7_加法器.md",
    "5.1": f"{CH5}/5.1_基本RS触发器.md",
    "5.ch": f"{CH5}/第5章_例题与考法解析.md",
    "6.2": f"{CH6}/6.2_时序逻辑电路的分析方法.md",
    "6.3": f"{CH6}/6.3_同步时序逻辑电路的设计.md",
    "6.5": f"{CH6}/6.5_计数器.md",
    "6.6": f"{CH6}/6.6_数码寄存器与移位寄存器.md",
    "7.1": f"{CH7}/7.1_放大器与集成运放基础.md",
    "7.3": f"{CH7}/7.3_集成555定时器.md",
    "7.5": f"{CH7}/7.5_555构成的施密特触发器.md",
}

# 逐题精析：文档里的「章节」原文 → 目标节号（None 表示讲义未收录，只加备注）
MAP_ZJ = {
    "第 1 章 §1.1 电路基本定律": None,
    "第 1 章 §1.1": None,
    "第 1 章 §1.2 半导体二极管": "1.2",
    "第 1 章 §1.2 二极管应用": "1.2",
    "第 1 章 §1.2": "1.2",
    "第 1 章 §1.3 半导体三极管": "1.3",
    "第 1 章 §1.3": "1.3",
    "第 1 章 §1.4 分立元件门电路": "1.6",
    "第 1 章 §1.4": "1.6",
    "第 2 章 §2.1 数制": "2.2",
    "第 2 章 §2.1": "2.2",
    "第 2 章 §2.2 码制": "2.3",
    "第 2 章 §2.2": "2.3",
    "第 2 章 §2.3 带符号数的表示": "2.4",
    "第 2 章 §2.3": "2.4",
    "第 2 章 §2.4 校验码": "2.3",
    "第 3 章 §3.1 逻辑代数基本定律": "3.3",
    "第 3 章 §3.1": "3.3",
    "第 3 章 §3.2 逻辑代数三条规则": "3.3",
    "第 3 章 §3.2": "3.3",
    "第 3 章 §3.3 公式化简法": "3.6",
    "第 3 章 §3.4 卡诺图化简": "3.7",
    "第 3 章 §3.4": "3.7",
    "第 3 章 §3.5 最小项与标准形式": "3.5",
    "第 3 章 §3.5": "3.5",
    "第 4 章 §4.2 组合逻辑电路设计": "4.2",
    "第 4 章 §4.2": "4.2",
    "第 4 章 §4.4 译码器应用": "4.4",
    "第 4 章 §4.4": "4.4",
    "第 4 章 §4.5 数据选择器应用": "4.5",
    "第 5 章 §5.1 触发器概述": "5.1",
    "第 6 章 §6.2 时序逻辑电路分析": "6.2",
    "第 6 章 §6.2": "6.2",
    "第 6 章 §6.3 时序逻辑电路设计": "6.3",
    "第 6 章 §6.3": "6.3",
    "第 6 章 §6.4 计数器": "6.5",
    "第 7 章 §7.1 集成运放基础": "7.1",
    "第 7 章 §7.1": "7.1",
    "第 7 章 §7.3 555 定时器": "7.3",
    "第 7 章 §7.3": "7.3",
    "第 7 章 §7.4 施密特触发器": "7.5",
}

# 期末习题集：整行原文 → 目标节号列表（一题可能跨两节）
MAP_QM = {
    "第 3 章 逻辑代数基础 —— 反演规则 / 德摩根定律。": ["3.3"],
    "第 3 章 —— 对偶规则。": ["3.3"],
    "第 3 章 —— 反演规则与对偶规则。": ["3.3"],
    "第 3 章 —— 卡诺图化简（格雷码排列、循环相邻、每圈须含新 1）。": ["3.7"],
    "第 3 章 —— 公式化简法（吸收律 $X+XY=X$、并项 $X+\\bar XY=X+Y$）。": ["3.6"],
    "第 3 章 —— 逻辑函数的标准形式。": ["3.5"],
    "第 2 章 —— 数制转换。": ["2.2"],
    "第 2 章 —— 数字电路基础 / 门电路。": ["2.1"],
    "第 2 章 —— TTL 集成门电路。": ["1.6"],
    "第 4 章 —— 组合逻辑电路的设计。": ["4.2"],
    "第 4 章 —— 组合逻辑电路的分析与设计。": ["4.1", "4.2"],
    "第 4 章 —— 加法器；Verilog HDL 基础语法。": ["4.7"],
    "第 5 章 时序逻辑电路 —— 触发器特性方程。": ["5.ch"],
    "第 5 章 —— 5.2 时序逻辑电路的分析方法。": ["6.2"],
    "第 5 章 —— 5.3 同步时序逻辑电路的设计（状态图的建立）。": ["6.3"],
    "第 5 章 —— 时序逻辑电路分析（Mealy/Moore 判别、方程 → 表 → 图 → 功能 → 自启动）。": ["6.2"],
    "第 5 章 —— 寄存器与移位寄存器；Verilog 时序逻辑建模。": ["6.6"],
    "第 6 章 —— 6.1 放大器与集成运放基础。": ["7.1"],
    "第 6 章 —— 6.3 集成 555 定时器。": ["7.3"],
    "第 6 章 —— 6.1.3 开环工作的比较器、6.5 555 构成的施密特触发器。": ["7.1", "7.5"],
}

MARK = "→\u3000[直达"
SEP = "\u3000→\u3000"  # 全角空格 → 箭头 全角空格


def short_title(path: str) -> str:
    """取目标讲义 H1 的中文部分作为链接文字（去掉英文括注）。"""
    with open(os.path.join(ROOT, path), "r", encoding="utf-8") as handle:
        head = handle.readline().strip()
    title = head.lstrip("# ").strip()
    for bracket in ("（", "("):
        if bracket in title:
            title = title.split(bracket)[0].strip()
    if "例题与考法解析" in title:
        match = re.search(r"第\s*(\d+)\s*章", title)
        if match:
            return f"第 {match.group(1)} 章 例题与考法解析"
    return title


def link_of(key: str) -> str:
    path = TARGETS[key]
    return f"[直达 {short_title(path)}](../{path})"


def build(rel_label: str) -> str:
    return SEP + link_of(rel_label) + "\n"


UNMATCHED: list[str] = []


def rewrite_zj(text: str) -> tuple[str, int]:
    """逐题精析：每行 `**考点**：…　｜　**章节**：<label>` 行尾追加链接。"""
    pattern = re.compile(r"(\*\*章节\*\*：)([^｜\n]+?)\s*$")
    out, hits = [], 0
    for line in text.split("\n"):
        if MARK in line:
            out.append(line)
            continue
        found = pattern.search(line)
        if not found:
            out.append(line)
            continue
        label = found.group(2).strip()
        if label not in MAP_ZJ:
            UNMATCHED.append(f"[逐题精析] 章节「{label}」")
            out.append(line)
            continue
        key = MAP_ZJ[label]
        if key is None:
            note = "\u3000→\u3000〔讲义未单列此节：第 1 章自模拟电子技术起讲，KCL/KVL 属电路分析基础〕"
            out.append(line + note)
        else:
            out.append(line + build(key).rstrip("\n"))
        hits += 1
    return "\n".join(out), hits


def rewrite_qm(text: str) -> tuple[str, int]:
    """期末习题集：`**考点**：<原文>` 行尾追加链接。"""
    out, hits = [], 0
    for line in text.split("\n"):
        if MARK in line or not line.startswith("**考点**："):
            out.append(line)
            continue
        body = line[len("**考点**："):].strip()
        if body not in MAP_QM:
            UNMATCHED.append(f"[期末习题集] 考点「{body}」")
            out.append(line)
            continue
        links = "、".join(link_of(key) for key in MAP_QM[body])
        out.append(line + SEP + links)
        hits += 1
    return "\n".join(out), hits


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="写盘")
    parser.add_argument("--check", action="store_true", help="只报告")
    args = parser.parse_args()
    write = args.apply and not args.check

    jobs = [(ZJ, rewrite_zj), (QM, rewrite_qm)]
    if os.path.exists(BACKUP):
        jobs.append((BACKUP, rewrite_qm))

    for path, func in jobs:
        with open(path, "r", encoding="utf-8", newline="") as handle:
            raw = handle.read()
        crlf = "\r\n" in raw
        text = raw.replace("\r\n", "\n")
        new, hits = func(text)
        if not new.endswith("\n"):
            new += "\n"
        changed = new != text
        print(f"{os.path.relpath(path, ROOT)}：新增链接 {hits} 处，{'有变化' if changed else '无变化'}")
        if write and changed:
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(new.replace("\n", "\r\n") if crlf else new)

    if UNMATCHED:
        print("\n未命中的引用（映射表缺项）：")
        for item in UNMATCHED:
            print("  " + item)
        return 1
    print("\n全部引用均已命中映射表。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
