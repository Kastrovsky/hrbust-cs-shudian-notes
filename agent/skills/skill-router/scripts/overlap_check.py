# -*- coding: utf-8 -*-
"""overlap_check.py —— 建技能前的查重与归类检查。

解决什么：技能越建越多，"这个也能干一点、那个也能干一点"的重叠是常态，
建第三个之前如果不查，就会出现三个技能抢同一批触发词、每次都要仲裁。
本脚本把"像不像"量化出来，让"新建 vs 优化现有"有据可依，而不是凭感觉。

两种用法
    --all                     现有技能两两比对，找潜在重叠对（合并/加边界/改触发词）
    --candidate "描述或原话"    拿一个候选去比，看现有技能覆盖到什么程度

判定口径（candidate 模式）
    ≥0.50  已经有技能覆盖 → **优化那个**，别新建
    0.28~0.50 部分覆盖 → 读该技能 SKILL.md 的「边界」节，决定扩边界还是新建
    <0.30  基本没覆盖 → 可新建，但要再跑触发词撞车检查（--all 里看有没有同词）

退出码：--all 默认恒 0；加 --gate 时若有对超过阈值则返回 1，便于定期巡检。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL_MD = os.path.join(BASE, "SKILL.md")
INDEX_MD = os.path.join(BASE, "references", "skill-index.md")

# 只关心"自建技能"之间的重叠；市场技能一大半是工具型，比对结果的噪声大于价值
OWN_SKILLS_FILE = os.path.join(BASE, "references", "skill-index.md")


def read_text(p):
    try:
        with open(p, encoding="utf-8", errors="ignore") as fh:
            return fh.read().replace("\r\n", "\n")
    except OSError:
        return ""


def load_descriptions():
    """从 skill-index.md 取 {技能: 职责摘要}。"""
    out = {}
    for line in read_text(INDEX_MD).split("\n"):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        m = re.search(r"`([A-Za-z0-9_\-\.]+)`", cells[1])
        if m:
            out[m.group(1)] = cells[2]
    return out


def load_triggers():
    """从 skill-router/SKILL.md 的「## 触发词」小节取 {技能: [词]}。"""
    text = read_text(SKILL_MD)
    start = text.find("## 触发词")
    if start < 0:
        return {}
    end = text.find("\n## ", start + 3)
    section = text[start:end] if end > start else text[start:]
    out = {}
    for line in section.split("\n"):
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue
        m = re.search(r"`([A-Za-z0-9_\-\.]+)`", cells[0])
        if not m:
            continue
        kws = [k.strip(" `") for k in re.split(r"[,，;；]", cells[1])]
        out[m.group(1)] = [k for k in kws if len(k) >= 2]
    return out


def bigrams(text):
    text = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text or "")
    return {text[i:i + 2] for i in range(len(text) - 1)}


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def coverage(cand, target):
    """候选被目标覆盖的比例：候选的特征有多大比例在目标里出现过。"""
    if not cand:
        return 0.0
    return len(cand & target) / len(cand)


def name_sim(a, b):
    ta = set(a.lower().replace("_", "-").split("-"))
    tb = set(b.lower().replace("_", "-").split("-"))
    return jaccard(ta, tb)


def score_pair(cand_kw, cand_desc, name, desc, trig):
    """三项加权：触发词覆盖 0.5 / 描述覆盖 0.4 / 名称成分 0.1。"""
    return (0.5 * coverage(cand_kw, set(trig.get(name, [])))
            + 0.4 * coverage(cand_desc, bigrams(desc.get(name, "")))
            + 0.1 * 0.0)  # 名称相似度在 candidate 模式下另算


