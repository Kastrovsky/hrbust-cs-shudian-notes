#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""UserPromptSubmit hook：每条用户消息提交时，自动匹配已有技能并注入上下文。

输入（stdin，JSON）：{"prompt": "用户原话", "cwd": "...", ...}
输出（stdout，JSON）：
    命中 -> {"continue": true, "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": "动态检索出的技能匹配结果"}}
    未命中/异常 -> {"continue": true}，不注入任何内容

三条硬约束（写这个脚本时踩过的坑，别改）：
1. 永不阻塞：任何异常都吞掉并返回 continue=true。钩子挂全局，崩了会卡住用户发消息。
2. 无匹配就静默：闲聊、追问、"好的"、纯问答不注入噪音，否则用户会学会无视提示。
3. 快：直接 import 同目录的 match_skill，不起子进程、不联网、原话截断到 300 字。

配套配置在 ~/.workbuddy/settings.json 的 hooks.UserPromptSubmit。
"""

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# 放行（不注入）时的返回值
PASS = {"continue": True}

# 这些短句不是任务，注入技能提示纯属噪音
NOISE = {
    "好", "好的", "好吧", "ok", "okay", "yes", "嗯", "收到", "明白", "继续",
    "继续吧", "可以", "行", "谢谢", "辛苦了", "结束任务", "结束", "先这样",
    "换个话题", "不用了", "下一步", "然后呢", "为什么", "什么意思",
}


def emit(obj):
    """输出一行 JSON；hook 框架靠 stdout 解析，不能夹带 print 调试信息。"""
    try:
        sys.stdout.write(json.dumps(obj, ensure_ascii=False))
        sys.stdout.write("\n")
        sys.stdout.flush()
    except Exception:
        # stdout 都坏了也不能抛异常阻塞输入
        sys.stdout.write('{"continue":true}\n')
        sys.stdout.flush()


TRACE = os.path.join(HERE, "..", "references", "hook-trace.log")


def trace(prompt, verdict):
    """往 references/hook-trace.log 追加一行留痕，超过 250 行时压缩到 200 行。

    为什么要留痕：这个 hook 是否真被框架调用，从对话里看不出来——
    注入块可能被上下文压缩吃掉，模型没看到就等于没跑。
    有了这份日志才能区分"钩子没触发"和"触发了但我没照做"，
    否则每次出问题都只能靠猜。写失败一律静默，绝不阻塞用户发消息。

    写入方式有讲究（踩过两次）：
    1. 常规路径必须用 "a" 真追加。之前是"读全文→改→整体写回"，
       两个会话同时触发 hook 就互相覆盖，甚至留下 0 字节文件把日志清光。
    2. 压缩才需要整体重写，且写同目录临时文件再 os.replace——
       要么旧内容要么新内容，不会出现写一半的空文件。
    """
    try:
        import time

        stamp = time.strftime("%m-%d %H:%M:%S")
        line = "{}  {:<10}  {}".format(stamp, verdict, prompt[:60].replace("\n", " "))
        path = os.path.abspath(TRACE)
        os.makedirs(os.path.dirname(path), exist_ok=True)

        with open(path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

        with open(path, encoding="utf-8", errors="ignore") as fh:
            lines = fh.read().splitlines()
        if len(lines) > 250:
            tmp = path + ".writing"
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines[-200:]) + "\n")
            os.replace(tmp, path)
    except Exception:
        pass


def clean(prompt):
    """剥掉系统注入的标签与多余的空白，避免把上下文里的词当成用户意图。"""
    prompt = re.sub(r"<[^>]+>", " ", prompt)
    prompt = re.sub(r"```.*?```", " ", prompt, flags=re.S)
    return re.sub(r"\s+", " ", prompt).strip()


def match(prompt):
    """返回 [(skill, score, hits, note)]；任何内部错误都当无匹配处理。"""
    try:
        import match_skill as m

        routes = m.parse_routes()
        if not routes:
            return []
        triggers = m.parse_triggers()
        for r in routes:
            if r["skill"] in triggers:
                r["kw"] = triggers[r["skill"]]
        return m.score(prompt[:300], routes, m.load_desc())
    except Exception:
        return []


def build_context(rows, threshold):
    """把匹配结果压成给模型看的短提示；只给前 1~2 个候选，别刷屏。"""
    top = rows[0]
    skill, sc, hits, note = top
    hit_text = "、".join(h for h in hits[:5] if not h.startswith("（")) or "语义相似"

    lines = [
        "<skill-match 任务前自动技能路由>",
        "本条消息已在任务开始前自动检索技能库（skill-router）：命中 `{}`（得分 {}，命中词：{}）。".format(
            skill, sc, hit_text
        ),
        "动作：先用 Skill 工具加载 `{}`，然后严格按它的工作流执行（含校验脚本、命名规范、提交方式）；加载后一句话报备用了哪套流程。".format(skill),
    ]
    if note:
        lines.append("注意：{}".format(note.strip()[:100]))
    alts = [r for r in rows[1:3] if r[1] >= sc * 0.7]
    if alts:
        lines.append("若任务跨两类，可考虑串联：{}。".format(
            "、".join("`{}`".format(r[0]) for r in alts)))
    lines.append("若判断这是纯问答/闲聊、确实不该套技能，可忽略本提示并说明一句原因。")
    lines.append("</skill-match>")
    return "\n".join(lines)


def main():
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw and raw.strip() else {}
    except Exception:
        emit(PASS)
        return 0

    prompt = clean(str(data.get("prompt") or ""))
    if len(prompt) < 6 or prompt.lower() in NOISE:
        trace(prompt, "跳过")
        emit(PASS)
        return 0

    rows = match(prompt)
    try:
        import match_skill as m

        threshold = m.THRESHOLD
    except Exception:
        threshold = 4

    if not rows or rows[0][1] < threshold:
        trace(prompt, "无匹配")
        emit(PASS)
        return 0

    trace(prompt, "%s %d" % (rows[0][0], rows[0][1]))
    emit({
        "continue": True,
        "suppressOutput": True,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": build_context(rows, threshold),
        },
    })
    return 0


if __name__ == "__main__":
    sys.exit(main())
