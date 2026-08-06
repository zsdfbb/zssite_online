#!/usr/bin/env bash
# sync_drawdown_blog.sh — 把 FenglaiIndex/DRAWDOWN_BOLLINGER_SIGNALS.md 同步为 zssite_online Hugo 文章
#
# 用法:
#   bash scripts/sync_drawdown_blog.sh
#
# 行为:
#   1. 先跑 FenglaiIndex scripts/drawdown_bollinger_signal.py 生成最新源数据
#   2. 源文件不存在          → exit 0, stdout 提示
#   3. 解析失败              → exit 1, mailcode 触发错误邮件
#   4. 源文件 date ≤ 当前文章  → exit 0, 幂等跳过
#   5. 拼装新文章 + commit + push
#   6. 内容无变化            → exit 0, 不 commit
#   7. push 失败             → exit 1, mailcode 触发错误邮件
#
# 被 cron/launchd 定时触发, 也会被人工 `bash` 调用调试。

set -euo pipefail

FENGLAI="${HOME}/Develop/FenglaiIndex"
SRC="$FENGLAI/DRAWDOWN_BOLLINGER_SIGNALS.md"
DST="/home/zs/Develop/zssite_online/content/posts/指数信号-3-红利低波回撤布林信号日报.md"
ZSSITE="/home/zs/Develop/zssite_online"

# ---- 1. 先运行 drawdown_bollinger_signal.py 生成最新源文件（忽略 git push 失败） ----
echo "[step 1/3] running drawdown_bollinger_signal.py..."
cd "$FENGLAI"
.venv/bin/python3 scripts/drawdown_bollinger_signal.py --commit 2>&1 || true
cd "$ZSSITE"
echo ""

# ---- 2. 源文件存在性 ----
if [ ! -f "$SRC" ]; then
  echo "skip: source missing ($SRC)"
  exit 0
fi

# ---- 3. 让 python 解析源文件, 把 front matter 元信息写到 META_FILE, 正文写到 BODY_FILE ----
META_FILE=$(mktemp)
BODY_FILE=$(mktemp)
trap 'rm -f "$META_FILE" "$BODY_FILE"' EXIT

python3 - "$SRC" "$META_FILE" "$BODY_FILE" <<'PY'
import re, sys

src, meta_path, body_path = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(src, encoding="utf-8").read()

m = re.search(r"更新于[：:]\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})", text)
if not m:
    sys.stderr.write("ERROR: 无法解析'更新于'时间戳: " + src + "\n")
    sys.exit(2)

date, time = m.group(1), m.group(2)
front_date = f"{date}T{time}:00+08:00"

# 切掉首行 H1 和"更新于"行, 保留后续所有内容（信号表 + 计算方法）
lines = text.splitlines()
kept = []
skip_next_blank = True
for ln in lines:
    if ln.startswith("# "):
        continue
    if re.match(r"^>\s*更新于[：:]", ln):
        continue
    if skip_next_blank and ln.strip() == "":
        # 跳过紧跟"更新于"行的空行
        continue
    kept.append(ln)
    skip_next_blank = False

body = "\n".join(kept).strip("\n")

with open(meta_path, "w", encoding="utf-8") as f:
    # 单行空格分隔, 便于 `read NEW_DATE NEW_TIME FRONT_DATE < META_FILE`
    f.write(f"{date} {time} {front_date}\n")
with open(body_path, "w", encoding="utf-8") as f:
    f.write(body)
PY

read -r NEW_DATE NEW_TIME FRONT_DATE < "$META_FILE"
BODY=$(cat "$BODY_FILE")

# ---- 4. 与当前文章 date 对比, 幂等 ----
if [ -f "$DST" ]; then
  CUR_DATE=$(grep -E '^date:' "$DST" | head -1 | awk '{print $2}' | tr -d '"' | tr -d "'")
  if [ "$FRONT_DATE" = "$CUR_DATE" ]; then
    echo "skip: Hugo post already at $FRONT_DATE"
    exit 0
  fi
fi

# ---- 5. 拼装新文章 ----
TMP=$(mktemp)
trap 'rm -f "$TMP" "$META_FILE" "$BODY_FILE"' EXIT

cat > "$TMP" <<EOF
---
title: "指数信号｜3. 红利低波回撤+布林信号日报"
date: ${FRONT_DATE}
draft: false
tags: ["指数信号", "红利低波", "回撤", "布林", "信号"]
categories: ["指数信号"]
summary: "每次执行 sync_drawdown_blog.sh 自动同步的红利低波「回撤+布林」信号日报，含 3 只红利低波标的的当日回撤档位、布林 %B 止跌确认与建议仓位。"
comments: true
showToc: true
cover:
  image: ""
  alt: ""
  caption: ""
---
> 📡 自动同步: 每次执行 sync_drawdown_blog.sh 即发布最新报告
> 数据源: FenglaiIndex 项目的回撤+布林独立信号生成器
> 风险提示: 信号仅供参考,不构成投资建议。市场有风险,投资需谨慎。

---

> 更新于：${NEW_DATE} ${NEW_TIME}

${BODY}
EOF

# ---- 6. 写入 + 提交 + 推送 ----
cp "$TMP" "$DST"

cd "$ZSSITE"

# 内容未变 → 不 commit（git status --porcelain 对 untracked 新文件也生效, git diff 只认已跟踪文件）
if [ -z "$(git status --porcelain -- "$DST")" ]; then
  echo "skip: no diff after splice ($DST)"
  exit 0
fi

git add "$DST"
git commit -m "data: 更新红利低波回撤+布林信号日报 ${NEW_DATE}"
git push origin master
echo "ok: synced to $FRONT_DATE"
