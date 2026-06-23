#!/usr/bin/env bash
# sync_smartbeta_blog.sh — 把 FenglaiIndex/SMARTBETA_SIGNALS.md 同步为 zssite_online Hugo 文章
#
# 用法:
#   bash scripts/sync_smartbeta_blog.sh
#
# 行为:
#   1. 源文件不存在          → exit 0, stdout 提示
#   2. 源文件 date ≤ 当前文章  → exit 0, 幂等跳过
#   3. 解析失败              → exit 1, mailcode 触发错误邮件
#   4. 拼装新文章 + commit + push
#   5. 内容无变化            → exit 0, 不 commit
#   6. push 失败             → exit 1, mailcode 触发错误邮件
#
# 被 mailcode 调度器在 daily 09:30 触发, 也会被人工 `bash` 调用调试。

set -euo pipefail

SRC="/home/zs/Develop/FenglaiIndex/SMARTBETA_SIGNALS.md"
DST="/home/zs/Develop/zssite_online/content/posts/指数信号-1-smartbeta日报.md"
ZSSITE="/home/zs/Develop/zssite_online"

# ---- 1. 源文件存在性 ----
if [ ! -f "$SRC" ]; then
  echo "skip: source missing ($SRC)"
  exit 0
fi

# ---- 2. 让 python 解析源文件, 把 front matter 元信息写到 META_FILE, 正文写到 BODY_FILE ----
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

# 切掉首行 H1 和"更新于"行, 保留后续所有内容
lines = text.splitlines()
kept = []
skip_next_blank = True
for ln in lines:
    if ln.startswith("# SmartBeta"):
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

# ---- 3. 与当前文章 date 对比, 幂等 ----
if [ -f "$DST" ]; then
  CUR_DATE=$(grep -E '^date:' "$DST" | head -1 | awk '{print $2}' | tr -d '"' | tr -d "'")
  if [ "$FRONT_DATE" = "$CUR_DATE" ]; then
    echo "skip: Hugo post already at $FRONT_DATE"
    exit 0
  fi
fi

# ---- 4. 拼装新文章 ----
TMP=$(mktemp)
trap 'rm -f "$TMP" "$META_FILE" "$BODY_FILE"' EXIT

cat > "$TMP" <<EOF
---
title: "指数信号｜1. SmartBeta日报"
date: ${FRONT_DATE}
draft: false
tags: ["指数信号", "smartbeta", "调仓信号", "红利低波", "中证现金流"]
categories: ["指数信号"]
summary: "每天 08:30 由 launchd 自动同步的 SmartBeta 策略信号日报，含红利低波 EMA169 V5 与中证现金流 MA180 当日偏离比、仓位建议及近 12 次调仓历史。"
comments: true
showToc: true
cover:
  image: ""
  alt: ""
  caption: ""
---
> 📡 自动同步: 每天 08:30 由 launchd 任务更新
> 数据源: FenglaiIndex 项目的 SmartBeta 策略信号生成器
> 风险提示: 信号仅供参考,不构成投资建议。市场有风险,投资需谨慎。

---

> 更新于：${NEW_DATE} ${NEW_TIME}

${BODY}
EOF

# ---- 5. 写入 + 提交 + 推送 ----
cp "$TMP" "$DST"

cd "$ZSSITE"

# 内容未变 → 不 commit
if git diff --quiet -- "$DST"; then
  echo "skip: no diff after splice ($DST)"
  exit 0
fi

git add "$DST"
git commit -m "data: 更新 SmartBeta日报 ${NEW_DATE}"
git push origin master
echo "ok: synced to $FRONT_DATE"
