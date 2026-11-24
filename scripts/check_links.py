# -*- coding: utf-8 -*-
"""全仓体检：相对链接 + 图片引用是否断链。

为什么需要它：本仓库有 300+ 条跨文件链接（`../第N章_xxx/x.y_xxx.md`）和 200+ 张配图，
移动/重命名任何一个文件都可能悄悄打断一批引用，而 Gitee/GitHub 上只是"点不动"，不会报错。
所以每次改结构、改名、搬目录之后，先跑这个。

规则：
  · 只查**相对路径**（http/https/mailto 跳过）；
  · 忽略行内代码与围栏代码块里的示例链接（`` `[文字](路径)` `` 是文档示例，不是真链接）；
  · 忽略 .git / 提取 / .workbuddy / .review / node_modules；
  · 任何一条不存在即退出码 1。

用法：python scripts/check_links.py
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_DIRS = {".git", "提取", ".workbuddy", ".review", "node_modules", "node_modules_cache"}

FENCE = re.compile(r"```.*?```", re.S)
INLINE_CODE = re.compile(r"`[^`\n]*`")
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\((?!#)([^)#]+)(#[^)]*)?\)")
IMAGE = re.compile(r"!\[[^\]]*\]\(([^)]+)\)")


def iter_md():
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for name in files:
            if name.endswith(".md"):
                yield os.path.join(base, name)


def main() -> int:
    total_l, total_i, bad = 0, 0, []
    for path in iter_md():
        text = open(path, encoding="utf-8").read()
        text = INLINE_CODE.sub("", FENCE.sub("", text))
        rel = os.path.relpath(path, ROOT)
        for m in LINK.finditer(text):
            target = m.group(1).strip()
            if not target or target.startswith(("http://", "https://", "mailto:", "tel:")):
                continue
            total_l += 1
            if not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(path), target))):
                bad.append((rel, target, "链接"))
        for m in IMAGE.finditer(text):
            target = m.group(1).strip()
            if target.startswith(("http://", "https://", "data:")):
                continue
            total_i += 1
            if not os.path.exists(os.path.normpath(os.path.join(os.path.dirname(path), target))):
                bad.append((rel, target, "图片"))

    print(f"相对链接 {total_l} 条 / 图片引用 {total_i} 条 / Markdown {sum(1 for _ in iter_md())} 篇")
    if bad:
        print(f"\n[不通过] 断链 {len(bad)} 处：")
        for rel, target, kind in bad:
            print(f"  {kind}  {rel}  ->  {target}")
        return 1
    print("[通过] 0 断链")
    return 0


if __name__ == "__main__":
    sys.exit(main())
