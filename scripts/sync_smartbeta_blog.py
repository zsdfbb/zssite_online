#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_smartbeta_blog.py — 生成「指数信号 1. SmartBeta 日报」。

数据来源：FenglaiData 项目的本地只读库（`./fenglai query sql`）。

只算当期信号，不做回测。两条策略，各一只标的：

  红利低波 EMA169 V5 —— H20269（中证红利低波全收益）
      偏离比 = 收盘 ÷ EMA169
      ratio ≤ 1.00        → 加仓 120%
      1.00 ~ 1.10         → 满仓 100%
      1.10 ~ 1.12         → 半仓 50%
      ratio ≥ 1.12        → 空仓 0%

  中证现金流 MA180 —— 932365（中证全指自由现金流）
      偏离比 = 收盘 ÷ MA180
      ratio ≤ 0.90        → 加仓 120%
      0.90 ~ 1.12         → 满仓 100%
      ratio ≥ 1.12        → 空仓 0%

两只标的都在 `csindex_daily`（中证官网指数点位）里，所以每日只需更新这一张表
（约 10 秒），不再需要碰 `etf_nav` / `etf_kline`。

用法：
    python3 scripts/sync_smartbeta_blog.py             # 先更新数据，再写文章
    python3 scripts/sync_smartbeta_blog.py --push      # 再 git commit + push
    python3 scripts/sync_smartbeta_blog.py --no-update # 跳过增量，用库里现有数据
    python3 scripts/sync_smartbeta_blog.py --dry-run   # 只打印，不落盘（也不跑增量）

定时（cron，每交易日 08:05；与「指数信号 4 场内配置」同一套做法：
每篇文章各自一把 flock 锁，日志分开写）：
    5 8 * * 1-5 cd /home/zs/Develop/zssite_online && \\
        /usr/bin/flock -n /tmp/smartbeta.lock /usr/bin/python3 \\
        scripts/sync_smartbeta_blog.py --push >> logs/smartbeta.log 2>&1

数据口径：
    H20269 是 H30269 的**全收益**版本（含分红再投资），两者点位差一个数量级，
    不可互替。策略全年持有，股息捕获率高，所以必须用全收益序列；换成价格指数
    会把减仓机制废掉。

说明：
    - **数据先更新再取**：开跑前调 `./fenglai update --datasets csindex_daily`。
      更新失败（SDK 登不上 / 断网 / 限流）不终止，用库里现有数据出报告，并在
      文首挂数据陈旧的警告。
    - 输出与前端只依赖标准库，不装 pandas，也不需要 FenglaiData 的 .venv。
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
ZSSITE_ROOT = Path(__file__).resolve().parents[1]
DST = ZSSITE_ROOT / "content" / "posts" / "指数信号-1-smartbeta日报.md"

FENGLAI_ROOT = Path(
    os.environ.get("FENGLAI_DATA_ROOT", str(Path.home() / "Develop" / "FenglaiData"))
)
FENGLAI_CLI = FENGLAI_ROOT / "fenglai"
# 每日增量的超时（秒）。只更新一张表，实测 10 秒级，留足 SDK 重试与限流的余量
UPDATE_TIMEOUT = 600

# 策略参数（改这里就等于改规则）
RED_EMA_SPAN = 169
RED_ADD_MAX = 1.00       # 偏离比 ≤ 1.00 加仓
RED_ADD_WEIGHT = 1.20    # 加仓目标仓位
RED_FULL_MAX = 1.10      # 1.00~1.10 满仓
RED_HALF_MAX = 1.12      # 1.10~1.12 半仓，≥1.12 空仓

CF_MA_SPAN = 180
CF_ADD_MAX = 0.90
CF_ADD_WEIGHT = 1.20
CF_EMPTY_MIN = 1.12


@dataclass(frozen=True)
class Symbol:
    """一只标的：代码、名称、口径说明。"""

    code: str
    name: str
    caliber: str


RED = Symbol(
    "H20269.CSI",
    "中证红利低波全收益",
    "中证官网全收益指数点位（含分红再投资）",
)
CF = Symbol(
    "932365.CSI",
    "中证现金流",
    "中证官网全指自由现金流指数点位",
)
ALL_SYMBOLS = (RED, CF)

