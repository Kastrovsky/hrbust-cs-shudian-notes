#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""幂等安装 UserPromptSubmit 技能匹配钩子到 ~/.workbuddy/settings.json。

用法：
    python install_hook.py            # 安装/确认已安装
    python install_hook.py --check    # 只检查，不写

为什么要单独一个脚本：
    settings.json 是产品级配置，含加密字段，手改容易写坏。这个脚本先备份、
    再 json 解析后合并（不覆盖已有 hooks）、校验通过才写回；重复运行不会装两遍。
    换机器或 WorkBuddy 升级重置了配置时，重跑一次即可。
"""

import json
import os
import shutil
import sys
from datetime import date

SETTINGS = os.path.join(os.path.expanduser("~"), ".workbuddy", "settings.json")

# 路径全部从本文件位置反推：换机器、换目录、换解释器都不用改代码
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # …/skill-router
ON_PROMPT = os.path.join(SKILL_DIR, "scripts", "on_prompt.py")
BACKUP_DIR = os.path.join(SKILL_DIR, ".backups")
HOOK_CMD = '"%s" "%s"' % (sys.executable or "python", ON_PROMPT)
ENTRY = {"type": "command", "command": HOOK_CMD, "timeout": 10}


def installed(hooks):
    """只要 UserPromptSubmit 里已有指向 on_prompt.py 的 command 就算装好了。"""
    for group in hooks.get("UserPromptSubmit", []):
        if not isinstance(group, dict):
            continue
        for h in group.get("hooks", []):
            if isinstance(h, dict) and "on_prompt.py" in str(h.get("command", "")):
                return True
    return False


def main():
    check_only = "--check" in sys.argv[1:]

    if not os.path.isfile(SETTINGS):
        print("FAIL 找不到配置文件：", SETTINGS)
        return 1
    with open(SETTINGS, encoding="utf-8") as fh:
        raw = fh.read()
    try:
        data = json.loads(raw)
    except ValueError as exc:
        print("FAIL settings.json 不是合法 JSON，未做任何修改：", exc)
        return 1

    # 防呆：关键字段必须还在，避免写回一个被误读的空壳
    for key in ("enabledPlugins", "sandbox"):
        if key not in data:
            print("FAIL 解析结果缺少关键字段 %s，未做任何修改" % key)
            return 1

    hooks = data.setdefault("hooks", {})
    if installed(hooks):
        print("OK 已安装，无需改动：", HOOK_CMD)
        return 0
    if check_only:
        print("未安装（仅检查模式，未写入）")
        return 0

    ups = hooks.setdefault("UserPromptSubmit", [])
    if ups and isinstance(ups[0], dict) and isinstance(ups[0].get("hooks"), list):
        # 已有别人的 UserPromptSubmit 配置：追加，不覆盖
        ups[0]["hooks"].append(ENTRY)
    else:
        ups.append({"hooks": [ENTRY]})

    out = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    json.loads(out)  # 写回前再校验一次

    os.makedirs(BACKUP_DIR, exist_ok=True)
    backup = os.path.join(
        BACKUP_DIR, "settings.json.bak-" + date.today().strftime("%Y%m%d")
    )
    shutil.copy2(SETTINGS, backup)

    with open(SETTINGS, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(out)

    # 落盘后复读确认
    with open(SETTINGS, encoding="utf-8") as fh:
        again = json.load(fh)
    if not installed(again.get("hooks", {})):
        print("FAIL 写回后校验不通过，请用备份还原：", backup)
        return 1

    print("OK 已安装 UserPromptSubmit 钩子：", HOOK_CMD)
    print("   备份：", backup)
    print("   需完全退出并重启 WorkBuddy 才会生效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
