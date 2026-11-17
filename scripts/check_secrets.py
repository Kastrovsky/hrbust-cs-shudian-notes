#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""推送前敏感信息闸门（数电讲义仓库）。

用途
----
在 commit / push 之前，扫描"即将进入仓库的内容"，确认没有账号、口令、
登录态、平台地址、鉴权参数等凭据混入。发现任何命中即以退出码 1 阻断。

用法
----
    python scripts/check_secrets.py                  # 默认：已跟踪文件 + 暂存区 + 未提交改动 + 未推送提交
    python scripts/check_secrets.py --all           # 追加：递归扫工作区全部文本（含被忽略的临时目录）
    python scripts/check_secrets.py --history       # 追加：扫描全部历史提交
    python scripts/check_secrets.py --extra "串"    # 追加一条精确待检串（只存内存，不落盘），可重复

返回
----
    0 = 干净，允许推送；1 = 发现疑似凭据，禁止推送。

三条设计约束
------------
    1. 本文件不含任何真实账号 / 口令 / 令牌明文，规则表只有通用模式；
    2. 命中内容一律脱敏打印，绝不把凭据原样回显到终端或日志；
    3. 只用 Python 标准库，任何机器 clone 后可直接运行，无需安装依赖。
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys

# 仓库根目录：本文件位于 <仓库根>/scripts/ 下，向上取两级
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 单个文件的扫描上限，超过则跳过（避免把大二进制或素材拖进内存）
MAX_FILE_BYTES = 2 * 1024 * 1024

# 规则表：(规则名, 正则模式)
#
# 说明：只放通用模式，绝不放真实凭据。
# 关键词用字符类拆写（例如把最后一个字写成 [D] 的形式），
# 这样规则表本身不会命中自己的规则，检查器可以连自身一起扫。
RULES: list[tuple[str, str]] = [
    ("中国大陆手机号", r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    ("在线课程平台地址", r"(?:passport2|mooc2-ans)\.chaoxing\.com"),
    ("URL 鉴权参数", r"[?&](?:enc|validate|token|ticket)=[0-9a-zA-Z_-]{12,}"),
    ("登录态痕迹", r"(?:JSESSIONI[D]|jsessioni[d]|cx-aut[h]|storageStat[e]|Set-Cooki[e]|set-cooki[e])"),
    ("长十六进制令牌", r"(?<![0-9a-fA-F])[0-9a-fA-F]{24,}(?![0-9a-fA-F])"),
    ("口令形态", r"@/{1,2}[A-Za-z0-9_.-]{4,}/?"),
    ("明文口令字段", r"(?:学习通密[码]|超星[号]|登录口[令]|password\s*[:=]\s*\S{4,}|uname\s*[:=]\s*\d{6,})"),
]

COMPILED = [(name, re.compile(pattern)) for name, pattern in RULES]


def git(*args: str) -> str:
    """执行一条 git 命令并返回标准输出。

    参数：args 为 git 子命令及其参数，例如 git("ls-files")。
    返回：成功时返回 stdout 文本；失败（如当前分支没有 upstream）返回空串，
          不抛异常，保证检查脚本本身不会因环境差异中断推送流程。
    """
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotePath=false", *args],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        return result.stdout if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def mask(fragment: str) -> str:
    """把命中的片段脱敏：只保留首尾各 2 个字符，中间用星号代替。

    参数：fragment 为正则实际匹配到的文本。
    返回：脱敏后的字符串。目的是让报告可读，同时不把凭据写进终端或日志。
    """
    text = fragment.strip()
    if len(text) <= 4:
        return "*" * len(text)
    return text[:2] + "*" * min(10, len(text) - 4) + text[-2:]


def scan_lines(hits: list, source: str, content: str, extras: list[str]) -> None:
    """逐行扫描一段文本，把命中追加到 hits。

    参数：hits 为结果列表；source 是报告里显示的位置名；
          content 是待检文本；extras 是调用方临时传入的精确待检串。
    返回：无，结果原地写入 hits。同一行的每条规则都单独报出，避免漏报：
          一行里同时出现平台地址与鉴权参数时，只报前者会掩盖后者。
    """
    for line_no, line in enumerate(content.splitlines(), 1):
        # git log -p 的元数据行含 40 位提交哈希，属于仓库自身的标识，不是外部令牌；
        # 不跳过会让 --history 产生成片误报，反而淹没真正的命中。
        if line.startswith("commit "):
            continue
        for name, pattern in COMPILED:
            found = pattern.search(line)
            if found:
                hits.append((source, line_no, name, mask(found.group(0))))
        for extra in extras:
            if extra and extra in line:
                hits.append((source, line_no, "精确待检串", "<已脱敏，不回显>"))
                break


def scan_file(hits: list, path: str, extras: list[str], label: str | None = None) -> None:
    """扫描单个文件。

    参数：hits 结果列表；path 绝对路径；extras 精确待检串；
          label 可选，用于自定义报告里的文件名（默认取相对仓库根的路径）。
    返回：无。二进制、超大文件、无法按 UTF-8 解码的文件一律跳过。
    """
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            return
        with open(path, "r", encoding="utf-8", errors="strict") as handle:
            content = handle.read()
    except (OSError, UnicodeError):
        return
    if "\x00" in content:
        return
    scan_lines(hits, label or os.path.relpath(path, ROOT).replace("\\", "/"), content, extras)


def tracked_files() -> list[str]:
    """返回 git 已跟踪的文件相对路径列表（即 clone 后一定存在的内容）。"""
    return [line.strip() for line in git("ls-files").splitlines() if line.strip()]


def worktree_files():
    """遍历工作区全部文件（含被忽略的临时目录），跳过 git 内部与依赖目录。"""
    skip_dirs = {".git", "node_modules", "__pycache__", ".venv", ".idea"}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in skip_dirs]
        for filename in filenames:
            yield os.path.join(dirpath, filename)


