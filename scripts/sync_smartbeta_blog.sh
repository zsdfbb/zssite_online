#!/usr/bin/env bash
# sync_smartbeta_blog.sh — 兼容入口：生成「指数信号 1. SmartBeta日报」
#
# 实际逻辑已迁到同目录的 sync_smartbeta_blog.py —— 数据源从 FenglaiIndex 的
# akshare/本地缓存换成 FenglaiData 的本地只读库（csindex_daily / etf_nav /
# etf_kline），不再依赖 FenglaiIndex 的 .venv 与 SMARTBETA_SIGNALS.md。
#
# 保留这个 .sh 是为了不动既有的 mailcode 调度（它调的就是这个路径）：
#   /home/zs/.config/mailcode/schedules.json → smartbeta_blog_sync → bash 本文件
#
# 用法:
#   bash scripts/sync_smartbeta_blog.sh          # 更新数据 → 生成 → 提交推送
#   bash scripts/sync_smartbeta_blog.sh --dry-run
#
# 参数原样转给 .py。

set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

exec /usr/bin/python3 "$DIR/sync_smartbeta_blog.py" --push "$@"