def cmd_all(args):
    desc, trig = load_descriptions(), load_triggers()
    names = [n for n in desc if n in trig]          # 只看自建技能（有触发词登记的）
    names.sort()
    pairs = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            s_trig = jaccard(set(trig[a]), set(trig[b]))
            s_desc = jaccard(bigrams(desc[a]), bigrams(desc[b]))
            s_name = name_sim(a, b)
            score = 0.55 * s_trig + 0.40 * s_desc + 0.05 * s_name
            pairs.append((score, a, b, s_trig, s_desc))
    pairs.sort(reverse=True)

    # 永远打前几名：低于阈值不代表"没关系"，只是"还不必动"，看得见才判得准
    shown = [p for p in pairs if p[0] >= args.floor][:args.top]
    hot = [p for p in pairs if p[0] >= args.threshold]

    print("重叠分前 %d 名（告警阈值 %.2f，地板 %.2f，比对 %d 个技能）：\n"
          % (len(shown), args.threshold, args.floor, len(names)))
    for score, a, b, st, sd in shown:
        mark = "⚠ " if score >= args.threshold else "  "
        print("%s%-5.2f  %-24s ~ %-24s  触发词 %.2f / 描述 %.2f" % (mark, score, a, b, st, sd))
        if score >= args.threshold:
            print("         %s" % advice(score))
    if not shown:
        print("  全部低于地板分 %.2f —— 技能之间彼此独立，没有归类可做。" % args.floor)
    print("\n告警 %d 对。处理顺序：先看高分的，能加边界就别合并，能合并就别新建。" % len(hot))
    return 1 if (args.gate and hot) else 0


def advice(score):
    if score >= 0.50:
        return "高度重叠 → 合并成一个（保留触发词更全的那个名字），或把其中一个降为子流程"
    if score >= 0.35:
        return "中度重叠 → 在各自 SKILL.md「边界」里写明何时不用自己，并错开触发词"
    return "轻度重叠 → 不动，留意就行"


def cmd_candidate(args):
    desc, trig = load_descriptions(), load_triggers()
    cand_kw = set(k.strip() for k in re.split(r"[,，;；]", args.triggers or "") if len(k.strip()) >= 2)
    cand_desc = bigrams(args.candidate)
    rows = []
    for name in desc:
        s = score_pair(cand_kw, cand_desc, name, desc, trig)
        if s > 0:
            rows.append((s, name))
    rows.sort(reverse=True)
    if not rows:
        print("没有任何现有技能沾边 → 可以新建（仍要检查触发词别撞车）")
        return 0
    print("候选与现有技能的覆盖度：\n")
    for s, name in rows[:args.top]:
        print("%-5.2f  %s" % (s, name))
    top_s, top_n = rows[0]
    print("\n判定：", end="")
    if top_s >= 0.50:
        print("已被 `%s` 覆盖 → **优化它**（补判据/补脚本/扩边界/加触发词），不要新建。" % top_n)
    elif top_s >= 0.28:
        print("与 `%s` 部分重叠 → 先读它 SKILL.md 的「边界」节：说得清「何时不用它」再新建；"
              "说不清就扩它。" % top_n)
    else:
        print("覆盖度低 → 可新建。新建后跑 `--all` 确认新触发词没跟别人撞车。")
    return 0


def main():
    ap = argparse.ArgumentParser(description="技能重叠检查：建前查重、定期归类")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("all", help="现有技能两两比对")
    p.add_argument("--threshold", type=float, default=0.30, help="告警阈值")
    p.add_argument("--floor", type=float, default=0.10, help="展示地板分，低于此不列")
    p.add_argument("--top", type=int, default=8)
    p.add_argument("--gate", action="store_true", help="有重叠即返回 1（巡检用）")
    p.set_defaults(func=cmd_all)

    p = sub.add_parser("candidate", help="候选 vs 现有技能")
    p.add_argument("candidate", help="候选技能的职责描述（一句话）")
    p.add_argument("--triggers", help="候选的触发词，逗号分隔")
    p.add_argument("--top", type=int, default=5)
    p.set_defaults(func=cmd_candidate)

    args = ap.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
