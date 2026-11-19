#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sync_index_signal_blog.py — 按《投资策略 9 场内配置》每天生成「指数信号 4」日报。

策略来源：content/posts/投资策略-9-场内配置.md
数据来源：FenglaiData 项目的本地只读库（./fenglai query sql）

只算当期信号，不做回测：

  规则一 沪深300 上场条件
      收盘价 > EMA200  且  EMA200 近 20 个交易日斜率 > 0  → 拿 30%
      否则                                              → 清空 0%

  规则二 红利低波偏离比（收盘 ÷ EMA169）
      < 1.10        → 满仓
      1.10 ~ 1.12   → 减掉一半
      ≥ 1.12        → 全部卖掉
      建仓档位：< 1.00 投 1/3，< 0.98 投 1/3，< 0.96 投 1/3

用法：
    python3 scripts/sync_index_signal_blog.py             # 先更新数据，再写文章
    python3 scripts/sync_index_signal_blog.py --push      # 再 git commit + push
    python3 scripts/sync_index_signal_blog.py --no-update # 跳过增量，用库里现有数据
    python3 scripts/sync_index_signal_blog.py --dry-run   # 只打印，不落盘（也不跑增量）

定时（每交易日 18:20，收盘数据发布后用 cron）：
    20 18 * * 1-5 cd /home/zs/Develop/zssite_online && \\
        /usr/bin/python3 scripts/sync_index_signal_blog.py --push >> logs/index-signal.log 2>&1

说明：
    - **数据先更新再取**：开跑前调 `./fenglai update --datasets index_daily csindex_daily`
      （约 1 分钟，只拉信号用到的两张行情表）。更新失败（SDK 登不上 / 断网 / 限流）
      不终止，用库里现有数据出报告，并在文首挂数据陈旧的警告。
    - 输出与前端只依赖标准库，不装 pandas，也不需要 FenglaiData 的 .venv。
    - 数据没换（周末、节假日、库里还没更新）时正文逐字相同，脚本直接跳过，不会产生空提交。
    - 红利低波用中证红利低波动全收益指数 H20269.CSI（含分红再投资，策略 9 原文口径）。
"""

from __future__ import annotations

import argparse
import csv
import io
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# 路径与常量
# ---------------------------------------------------------------------------
ZSSITE_ROOT = Path(__file__).resolve().parents[1]
DST = ZSSITE_ROOT / "content" / "posts" / "指数信号-4-场内配置信号日报.md"

FENGLAI_ROOT = Path(
    os.environ.get("FENGLAI_DATA_ROOT", str(Path.home() / "Develop" / "FenglaiData"))
)
FENGLAI_CLI = FENGLAI_ROOT / "fenglai"
# 每日增量的超时（秒）。实测两张表约 1 分钟，留出 SDK 重试与限流的余量
UPDATE_TIMEOUT = 900

# 沪深300：交易所指数，在 index_daily 表
HS300 = "000300.SH"
# 红利低波：中证红利低波动全收益指数（含分红再投资），策略 9 原文用的就是这条
RED_MAIN = "H20269.CSI"

# 策略参数（与投资策略 9 一一对应，改这里就等于改规则）
HS_EMA_SPAN = 200          # 沪深300 趋势均线
HS_SLOPE_DAYS = 20         # EMA200 斜率回看交易日
RED_EMA_SPAN = 169         # 红利低波偏离比均线
RED_FULL_MAX = 1.10        # 偏离比 1.10 以下满仓
RED_EMPTY_MIN = 1.12       # 偏离比 1.12 以上清空
RED_ENTRY_STEPS = [(1.00, "第一批 1/3"), (0.98, "第二批 1/3"), (0.96, "第三批 1/3")]
RED_BASE_WEIGHT = 0.80     # 红利低波 + 沪深300 恒等于 80%
HS_MAX_WEIGHT = 0.30       # 沪深300 上场时的目标仓位
STOCK_WEIGHT = 0.10        # 个股自留地

# 偏离比的统计窗口：策略 9 的均值/标准差是按 2016 年以来的 10 年数据算的
STAT_START = "2016-01-01"


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------
SQL = """
SELECT code, CAST(trade_date AS VARCHAR) AS d, close FROM index_daily WHERE code = '000300.SH'
UNION ALL
SELECT code, CAST(trade_date AS VARCHAR), close FROM csindex_daily
 WHERE code = 'H20269.CSI'