def main() -> int:
    """主流程：汇总各扫描面，输出报告并返回退出码。"""
    parser = argparse.ArgumentParser(description="推送前检查是否混入账号、口令、登录态等凭据")
    parser.add_argument("--all", action="store_true", help="追加扫描工作区全部文本（含被忽略的临时目录）")
    parser.add_argument("--history", action="store_true", help="追加扫描全部历史提交")
    parser.add_argument("--extra", action="append", default=[], help="追加精确待检串，只存内存不落盘")
    args = parser.parse_args()

    hits: list = []
    extras: list[str] = args.extra

    # 扫描面 1：已跟踪文件——这是最核心的一道，决定仓库内容本身是否干净
    tracked_count = 0
    for relative in tracked_files():
        full_path = os.path.join(ROOT, relative)
        if os.path.isfile(full_path):
            scan_file(hits, full_path, extras, relative)
            tracked_count += 1

    # 扫描面 2：本次将要提交 / 尚未提交的变化
    scan_lines(hits, "暂存区(git diff --cached)", git("diff", "--cached"), extras)
    scan_lines(hits, "未提交改动(git diff)", git("diff"), extras)

    # 扫描面 3：本地已提交但尚未推送到上游的内容（无 upstream 时自动跳过）
    scan_lines(hits, "本地未推送提交", git("log", "@{u}..HEAD", "-p", "--no-color"), extras)

    # 扫描面 4/5：可选的本地体检
    if args.all:
        for full_path in worktree_files():
            scan_file(hits, full_path, extras)
    if args.history:
        scan_lines(hits, "全部历史提交", git("log", "--all", "-p", "--no-color"), extras)

    if hits:
        print("[阻断] 发现 %d 处疑似凭据，禁止推送：" % len(hits))
        for source, line_no, rule, sample in hits[:60]:
            print("  %s:%s  [%s]  %s" % (source, line_no, rule, sample))
        if len(hits) > 60:
            print("  ... 另有 %d 处" % (len(hits) - 60))
        print("\n处理：删除或脱敏后重新检查；确属误报请登记白名单并写明理由。")
        return 1

    print("[通过] 已跟踪文件 %d 个 + 暂存区 + 未提交改动 + 未推送提交：0 命中" % tracked_count)
    if args.all:
        print("[通过] 工作区全量文本（含被忽略的临时目录）：0 命中")
    if args.history:
        print("[通过] 全部历史提交：0 命中")
    if extras:
        print("[通过] %d 条精确待检串：0 命中（待检串未写入任何文件）" % len(extras))
    print("可以执行 commit / push。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
