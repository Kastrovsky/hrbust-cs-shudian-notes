#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重建技能索引：扫描用户级与项目级技能目录，生成 references/skill-index.md。

用法：
    python refresh_index.py                 # 扫描默认目录，重写索引
    python refresh_index.py --root <目录>   # 额外扫描一个技能目录
    python refresh_index.py --check         # 只检查不写文件

作用：
1. 列出每个技能的 name、职责摘要、触发词、实体位置（本地目录 / 目录联接）。
2. 与 SKILL.md 路由表比对，报告"尚未登记到路由表"的新技能，提醒补表。
"""

import os
import re
import sys

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDEX_PATH = os.path.join(SKILL_DIR, "references", "skill-index.md")
SKILL_MD = os.path.join(SKILL_DIR, "SKILL.md")

USER_SKILLS = os.path.expanduser("~/.workbuddy/skills")


def parse_frontmatter(path):
    """解析 SKILL.md 的 YAML 头，返回 {name, description}；解析失败返回空字典。

    只处理本项目实际用到的扁平结构（key: value + 缩进续行），不引入 yaml 依赖。
    """
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            text = fh.read().replace("\r\n", "\n").replace("\r", "\n")
    except OSError:
        return {}
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end < 0:
        return {}
    data = {}
    key = None
    for line in text[3:end].strip("\n").split("\n"):
        if line[:1] in " \t" and key:
            data[key] += " " + line.strip()
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            key = k.strip()
            data[key] = v.strip()
    desc = data.get("description", "").strip()
    if desc.startswith('"') and desc.endswith('"'):
        desc = desc[1:-1]
    data["description"] = desc
    name = data.get("name", "").strip().strip('"').strip("'")
    data["name"] = name
    return data


def short(text, limit):
    """压成一行并截断，用于表格单元格。"""
    text = " ".join(text.split()).strip(" |")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def scan(root):
    """扫描一个技能目录，返回技能信息列表。"""
    out = []
    if not os.path.isdir(root):
        return out
    for entry in sorted(os.listdir(root)):
        path = os.path.join(root, entry)
        skill_md = os.path.join(path, "SKILL.md")
        if not os.path.isdir(path) or not os.path.exists(skill_md):
            continue
        meta = parse_frontmatter(skill_md)
        real = os.path.realpath(path)
        out.append(
            {
                "dir": entry,
                "name": meta.get("name", entry),
                "desc": meta.get("description", ""),
                "path": path,
                "real": real,
                "linked": os.path.normcase(real) != os.path.normcase(path),
                "scope": "用户级" if root == USER_SKILLS else "项目级",
            }
        )
    return out


def registered_names():
    """从 SKILL.md 的「路由表」小节中提取已登记的技能名（反引号包裹的标识符）。

    只取路由表那一段，不看组合规则与维护说明，避免把"提到过"误判成"登记过"。
    """
    try:
        with open(SKILL_MD, encoding="utf-8", errors="ignore") as fh:
            text = fh.read()
    except OSError:
        return set()
    start = text.find("## 路由表")
    end = text.find("## 组合规则")
    section = text[start:end] if start >= 0 and end > start else text
    return {m for m in re.findall(r"`([a-zA-Z0-9_\-\.]{3,})`", section)}


def build(roots):
    rows = []
    for root in roots:
        rows.extend(scan(root))
    rows.sort(key=lambda r: (r["scope"], r["name"]))
    return rows


def render(rows, known):
    lines = [
        "# 技能索引（自动生成）",
        "",
        "> 由 `scripts/refresh_index.py` 生成，请勿手工编辑；技能有增减时重跑脚本。",
        "",
        "| 作用域 | 技能名 | 职责摘要（取自 description） | 实体位置 |",
        "|---|---|---|---|",
    ]
    for r in rows:
        loc = "D 盘实体（联接）" if r["linked"] else "本地目录"
        lines.append(
            "| {} | `{}` | {} | {} |".format(
                r["scope"],
                r["name"],
                short(r["desc"], 120) or "（无描述）",
                loc,
            )
        )
    # 路由表不登记自己，跳过，否则每次刷新都报一条假缺口
    unknown = [
        r["name"]
        for r in rows
        if r["name"] not in known and r["dir"] not in known and r["name"] != "skill-router"
    ]
    lines += ["", "## 统计", "", "- 技能总数：{}".format(len(rows))]
    if unknown:
        lines += ["", "## 尚未登记到路由表", ""]
        lines += ["- `{}`".format(n) for n in unknown]
        lines += ["", "请把以上技能补进 `SKILL.md` 的路由表。"]
    else:
        lines += ["", "全部技能均已登记到路由表。"]
    return "\n".join(lines) + "\n", unknown


def main():
    roots = [USER_SKILLS]
    project = os.path.join(os.getcwd(), ".workbuddy", "skills")
    if os.path.isdir(project):
        roots.append(project)
    check_only = "--check" in sys.argv
    if "--root" in sys.argv:
        roots.append(sys.argv[sys.argv.index("--root") + 1])

    rows = build(roots)
    if not rows:
        print("未找到任何技能，请确认目录：", ", ".join(roots))
        return 1
    text, unknown = render(rows, registered_names())
    print("扫描目录：")
    for root in roots:
        print("  -", root)
    print("技能总数：", len(rows))
    if unknown:
        print("未登记到路由表：", ", ".join(unknown))
    else:
        print("全部已登记到路由表 ✓")
    if check_only:
        print("（--check 模式，未写入文件）")
        return 0
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    with open(INDEX_PATH, "w", encoding="utf-8") as fh:
        fh.write(text)
    print("已写入：", INDEX_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