ORDER BY code, d
"""


def update_data() -> None:
    """先跑 FenglaiData 的每日增量，把最新交易日数据拉进库再取数。

    只更新信号用得到的两个行情表（index_daily 覆盖沪深300，csindex_daily 覆盖红利低波
    全收益 H20269），不为这两条均线去拉全市场 5000 多只股票。

    失败不终止：SDK 登不上、限流、断网时继续跑，用库里已有的数据出报告，
    文章顶部会因数据过旧而挂上警告。
    """
    if not FENGLAI_CLI.exists():
        raise SystemExit(f"找不到取数入口：{FENGLAI_CLI}（用 FENGLAI_DATA_ROOT 指定）")
    cmd = [
        str(FENGLAI_CLI), "update",
        "--datasets", "index_daily", "csindex_daily",
    ]
    print(f"[update] {' '.join(cmd[1:])} …（约 1 分钟）", flush=True)
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


def fetch_closes() -> dict[str, list[tuple[str, float]]]:
    """从 FenglaiData 只读库里取收盘价，返回 {code: [(YYYY-MM-DD, close), ...]}。"""
    cmd = [str(FENGLAI_CLI), "query", "sql", SQL, "--format", "csv", "--quiet"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise SystemExit(f"取数失败（退出码 {proc.returncode}）：{proc.stderr.strip()}")

    out: dict[str, list[tuple[str, float]]] = {}
    for row in csv.DictReader(io.StringIO(proc.stdout)):
        close = row.get("close") or ""
        if not close:
            continue
        out.setdefault(row["code"], []).append((row["d"], float(close)))
    if HS300 not in out:
        raise SystemExit("取数结果缺少沪深300（000300.SH），检查库是否建好")
    if RED_MAIN not in out:
        raise SystemExit(
            f"取数结果里没有 {RED_MAIN}（中证红利低波动全收益）。\n"
            "确认 FenglaiData 的 config/config.toml 的 [csi_index] 含 H20269，"
            "并跑过 ./fenglai update --datasets csindex_daily"
        )
    for rows in out.values():
        rows.sort()
    return out


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------
def ema(values: list[float], span: int) -> list[float]:
    """指数移动平均，与策略 9 的口径一致：以首日为种子，平滑系数 2/(span+1)。"""
    k = 2.0 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def pct(x: float, sign: bool = True) -> str:
    return f"{x * 100:+.2f}%" if sign else f"{abs(x) * 100:.2f}%"


def hs300_signal(rows: list[tuple[str, float]]) -> dict:
    dates = [d for d, _ in rows]
    closes = [c for _, c in rows]
    line = ema(closes, HS_EMA_SPAN)
    slope = line[-1] - line[-1 - HS_SLOPE_DAYS]
    above = closes[-1] > line[-1]
    hold = above and slope > 0
    return {
        "date": dates[-1],
        "close": closes[-1],
        "ema": line[-1],
        "gap": closes[-1] / line[-1] - 1,
        "slope": slope,
        "slope_date": dates[-1 - HS_SLOPE_DAYS],
        "above": above,
        "hold": hold,
        "weight": HS_MAX_WEIGHT if hold else 0.0,
    }


def red_signal(rows: list[tuple[str, float]]) -> dict:
    dates = [d for d, _ in rows]
    closes = [c for _, c in rows]
    line = ema(closes, RED_EMA_SPAN)
    ratios = [c / e for c, e in zip(closes, line)]
    ratio = ratios[-1]

    window = [r for d, r in zip(dates, ratios) if d >= STAT_START] or ratios
    mean = sum(window) / len(window)
    var = sum((r - mean) ** 2 for r in window) / max(len(window) - 1, 1)
    std = var ** 0.5

    if ratio >= RED_EMPTY_MIN:
        level, signal = "清空", "全部卖出，钱进现金"
        weight = 0.0
    elif ratio >= RED_FULL_MAX:
        level, signal = "减半", "减掉一半，一半转现金"
        weight = 0.5
    else:
        level, signal = "满仓", "持有不动"
        weight = 1.0

    return {
        "date": dates[-1],
        "close": closes[-1],
        "ema": line[-1],
        "ratio": ratio,
        "mean": mean,
        "std": std,
        "z": (ratio - mean) / std if std else 0.0,
        "level": level,
        "signal": signal,
        "weight": weight,
    }


def entry_gate(ratio: float) -> list[tuple[str, float, bool]]:
    """建仓三档：返回 [(档位名, 阈值, 是否已触发), ...]。"""
    return [(name, th, ratio < th) for th, name in RED_ENTRY_STEPS]


# ---------------------------------------------------------------------------
# 排版
# ---------------------------------------------------------------------------
def build_body(closes: dict[str, list[tuple[str, float]]], now: datetime) -> str:
    hs = hs300_signal(closes[HS300])
    red = red_signal(closes[RED_MAIN])

    # 仓位换算：红利低波与沪深300 合计恒等于 80%，红利低波减仓/清空掉的部分转现金
    hs_weight = hs["weight"]
    red_base = RED_BASE_WEIGHT - hs_weight
    if red["weight"] >= 1.0:
        red_weight = red_base
    elif red["weight"] > 0.0:
        red_weight = red_base * red["weight"]
    else:
        red_weight = 0.0
    cash_weight = max(0.0, 1.0 - red_weight - hs_weight - STOCK_WEIGHT)

    # 数据是否对齐 / 是否过期
    last_date = max(hs["date"], red["date"])
    fresh_days = (now.date() - datetime.strptime(last_date, "%Y-%m-%d").date()).days
    warnings: list[str] = []
    if hs["date"] != red["date"]:
        warnings.append(
            f"两条序列末日不一致：沪深300 到 {hs['date']}，红利低波到 {red['date']}，"
            "以各自最新交易日为准。"
        )
    if fresh_days > 4:
        warnings.append(
            f"最新数据停在 {last_date}，距今 {fresh_days} 天，"
            "先确认 FenglaiData 的 `./fenglai update` 是否跑过再照此执行。"
        )
    warn_block = "".join(f"> ⚠️ {w}\n" for w in warnings)
    warn_block = f"{warn_block}\n" if warn_block else ""

    hs_verdict = "持有 30%" if hs["hold"] else "清空 0%"
    lines: list[str] = []
    lines.append(f"> 更新于：{now.strftime('%Y-%m-%d %H:%M')}")
    lines.append("")
    lines.append(warn_block.rstrip("\n") if warn_block else "")
    if warn_block:
        lines.append("")
    lines.append("## 一、今日信号")
    lines.append("")
    lines.append("| 标的 | 数据截至 | 收盘 | 均线 | 状态 | 信号 | 目标仓位 |")
    lines.append("|:---|:---:|--:|--:|:---|:---|--:|")
    lines.append(
        f"| 沪深300 | {hs['date']} | {hs['close']:.2f} | EMA200 {hs['ema']:.2f} | "
        f"{'均线上方' if hs['above'] else '均线下方'} {pct(hs['gap'])}，"
        f"20 日斜率 {hs['slope']:+.2f}（{'上行' if hs['slope'] > 0 else '下行'}） | "
        f"**{hs_verdict}** | {hs_weight * 100:.0f}% |"
    )
    lines.append(
        f"| 红利低波 | {red['date']} | {red['close']:.2f} | EMA169 {red['ema']:.2f} | "
        f"偏离比 {red['ratio']:.4f}（{red['z']:+.2f} 个标准差） | "
        f"**{red['level']}** | {red_weight * 100:.0f}% |"
    )
    lines.append("")
    lines.append("## 二、沪深300：上不上场")
    lines.append("")
    lines.append("条件是收盘价站上 EMA200、且 EMA200 近 20 个交易日在往上走，两条都成立才拿 30%。")
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("|:---|--:|")
    lines.append(f"| 收盘价 | {hs['close']:.2f}（{hs['date']}） |")
    lines.append(f"| EMA200 | {hs['ema']:.2f} |")
    lines.append(f"| 距均线 | {pct(hs['gap'])} |")
    lines.append(
        f"| 二十日斜率 | {hs['slope']:+.2f}（{hs['slope_date']} → {hs['date']}） |"
    )
    lines.append(f"| 站上均线 | {'✅ 成立' if hs['above'] else '❌ 不成立'} |")
    lines.append(f"| 斜率向上 | {'✅ 成立' if hs['slope'] > 0 else '❌ 不成立'} |")
    lines.append(f"| **结论** | **{hs_verdict}** |")
    lines.append("")
    lines.append("## 三、红利低波：偏离比在哪一档")
    lines.append("")
    lines.append("偏离比是收盘价 ÷ EMA169：低于 1.10 满仓，1.10~1.12 减半，1.12 以上清空。")
    lines.append("")
    lines.append("| 项目 | 数值 |")
    lines.append("|:---|--:|")
    lines.append(f"| 收盘价 | {red['close']:.2f}（{red['date']}） |")
    lines.append(f"| EMA169 | {red['ema']:.2f} |")
    lines.append(f"| 偏离比 | **{red['ratio']:.4f}** |")
    lines.append(f"| 2016 年以来均值 / 标准差 | {red['mean']:.4f} / {red['std']:.4f} |")
    lines.append(f"| 距均值 | {red['z']:+.2f} 个标准差 |")
    lines.append(f"| **当前档位** | **{red['level']}**（{red['signal']}） |")
    lines.append("")
    lines.append("| 动作 | 阈值 | 距当前 |")
    lines.append("|:---|--:|--:|")
    lines.append(f"| 清空 | {RED_EMPTY_MIN:.2f} | 还需涨 {pct(RED_EMPTY_MIN / red['ratio'] - 1, sign=False)} |")
    lines.append(f"| 减半 | {RED_FULL_MAX:.2f} | 还需涨 {pct(RED_FULL_MAX / red['ratio'] - 1, sign=False)} |")
    for name, th, hit in entry_gate(red["ratio"]):
        if hit:
            dist = "已触发 ✅"
        else:
            dist = f"还需跌 {pct(th / red['ratio'] - 1, sign=False)}"
        lines.append(f"| 建仓{name} | {th:.2f} | {dist} |")
    lines.append("")
    lines.append("## 四、目标配置")
    lines.append("")
    lines.append("| 放在哪 | 目标仓位 | 依据 |")
    lines.append("|:---|--:|:---|")
    lines.append(f"| 红利低波 | {red_weight * 100:.0f}% | 偏离比 {red['ratio']:.4f} → {red['level']} |")
    lines.append(
        f"| 沪深300 | {hs_weight * 100:.0f}% | "
        f"{'站上均线且斜率向上' if hs['hold'] else '未同时满足两条条件'} |"
    )
    lines.append(f"| 红利个股 | {STOCK_WEIGHT * 100:.0f}% | 自留地，单只不超过 5% |")
    lines.append(f"| 现金 | {cash_weight * 100:.0f}% | 减仓与未建仓的钱 |")
    lines.append("")
    if red_weight == 0.0:
        lines.append(
            f"> 红利低波已清空：{RED_BASE_WEIGHT * 100:.0f}% 底仓转现金，现金升到 {cash_weight * 100:.0f}%。"
            "下一轮等偏离比跌破 1.00 再分三批建仓。"
        )
    elif red["weight"] == 0.5:
        lines.append(
            f"> 红利低波已减半：{(RED_BASE_WEIGHT - hs_weight) * 50:.0f} 个百分点转现金。"
        )
    elif red["ratio"] < 1.00:
        triggered = [name for name, _th, hit in entry_gate(red["ratio"]) if hit]
        lines.append(
            f"> 处于建仓期：已到位「{'、'.join(triggered)}」，没投的部分放债基里等。"
        )
    else:
        lines.append(
            f"> 红利低波满仓、沪深300 {hs_verdict}，今天不需要任何动作。"
        )
    lines.append("")
    lines.append("## 五、口径")
    lines.append("")
    lines.append("| 用途 | 代码 | 表 | 起点 | 说明 |")
    lines.append("|:---|:---|:---|:---:|:---|")
    lines.append(
        f"| 沪深300 | {HS300} | index_daily | {closes[HS300][0][0]} | 价格指数，不复权 |"
    )
    lines.append(
        f"| 红利低波 | {RED_MAIN} | csindex_daily | {closes[RED_MAIN][0][0]} | 全收益，含分红再投资 |"
    )
    lines.append("")
    lines.append(
        "红利低波必须用全收益序列：分红除权会把价格抹掉一截，换成价格指数或 ETF 实盘价，"
        "偏离比会系统性偏低。"
    )
    lines.append("")
    lines.append(
        "本文由 `scripts/sync_index_signal_blog.py` 按《投资策略 9 场内配置》的公式自动生成，"
        "不含回测。信号仅供参考，不构成投资建议。"
    )
    lines.append("")
    body = "\n".join(lines)
    # 去掉可能的连续空行（排版洁癖）
    while "\n\n\n" in body:
        body = body.replace("\n\n\n", "\n\n")
    return body.strip("\n") + "\n"


FRONT_MATTER = """---
title: "指数信号｜4. 场内配置信号日报"
date: {date}
draft: false
tags: ["指数信号", "场内配置", "红利低波", "沪深300", "EMA169", "EMA200"]
categories: ["指数信号"]
summary: "按《投资策略 9 场内配置》每天计算一次沪深300 的 EMA200 趋势开关与红利低波的 EMA169 偏离比档位，给出目标仓位与五道阈值的距离。"
comments: true
showToc: true
cover:
  image: ""
  alt: ""
  caption: ""
