#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""技能库自检节律器（selftick）。

作用
----
把"我该在什么时候主动检查技能库"这件事**落到磁盘状态上**，而不是靠临场记性。
三个时钟对应三个动作：

    T1 任务完成      -> tick()   每完成一个任务累加一次，满 N 次提醒做模式扫描
    T2 轮次累积      -> tick()   返回值 need_pattern_scan=True 时做"同类型问题 ≥2 次"的扫描
    T3 定期巡检      -> status() 距上次巡检超过 DAYS 天就提醒；audit() 真正执行巡检

设计约束
--------
1. **零依赖**：只用标准库，任何 python3 都能跑，不需要装包。
2. **幂等**：重复跑 status 不改变状态；只有 tick / audit 写盘。
3. **不阻塞**：status 只打印一两行，供我在回复里附一句；从不交互、从不等待输入。
4. **可解释**：每行输出都说清"为什么现在要做这件事"，否则下次看到也判断不了。

用法
----
    python selftick.py --status           # 会话第一个任务前看一眼
    python selftick.py --tick             # 每完成一个任务调一次
    python selftick.py --audit            # 该巡检时执行（跑校验 + 刷新索引）
    python selftick.py --reset            # 手动清零（调试用）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import date, datetime

# ---------- 常量：改写这里就能调节律，不必动逻辑 ----------
DAYS = 7          # 距上次巡检超过这么多天 -> 提醒巡检
TICK_BATCH = 5    # 每完成这么多个任务 -> 提醒做一次模式扫描

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                     # skill-router 目录
STATE = os.path.join(ROOT, ".state.json")


def _today() -> str:
    """返回今天日期字符串（本地时区），用于和状态文件比对。"""
    return date.today().isoformat()


def load_state() -> dict:
    """读取状态文件；不存在或损坏时返回一份干净的默认状态。"""
    default = {
        "last_audit": None,      # 上次巡检日期 YYYY-MM-DD
        "audit_count": 0,        # 累计巡检次数
        "tick_since_scan": 0,    # 距上次模式扫描完成的任务数
        "total_tick": 0,         # 累计任务数
        "updated": None,
    }
    if not os.path.exists(STATE):
        return default
    try:
        with open(STATE, "r", encoding="utf-8") as f:
            data = json.load(f)
        default.update({k: v for k, v in data.items() if k in default})
    except (json.JSONDecodeError, OSError):
        # 状态坏了不报错、直接重置：这只是个提醒器，不该阻塞任务
        pass
    return default


def save_state(st: dict) -> None:
    """写回状态文件，时间戳自动更新。"""
    st["updated"] = datetime.now().isoformat(timespec="seconds")
    with open(STATE, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)


def days_since(last: str | None) -> int | None:
    """距某日期过了多少天；从未巡检过返回 None。"""
    if not last:
        return None
    try:
        return (date.today() - date.fromisoformat(last)).days
    except ValueError:
        return None


def cmd_status(st: dict) -> int:
    """打印当前节律状态，并给出唯一一条建议动作。"""
    d = days_since(st["last_audit"])
    left = TICK_BATCH - st["tick_since_scan"]

    if d is None:
        verdict = "从未巡检过 -> 建议现在跑 --audit"
    elif d > DAYS:
        verdict = f"距上次巡检 {d} 天（阈值 {DAYS}）-> 该跑 --audit"
    elif left <= 0:
        verdict = f"已连续完成 {st['tick_since_scan']} 个任务 -> 该做一次模式扫描"
    else:
        verdict = f"正常（巡检 {d} 天前；再做 {left} 个任务后模式扫描）"

    print(f"selftick: {verdict}")
    print(
        f"  last_audit={st['last_audit'] or '从未'}  "
        f"audit_count={st['audit_count']}  "
        f"tick_since_scan={st['tick_since_scan']}/{TICK_BATCH}  "
        f"total_tick={st['total_tick']}"
    )
    return 0


