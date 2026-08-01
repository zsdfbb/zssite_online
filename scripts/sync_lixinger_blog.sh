#!/usr/bin/env bash
# sync_lixinger_blog.sh — 把 FenglaiIndex/LIXINGER_SIGNALS.md 同步为 zssite_online Hugo 文章
#
# 用法:
#   bash scripts/sync_lixinger_blog.sh
#
# 行为（不负责生成源文件, 只负责把已存在的 LIXINGER_SIGNALS.md 发布到本站）:
#   1. 源文件不存在          → exit 0, stdout 提示
#   2. 解析失败              → exit 1, 触发错误邮件
#   3. 源文件生成时间 ≤ 当前文章 → exit 0, 幂等跳过
#   4. 拼装新文章 + commit + push
#   5. 内容无变化            → exit 0, 不 commit
#   6. push 失败             → exit 1, 触发错误邮件
#
# 可被 cron/launchd 定时触发, 也会被人工 `bash` 调用调试。

set -euo pipefail

FENGLAI="${HOME}/Develop/FenglaiIndex"
SRC="$FENGLAI/LIXINGER_SIGNALS.md"
DST="/home/zs/Develop/zssite_online/content/posts/指数信号-2-理杏仁低估日报.md"
ZSSITE="/home/zs/Develop/zssite_online"

# ---- 1. 源文件存在性 ----
if [ ! -f "$SRC" ]; then
  echo "skip: source missing ($SRC)"
  exit 0
fi

# ---- 2. 让 python 解析源文件, 把 front matter 元信息写到 META_FILE, 正文写到 BODY_FILE ----
META_FILE=$(mktemp)
BODY_FILE=$(mktemp)
TMP=$(mktemp)
trap 'rm -f "$META_FILE" "$BODY_FILE" "$TMP"' EXIT

python3 - "$SRC" "$META_FILE" "$BODY_FILE" <<'PY'
import re, sys

src, meta_path, body_path = sys.argv[1], sys.argv[2], sys.argv[3]
text = open(src, encoding="utf-8").read()

# 定位 "> 生成时间：YYYY-MM-DD HH:MM[:SS] ..." 整行, 完整保留以便原样回填
m = re.search(r"^>\s*(生成时间[：:]\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}(?::\d{2})?.*)$", text, re.M)
if not m:
    sys.stderr.write("ERROR: 无法解析'生成时间'时间戳: " + src + "\n")
    sys.exit(2)

gen_line = m.group(1)
dm = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}(?::\d{2})?)", gen_line)
gen_date, gen_time = dm.group(1), dm.group(2)
if len(gen_time) == 5:
    gen_time += ":00"
front_date = f"{gen_date}T{gen_time}+08:00"

d = re.search(r"数据截至[：:]\s*(\d{4}-\d{2}-\d{2})", gen_line)
data_date = d.group(1) if d else gen_date

# 切掉首行 H1 与开头 blockquote 元信息, 保留后续所有内容
lines = text.splitlines()
i = 0
if lines and lines[0].startswith("# "):
    i = 1
while i < len(lines) and (lines[i].startswith(">") or lines[i].strip() == ""):
    i += 1

# 只保留概览(一)与低估榜单(二)，丢弃「技术信号命中明细」(三)及之后的内容
body_lines = lines[i:]
for j, ln in enumerate(body_lines):
    if ln.startswith("## 三、"):  # 技术信号命中明细不需要
        body_lines = body_lines[:j]
        break
body = "\n".join(body_lines).strip("\n")

with open(meta_path, "w", encoding="utf-8") as f:
    # 单行空格分隔, 便于 `read FRONT_DATE DATA_DATE GEN_LINE < META_FILE`
    f.write(f"{front_date} {data_date} {gen_line}\n")
with open(body_path, "w", encoding="utf-8") as f:
    f.write(body)
PY

read -r FRONT_DATE DATA_DATE GEN_LINE < "$META_FILE"
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
cat > "$TMP" <<EOF
---
title: "指数信号｜2. 理杏仁低估日报"
date: ${FRONT_DATE}
draft: false
tags: ["指数信号", "理杏仁", "低估", "估值分位"]
categories: ["指数信号"]
summary: "每次执行 sync_lixinger_blog.sh 自动同步的理杏仁低估指数日报，含 10 年窗口 PE/PB 低估榜单与数据概览。"
comments: true
showToc: true
cover:
  image: ""
  alt: ""
  caption: ""
---
> 📡 自动同步: 每次执行 sync_lixinger_blog.sh 即发布最新报告
> 数据源: FenglaiIndex 项目的理杏仁低估指数信号
> 风险提示: 低估=准入观察非买入信号，非买入建议。市场有风险，投资需谨慎。

---

> ${GEN_LINE}

${BODY}
EOF

# ---- 5. 写入 + 提交 + 推送 ----
cp "$TMP" "$DST"

cd "$ZSSITE"

# 内容未变 → 不 commit（git status --porcelain 对 untracked 新文件也生效, git diff 只认已跟踪文件）
if [ -z "$(git status --porcelain -- "$DST")" ]; then
  echo "skip: no diff after splice ($DST)"
  exit 0
fi

git add "$DST"
git commit -m "data: 更新理杏仁低估日报 ${DATA_DATE}"
git push origin master
echo "ok: synced to $FRONT_DATE"