TABLE = "csindex_daily"


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------
def _sql() -> str:
    codes = ", ".join(f"'{s.code}'" for s in ALL_SYMBOLS)
    return (
        f"SELECT code, CAST(trade_date AS VARCHAR) AS d, close AS v "
        f"FROM {TABLE} WHERE code IN ({codes}) AND close IS NOT NULL "
        f"ORDER BY code, d"
    )


def update_data() -> None:
    """先跑 FenglaiData 的每日增量，把最新交易日数据拉进库再取数。

    失败不终止：SDK 登不上、限流、断网时继续跑，用库里已有的数据出报告，
    文章顶部会因数据过旧而挂上警告。
    """
    if not FENGLAI_CLI.exists():
        raise SystemExit(f"找不到取数入口：{FENGLAI_CLI}（用 FENGLAI_DATA_ROOT 指定）")
    cmd = [str(FENGLAI_CLI), "update", "--datasets", TABLE]
    print(f"[update] {' '.join(cmd[1:])} …", flush=True)
    try:
        proc = subprocess.run(
            cmd, cwd=FENGLAI_ROOT, capture_output=True, text=True, timeout=UPDATE_TIMEOUT
        )
    except subprocess.TimeoutExpired:
        print(f"[update] 超时（>{UPDATE_TIMEOUT}s），本次用库里现有数据", file=sys.stderr)
        return
    tail = (proc.stdout + proc.stderr).strip().splitlines()
    for line in tail[-6:]:
        print(f"[update] {line}")
    if proc.returncode != 0:
        print(
            f"[update] 失败（退出码 {proc.returncode}），本次用库里现有数据",
            file=sys.stderr,
        )


