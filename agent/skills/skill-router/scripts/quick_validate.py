#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能完整性校验（quick validate）。

用途：新建或修改技能后跑一遍，拦住"写了却不存在"这类问题。
典型症状是 SKILL.md 里引用 `scripts/foo.py` 或 `references/bar.md`，
文件其实没建；下次照着执行就直接报错，而人读文档时看不出来。

用法：
    python quick_validate.py              # 扫本机技能目录下全部技能
    python quick_validate.py skill-router # 只校验一个

退出码：0 全部通过；1 有 FAIL。
"""

import os
import re
import sys

# 技能实体根目录：默认取本脚本上两级（…/skills/skill-router/scripts → …/skills）。
# 想让脚本扫别的目录，设环境变量 SKILLS_HOME，或直接用命令行参数指定。
ROOT = os.environ.get(
    "SKILLS_HOME",
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
)

# frontmatter 必须有这两个字段，缺一个技能就不会被平台正确识别
REQUIRED_FIELDS = ("name", "description")

# description 过长会被平台截断，导致触发词丢失
DESC_MAX = 1024

# 正文最短字数。短于此基本是空壳，没有可执行流程
BODY_MIN = 200


def read_text(path):
    """按 UTF-8 读取，读失败返回 None（避免编码问题中断整轮扫描）。"""
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def parse_frontmatter(text):
    """拆出 frontmatter 字典与正文。

    只认文件开头的 --- ... --- 块；正文里出现的 --- 不当分隔符。
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    block = text[3:end]
    body = text[end + 4:]

    meta = {}
    for line in block.splitlines():
        # 只取 key: value 形式的简单行；多行值（|）在技能里很少用，忽略即可
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.+)$", line.strip())
        if m:
            meta[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return meta, body


def find_elsewhere(rel):
    """查同一相对路径是否存在于别的技能目录下。

    技能文档里常引用别家的脚本（如 `scripts/check_note.py` 其实是
    handwritten-study-notes 的）。这种情况下"本目录没有"不算错，
    否则每个交叉引用都会被误报成缺文件。
    """
    for d in os.listdir(ROOT):
        if d.startswith("."):
            continue
        if os.path.exists(os.path.join(ROOT, d, rel)):
            return d
    return None


def check_refs(skill_dir, body):
    """校验正文里用反引号包住的相对路径是否真实存在。

    只查带扩展名的（scripts/x.py、references/y.md 这种），
    纯名字（如 `check_note.py`）无法判断相对位置，跳过不误报。
    返回 (缺失列表, 跨技能引用列表)。
    """
    missing, external = [], []
    # 匹配 `dir/name.ext`，要求斜杠前是 scripts/references/assets 之类
    for m in re.finditer(r"`((?:scripts|references|assets|templates)/[\w\-./]+\.\w+)`", body):
        rel = m.group(1)
        if os.path.exists(os.path.join(skill_dir, rel)):
            continue
        owner = find_elsewhere(rel)
        if owner:
            external.append("%s（属 %s）" % (rel, owner))
        else:
            missing.append(rel)
    return sorted(set(missing)), sorted(set(external))


def validate(skill_dir):
    """校验单个技能目录，返回 (错误列表, 警告列表)。"""
    errors, warns = [], []
    name = os.path.basename(skill_dir)
    skill_md = os.path.join(skill_dir, "SKILL.md")

    if not os.path.isfile(skill_md):
        return ["缺少 SKILL.md"], warns

    text = read_text(skill_md)
    if text is None:
        return ["SKILL.md 不是合法 UTF-8"], warns
    if not text.strip():
        return ["SKILL.md 是空文件"], warns

    meta, body = parse_frontmatter(text)

    # 1) frontmatter 必备字段
    for field in REQUIRED_FIELDS:
        if not meta.get(field):
            errors.append("frontmatter 缺 `%s`" % field)

    # 2) name 与目录名一致（平台按目录名定位，不一致会导致加载失败）
    declared = meta.get("name", "")
    if declared and declared != name:
        errors.append("frontmatter name=`%s` 与目录名 `%s` 不一致" % (declared, name))

    # 3) description 长度
    desc = meta.get("description", "")
    if len(desc) > DESC_MAX:
        errors.append("description %d 字，超过 %d 会被截断" % (len(desc), DESC_MAX))
    elif desc and len(desc) < 40:
        warns.append("description 仅 %d 字，触发词可能不足" % len(desc))

    # 4) 正文长度
    if len(body.strip()) < BODY_MIN:
        warns.append("正文仅 %d 字，流程可能不完整" % len(body.strip()))

    # 5) 正文引用的脚本/文件是否真实存在 —— 本条最实用
    missing, external = check_refs(skill_dir, body)
    for rel in missing:
        errors.append("正文引用了不存在的文件：`%s`" % rel)
    for rel in external:
        warns.append("跨技能引用：`%s`" % rel)

    # 6) 空文件（常见于占位后忘了写）
    # 运行时产物与有意占位不算：hook-trace.log 在首次触发前就是 0 字节，
    # .writing 是原子写的中间态，.keep/.gitkeep 是特意留的空占位。
    runtime_ext = {".log", ".writing", ".lock"}
    placeholders = {".keep", ".gitkeep"}
    for dirpath, _dirnames, filenames in os.walk(skill_dir):
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() in runtime_ext or fn in placeholders:
                continue
            fp = os.path.join(dirpath, fn)
            if os.path.getsize(fp) == 0:
                warns.append("空文件：%s" % os.path.relpath(fp, skill_dir))

    return errors, warns


def main():
    if len(sys.argv) > 1:
        targets = [os.path.join(ROOT, a) if not os.path.isdir(a) else a
                   for a in sys.argv[1:]]
    else:
        targets = sorted(
            os.path.join(ROOT, d) for d in os.listdir(ROOT)
            if os.path.isdir(os.path.join(ROOT, d)) and not d.startswith(".")
        )

    fail = 0
    for d in targets:
        if not os.path.isdir(d):
            print("SKIP  目录不存在：%s" % d)
            continue
        errors, warns = validate(d)
        tag = "FAIL" if errors else ("WARN" if warns else "PASS")
        print("%-5s %s" % (tag, os.path.basename(d)))
        for e in errors:
            print("        ✗ %s" % e)
        for w in warns:
            print("        · %s" % w)
        if errors:
            fail += 1

    print("-" * 56)
    print("共 %d 个技能，FAIL %d 个" % (len(targets), fail))
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