---
> 📡 自动同步: 每天由 cron 触发 `scripts/sync_index_signal_blog.py --push` 更新
> 策略来源: content/posts/投资策略-9-场内配置.md
> 数据来源: FenglaiData（每次生成前先跑 `./fenglai update --datasets index_daily csindex_daily` 更新）
> 风险提示: 信号仅供参考，不构成投资建议。市场有风险，投资需谨慎。

---

"""


def render(closes, now) -> str:
    return FRONT_MATTER.format(date=now.strftime("%Y-%m-%dT%H:%M:%S+08:00")) + build_body(closes, now)


def strip_stamp(text: str) -> str:
    """去掉时间戳行后比较，用来判断数据到底有没有变。"""
    keep = [
        ln
        for ln in text.splitlines()
        if not ln.startswith("> 更新于：") and not ln.startswith("date:")
    ]
    return "\n".join(keep).strip()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成「指数信号 4 场内配置信号日报」")
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
    closes = fetch_closes()
    doc = render(closes, now)

    if args.dry_run:
        sys.stdout.write(doc)
        return 0

    old = DST.read_text(encoding="utf-8") if DST.exists() else ""
    if old and strip_stamp(old) == strip_stamp(doc):
        print(f"skip: 数据未变（{closes[HS300][-1][0]}），正文与现有文章一致")
        return 0

    DST.parent.mkdir(parents=True, exist_ok=True)
    DST.write_text(doc, encoding="utf-8")
    print(f"ok: 已写入 {DST}（沪深300 数据截至 {closes[HS300][-1][0]}）")

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
    stamp = closes[HS300][-1][0]
    commit = git("commit", "-m", f"data: 更新场内配置信号日报 {stamp}")
    if commit.returncode != 0:
        print(commit.stdout + commit.stderr, file=sys.stderr)
        return 1
    push = git("push", "origin", "master")
    if push.returncode != 0:
        print(push.stdout + push.stderr, file=sys.stderr)
        return 1
    print(f"ok: 已提交并推送（数据截至 {stamp}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