def fetch_series() -> dict[str, list[tuple[str, float]]]:
    """取两只标的的收盘序列，返回 {code: [(日期, 收盘), ...]}（按日期升序）。"""
    cmd = [str(FENGLAI_CLI), "query", "sql", _sql(), "--format", "csv", "--quiet"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if proc.returncode != 0:
        raise SystemExit(f"取数失败（退出码 {proc.returncode}）：{proc.stderr.strip()}")

    out: dict[str, list[tuple[str, float]]] = {}
    for row in csv.DictReader(io.StringIO(proc.stdout)):
        val = (row.get("v") or "").strip()
        if not val:
            continue
        out.setdefault(row["code"], []).append((row["d"], float(val)))
    for rows in out.values():
        rows.sort()

    missing = [f"{s.name}（{s.code}）" for s in ALL_SYMBOLS if not out.get(s.code)]
    if missing:
        raise SystemExit(
            f"取数结果里没有 {'、'.join(missing)}。\n"
            f"确认 FenglaiData 的 config/config.toml 收了这只代码，"
            f"并跑过 ./fenglai update 或 ./fenglai download --dataset {TABLE} --code ..."
        )
    return out


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------
def ema(values: list[float], span: int) -> list[float]:
    """指数移动平均，与 FenglaiIndex 口径一致：以首日为种子，平滑系数 2/(span+1)。"""
    k = 2.0 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def sma(values: list[float], span: int) -> list[float | None]:
    """简单移动平均；不足 `span` 个点时给 None（对应 pandas rolling 的默认行为）。"""
    out: list[float | None] = []
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= span:
            total -= values[i - span]
        out.append(total / span if i + 1 >= span else None)
    return out


def fmt_price(value: float) -> str:
    """点位：≥1000 显示整数，否则两位小数。"""
    return f"{value:.0f}" if value >= 1000 else f"{value:.2f}"


def pct(x: float, sign: bool = True) -> str:
    return f"{x * 100:+.2f}%" if sign else f"{abs(x) * 100:.2f}%"


def red_signal(rows: list[tuple[str, float]]) -> dict:
    """红利低波：EMA169 偏离比 → 档位与目标仓位。"""
    dates = [d for d, _ in rows]
    closes = [c for _, c in rows]
    line = ema(closes, RED_EMA_SPAN)
    ratio = closes[-1] / line[-1]

    if ratio >= RED_HALF_MAX:
        level, weight = "空仓", 0.0
    elif ratio >= RED_FULL_MAX:
        level, weight = "半仓", 0.50
    elif ratio <= RED_ADD_MAX:
        level, weight = "加仓", RED_ADD_WEIGHT
    else:
        level, weight = "满仓", 1.00

    return {
        "date": dates[-1],
        "start": dates[0],
        "close": closes[-1],
        "line": line[-1],
        "ratio": ratio,
        "level": level,
        "weight": weight,
        "n": len(closes),
    }


def cashflow_signal(rows: list[tuple[str, float]]) -> dict:
    """中证现金流：MA180 偏离比 → 档位与目标仓位。"""
    dates = [d for d, _ in rows]
    closes = [c for _, c in rows]
    line = sma(closes, CF_MA_SPAN)
    if line[-1] is None:
        raise SystemExit(
            f"中证现金流历史只有 {len(closes)} 个交易日，不足 MA{CF_MA_SPAN} 根，无法计算"
        )
    ratio = closes[-1] / line[-1]

    if ratio >= CF_EMPTY_MIN:
        level, weight = "空仓", 0.0
    elif ratio <= CF_ADD_MAX:
        level, weight = "加仓", CF_ADD_WEIGHT
    else:
        level, weight = "满仓", 1.00

    return {
        "date": dates[-1],
        "start": dates[0],
        "close": closes[-1],
        "line": line[-1],
        "ratio": ratio,
        "level": level,
        "weight": weight,
        "n": len(closes),
    }


# ---------------------------------------------------------------------------
# 排版
# ---------------------------------------------------------------------------
def _lag(d: str, now: datetime) -> int:
    return max(0, (now.date() - datetime.strptime(d, "%Y-%m-%d").date()).days)


def _warn_block(signals: dict[str, dict], now: datetime) -> str:
    """数据是否对齐 / 是否过期。每条序列各自算滞后，不能取 max()。"""
    lines: list[str] = []
    dates = {name: s["date"] for name, s in signals.items()}
    newest = max(dates.values())
    stale = {n: d for n, d in dates.items() if d != newest}
    if stale:
        detail = "、".join(
            f"{n} 到 {d}（滞后 {_lag(d, now)} 天）" for n, d in stale.items()
        )
        lines.append(f"两只标的数据末日不一致：{detail}，最新的是 {newest}。")
    for name, d in dates.items():
        lag = _lag(d, now)
        if lag > 4:
            lines.append(
                f"{name}的数据停在 {d}，距今 {lag} 天，对应信号可能已失效，"
                "先确认 FenglaiData 的 `./fenglai update` 是否跑过再照此执行。"
            )
    if not lines:
        return ""
    return "".join(f"> ⚠️ {ln}\n" for ln in lines) + "\n"


def _alert(level: str, name: str, ratio: float, weight: float) -> str:
    """只有加仓 / 减仓才提醒；满仓是常态，不喊。"""
    if weight > 1.0:
        return (
            f"> ⚠ **加仓提醒**：{name} 偏离比 {ratio:.4f} 已进入「{level}」档，"
            f"按规则加仓至 {weight * 100:.0f}%"
        )
    if weight < 1.0:
        return (
            f"> 🔻 **减仓提醒**：{name} 偏离比 {ratio:.4f} 已进入「{level}」档，"
            f"仓位降到 {weight * 100:.0f}%"
        )
    return ""


def build_body(series: dict[str, list[tuple[str, float]]], now: datetime) -> str:
    red = red_signal(series[RED.code])
    cf = cashflow_signal(series[CF.code])
    warn = _warn_block({RED.name: red, CF.name: cf}, now)

    lines: list[str] = []
    lines.append(f"> 更新于：{now.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    if warn:
        lines.append(warn.rstrip("\n"))
        lines.append("")

    # 一、今日信号
    lines.append("## 一、今日信号")
    lines.append("")
    lines.append("| 标的 | 数据截至 | 收盘 | 均线 | 偏离比 | 信号 | 目标仓位 |")
    lines.append("|:---|:---:|--:|--:|--:|:---|--:|")
    lines.append(
        f"| {RED.name} ({RED.code}) | {red['date']} | {fmt_price(red['close'])} | "
        f"EMA169 {fmt_price(red['line'])} | {red['ratio']:.4f} | "
        f"**{red['level']}** | {red['weight'] * 100:.0f}% |"
    )
    lines.append(
        f"| {CF.name} ({CF.code}) | {cf['date']} | {fmt_price(cf['close'])} | "
        f"MA180 {fmt_price(cf['line'])} | {cf['ratio']:.4f} | "
        f"**{cf['level']}** | {cf['weight'] * 100:.0f}% |"
    )
    lines.append("")
    alerts = [
        a
        for a in (
            _alert(red["level"], RED.name, red["ratio"], red["weight"]),
            _alert(cf["level"], CF.name, cf["ratio"], cf["weight"]),
        )
        if a
    ]
    if alerts:
        lines.extend(alerts)
        lines.append("")

    # 二、红利低波
    lines.append("## 二、红利低波（EMA169 V5）")
    lines.append("")
    lines.append(
        f"偏离比是收盘 ÷ EMA169：≤ {RED_ADD_MAX:.2f} 加仓 {RED_ADD_WEIGHT * 100:.0f}%，"
        f"{RED_ADD_MAX:.2f}~{RED_FULL_MAX:.2f} 满仓，"
        f"{RED_FULL_MAX:.2f}~{RED_HALF_MAX:.2f} 半仓，≥ {RED_HALF_MAX:.2f} 空仓。"
    )
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("|:---|--:|")
    lines.append(f"| 数据截至 | {red['date']} |")
    lines.append(f"| 收盘 | {fmt_price(red['close'])} |")
    lines.append(f"| EMA169 | {fmt_price(red['line'])} |")
    lines.append(f"| 偏离比 | **{red['ratio']:.4f}** |")
    lines.append(f"| 档位 | **{red['level']}**（{red['weight'] * 100:.0f}%） |")
    lines.append("")
    lines.append("| 动作 | 阈值 | 距当前 |")
    lines.append("|:---|--:|--:|")
    for th, word, label in (
        (RED_ADD_MAX, "跌", f"加仓 {RED_ADD_WEIGHT * 100:.0f}%"),
        (RED_FULL_MAX, "涨", "半仓"),
        (RED_HALF_MAX, "涨", "空仓"),
    ):
        lines.append(f"| {label} | {th:.2f} | {_distance(red['ratio'], th, word)} |")
    lines.append("")

    # 三、中证现金流
    lines.append(f"## 三、中证现金流（MA{CF_MA_SPAN}）")
    lines.append("")
    lines.append(
        f"偏离比是收盘 ÷ MA{CF_MA_SPAN}：≤ {CF_ADD_MAX:.2f} 加仓 "
        f"{CF_ADD_WEIGHT * 100:.0f}%，{CF_ADD_MAX:.2f}~{CF_EMPTY_MIN:.2f} 满仓，"
        f"≥ {CF_EMPTY_MIN:.2f} 空仓。"
    )
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("|:---|--:|")
    lines.append(f"| 数据截至 | {cf['date']} |")
    lines.append(f"| 收盘 | {fmt_price(cf['close'])} |")
    lines.append(f"| MA{CF_MA_SPAN} | {fmt_price(cf['line'])} |")
    lines.append(f"| 偏离比 | **{cf['ratio']:.4f}** |")
    lines.append(f"| 档位 | **{cf['level']}**（{cf['weight'] * 100:.0f}%） |")
    lines.append("")
    lines.append("| 动作 | 阈值 | 距当前 |")
    lines.append("|:---|--:|--:|")
    lines.append(
        f"| 加仓 {CF_ADD_WEIGHT * 100:.0f}% | {CF_ADD_MAX:.2f} | "
        f"{_distance(cf['ratio'], CF_ADD_MAX, '跌')} |"
    )
    lines.append(
        f"| 空仓 | {CF_EMPTY_MIN:.2f} | {_distance(cf['ratio'], CF_EMPTY_MIN, '涨')} |"
    )
    lines.append("")

    # 四、口径
    lines.append("## 四、口径与说明")
    lines.append("")
    lines.append("| 标的 | 取数表 | 序列 | 起点 | 样本数 |")
    lines.append("|:---|:---|:---|:---:|--:|")
    for sym, sig in ((RED, red), (CF, cf)):
        lines.append(
            f"| {sym.name} ({sym.code}) | {TABLE}.close | {sym.caliber} | "
            f"{sig['start']} | {sig['n']} |"
        )
    lines.append("")
    lines.append(
        "H20269 是 H30269 的**全收益**版本（含分红再投资），两者点位差一个数量级，"
        "不可互替。策略全年持有这只资产，股息捕获率高，所以必须用全收益序列；"
        "换成价格指数会把减仓机制废掉。"
    )
    lines.append("")
    lines.append(
        "本文由 `scripts/sync_smartbeta_blog.py` 基于 FenglaiData 的本地库自动生成，"
        "只算当期信号、不做回测。信号仅供参考，不构成投资建议。"
    )
    lines.append("")
    body = "\n".join(lines)
    while "\n\n\n" in body:
        body = body.replace("\n\n\n", "\n\n")
    return body.strip("\n") + "\n"


def _distance(ratio: float, threshold: float, direction: str) -> str:
    """到某个阈值的距离：`direction` 是触发该阈值需要的方向（涨 / 跌）。"""
    reached = ratio >= threshold if direction == "涨" else ratio <= threshold
    if reached:
        return "已触发 ✅"
    return f"还需{direction} {pct(threshold / ratio - 1, sign=False)}"


FRONT_MATTER = """---
title: "指数信号｜1. SmartBeta日报"
date: {date}
draft: false
tags: ["指数信号", "smartbeta", "调仓信号", "红利低波", "中证现金流"]
categories: ["指数信号"]
summary: "每交易日 08:05 由 cron 自动生成的 SmartBeta 策略信号日报：红利低波 EMA169 V5 与中证现金流 MA180 的当日偏离比、档位仓位建议及各道阈值的距离，数据取自 FenglaiData 本地库。"
comments: true
showToc: true
cover:
  image: ""
  alt: ""
  caption: ""
---
> 📡 自动同步: 每交易日 08:05 由 cron 触发 `scripts/sync_smartbeta_blog.py --push` 更新
> 数据来源: FenglaiData 本地只读库（生成前先跑 `./fenglai update --datasets csindex_daily`）
> 风险提示: 信号仅供参考，不构成投资建议。市场有风险，投资需谨慎。

---

"""


def render(series, now: datetime) -> str:
    return FRONT_MATTER.format(date=now.strftime("%Y-%m-%dT%H:%M:%S+08:00")) + build_body(
        series, now
    )


def strip_stamp(text: str) -> str:
    """去掉时间戳行后比较，用来判断数据到底有没有变。"""
    keep = [
        ln
        for ln in text.splitlines()
        if not ln.startswith("> 更新于：") and not ln.startswith("date:")
    ]
    return "\n".join(keep).strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成「指数信号 1. SmartBeta 日报」")
    ap.add_argument("--push", action="store_true", help="写完后 git add/commit/push")
    ap.add_argument("--dry-run", action="store_true", help="只打印到 stdout，不写文件")
    ap.add_argument(
        "--no-update",
        action="store_true",
        help="跳过 FenglaiData 的每日增量，直接用库里现有数据（调试 / 库里已是最新时用）",
    )
    args = ap.parse_args(argv)

    now = datetime.now()
    if not args.no_update and not args.dry_run:
        update_data()
    series = fetch_series()
    doc = render(series, now)

    if args.dry_run:
        sys.stdout.write(doc)
        return 0

    old = DST.read_text(encoding="utf-8") if DST.exists() else ""
    newest = max(rows[-1][0] for rows in series.values())
    if old and strip_stamp(old) == strip_stamp(doc):
        print(f"skip: 数据未变（最新交易日 {newest}），正文与现有文章一致")
        return 0

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(doc, encoding="utf-8")
    print(f"ok: 已写入 {DST}（数据截至 {newest}）")

    if not args.push:
        print("提示：加 --push 才会提交并推送（cron 里记得带上）")
        return 0

    def git(*a: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *a], cwd=ZSSITE_ROOT, capture_output=True, text=True)

    git("add", str(DST))
    status = git("status", "--porcelain", "--", str(DST))
    if not status.stdout.strip():
        print("skip: 没有需要提交的改动")
        return 0
    commit = git("commit", "-m", f"data: 更新 SmartBeta日报 {newest}")
    if commit.returncode != 0:
        print(commit.stdout + commit.stderr, file=sys.stderr)
        return 1
    push = git("push", "origin", "master")
    if push.returncode != 0:
        print(push.stdout + push.stderr, file=sys.stderr)
        return 1
    print(f"ok: 已提交并推送（数据截至 {newest}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