def cmd_tick(st: dict) -> int:
    """任务完成时累加；到达批量阈值就要求做模式扫描（扫描完用 --scanned 清零）。"""
    st["tick_since_scan"] += 1
    st["total_tick"] += 1
    scan = st["tick_since_scan"] >= TICK_BATCH
    save_state(st)

    if scan:
        print(
            f"selftick: 已累计 {st['tick_since_scan']} 个任务 -> 现在做一次模式扫描"
            "（查同类型修改 ≥2 次、同一纠正 ≥2 次的规律），扫完跑 --scanned"
        )
    else:
        print(f"selftick: 第 {st['total_tick']} 个任务已记录（{st['tick_since_scan']}/{TICK_BATCH}）")
    return 0


def cmd_scanned(st: dict) -> int:
    """模式扫描完成后清零计数器，避免反复提醒。"""
    st["tick_since_scan"] = 0
    save_state(st)
    print("selftick: 模式扫描已归档，计数器清零")
    return 0


def _run(cmd: list[str]) -> tuple[int, str]:
    """跑子进程并回收输出；失败不抛异常，交给调用方决定怎么报告。"""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", timeout=120)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except (OSError, subprocess.TimeoutExpired) as e:
        return 1, f"{type(e).__name__}: {e}"


def cmd_audit(st: dict) -> int:
    """执行一次巡检：完整性校验 + 索引刷新 + 匹配回归 + 技能重叠，结果压成几行。

    第三项（回归测试）是必需项：改路由表或触发词最容易悄悄改坏已有判定，
    而这种坏法在文件层面看不出来，只有跑用例才暴露。
    第四项（重叠检查）防的是另一件事：技能越建越多，两个技能覆盖同一批触发词，
    之后每轮都要临时仲裁。这里只告警不失败——重叠要人判，但必须被看见。
    """
    py = sys.executable
    print(f"selftick: 开始巡检（{_today()}）")

    rc1, out1 = _run([py, os.path.join(HERE, "quick_validate.py")])
    tail1 = [ln for ln in out1.strip().splitlines() if ln.strip()][-3:]
    print(f"  [1/4] quick_validate  rc={rc1}")
    for ln in tail1:
        print(f"        {ln}")

    rc2, out2 = _run([py, os.path.join(HERE, "refresh_index.py")])
    tail2 = [ln for ln in out2.strip().splitlines() if ln.strip()][-3:]
    print(f"  [2/4] refresh_index   rc={rc2}")
    for ln in tail2:
        print(f"        {ln}")

    rc3, out3 = _run([py, os.path.join(HERE, "match_skill.py"), "--selftest"])
    tail3 = [ln for ln in out3.strip().splitlines() if ln.strip()][-3:]
    print(f"  [3/4] match_skill --selftest  rc={rc3}")
    for ln in tail3:
        print(f"        {ln}")

    rc4, out4 = _run([py, os.path.join(HERE, "overlap_check.py"), "all"])
    tail4 = [ln for ln in out4.strip().splitlines() if ln.strip()][-4:]
    print(f"  [4/4] overlap_check   rc={rc4}（重叠只告警，不判失败）")
    for ln in tail4:
        print(f"        {ln}")

    st["last_audit"] = _today()
    st["audit_count"] += 1
    save_state(st)

    ok = rc1 == 0 and rc2 == 0 and rc3 == 0
    print(f"selftick: 巡检{'通过' if ok else '有问题，看上面'}；下次提醒在 {DAYS} 天后")
    return 0 if ok else 1


def cmd_reset(st: dict) -> int:
    """清零全部状态（调试或换机器时用）。"""
    save_state(
        {
            "last_audit": None,
            "audit_count": 0,
            "tick_since_scan": 0,
            "total_tick": 0,
            "updated": None,
        }
    )
    print("selftick: 状态已清零")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="技能库自检节律器")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true", help="看状态与建议动作")
    g.add_argument("--tick", action="store_true", help="任务完成 +1")
    g.add_argument("--scanned", action="store_true", help="模式扫描完成，计数器清零")
    g.add_argument("--audit", action="store_true", help="执行巡检")
    g.add_argument("--reset", action="store_true", help="清零状态")
    a = ap.parse_args()

    st = load_state()
    if a.status:
        return cmd_status(st)
    if a.tick:
        return cmd_tick(st)
    if a.scanned:
        return cmd_scanned(st)
    if a.audit:
        return cmd_audit(st)
    return cmd_reset(st)


if __name__ == "__main__":
    sys.exit(main())
