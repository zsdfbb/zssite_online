# Hugo + PaperMod + GitHub Pages 设计计划

## 背景

当前仓库 `/home/zs/Develop/zssite_online` 是空仓库（仅含 `LICENSE`、`README.md`、`.git`），git remote 为 `git@github.com:zsdfbb/zssite_online.git`。目标：把这个仓库改造成一个 Hugo 静态博客，启用 PaperMod v8.0 主题（git submodule，pin 到 `v8.0` tag），通过 GitHub Actions（peaceiris/actions-hugo + 官方 actions/deploy-pages）自动构建并发布到 GitHub Pages，最终通过自定义域名 `zssite.online` 对外提供。首页采用 Profile 模式，接入 Giscus 作为评论系统。

## 设计

### 整体方案

- **静态站点生成**：Hugo 0.128.0 extended + PaperMod v8.0 主题
- **自动化部署**：push 到默认分支 → GitHub Actions 构建 → 通过官方 actions/deploy-pages 发布到 GitHub Pages
- **自定义域名**：通过 `static/CNAME` 文件让 GitHub Pages 自动绑定 `zssite.online`
- **评论系统**：PaperMod v8.0 不内置 Giscus，自写 `layouts/partials/comments.html` partial 嵌入 iframe

### 架构决策

1. **PaperMod 用 git submodule 而非 Hugo Modules**：submodule 不依赖 Go toolchain，构建更轻量；CI 中通过 `actions/checkout` 的 `submodules: recursive` 自动拉取。
2. **PaperMod pin 到 v8.0 tag 而非 main**：v7 → v8 重构过参数，避免未来 breaking change 突袭。
3. **用 actions/deploy-pages 而非 peaceiris/actions-gh-pages**：官方 action 与 GitHub Pages API 原生集成，权限模型清晰（`pages: write` + `id-token: write`）。
4. **workflow 触发用 `$default-branch` 而非硬编码 `main`**：当前仓库默认分支是 `master`，硬编码 `main` 会导致 push 后不触发。
5. **baseURL 在 CI 注入**：使用 `actions/configure-pages@v5` 拿到官方 baseURL，覆盖 `hugo.toml` 中的 `baseURL`，保证部署后所有绝对 URL 正确。
6. **Giscus 自写 partial**：PaperMod 官方文档明示需要用户自写 `layouts/partials/comments.html`，不能依赖主题内置。
7. **Hugo Extended**：虽然 PaperMod v8.0 不用 SCSS 编译，但 `extended: true` 留作未来扩展余地，无副作用。

### 数据流

```
push to main/master
  → actions/checkout@v4 (submodules: recursive)
  → actions/configure-pages@v5 (提供 base_url)
  → peaceiris/actions-hugo@v3 (安装 Hugo 0.128.0 extended)
  → hugo --minify --baseURL <pages base_url>/
  → actions/upload-pages-artifact@v3
  → actions/deploy-pages@v4 (publish + CNAME 自动绑定)
```

## 涉及文件

### 新增

- `hugo.toml` — Hugo + PaperMod 全部配置
- `.gitmodules` — PaperMod submodule 记录
- `.gitignore` — 忽略 /public, /resources
- `themes/PaperMod/` — git submodule 内容（pin v8.0）
- `static/CNAME` — 内容: `zssite.online`
- `layouts/partials/comments.html` — 自定义 Giscus partial
- `content/posts/hello-world.md` — 示例文章（含 `comments: true`）
- `content/about.md` — About 页
- `assets/images/.gitkeep` — 占位
- `.github/workflows/hugo.yaml` — 构建 + 部署 workflow

### 修改

无（空仓库起步）

### 删除

无

## 测试策略

### 测试合同（与用户签字确认，Phase 3 审查依据）

> 每条需求一条验收条款。Phase 3 review subagent 按本节逐条跑命令 / 核对输出。

#### N1: PaperMod 子模块正确克隆并固定到 v8.0 tag
- **验收方法**:
  1. `cat .gitmodules` — 期望 `branch = v8.0`
  2. `git -C themes/PaperMod describe --tags` — 期望以 `v8.0` 开头
  3. `ls themes/PaperMod/layouts/partials/comments.html` — 文件存在但只有 placeholder 注释（证实需要自写）
- **优先级**: 必测
- **验收人**: review subagent

#### N2: `hugo.toml` 语法正确，Profile 模式与 Giscus 配置块就位
- **验收方法**:
  1. `hugo config | head -50` — 退出码 0，无 TOML 解析错误
  2. `grep -E '^\[params\.profileMode\]' hugo.toml` — 命中
  3. `grep -E 'enabled *= *true' hugo.toml`（在 profileMode 块内）— 命中
  4. `grep -E 'baseURL *= *"https://zssite.online/"' hugo.toml` — 命中
- **优先级**: 必测
- **验收人**: review subagent

#### N3: 自写的 `layouts/partials/comments.html` 渲染 Giscus iframe
- **验收方法**:
  1. `grep -c 'giscus.app' layouts/partials/comments.html` — ≥ 1
  2. `grep -E 'data-repo *= *"zsdfbb/zssite_online"' layouts/partials/comments.html` — 命中
  3. `hugo --minify` 后 `grep -c giscus.app public/posts/hello-world/index.html` — ≥ 1
- **优先级**: 必测
- **验收人**: review subagent

#### N4: `static/CNAME` 写入且被 build 产物包含
- **验收方法**:
  1. `cat static/CNAME` — 内容为 `zssite.online`
  2. `hugo --gc --minify` 后 `cat public/CNAME` — 内容为 `zssite.online`
- **优先级**: 必测
- **验收人**: review subagent

#### N5: `hugo --minify` 整体构建无错误，且产出关键路径齐全
- **验收方法**:
  1. `hugo --minify` — 退出码 0，无 ERROR 行
  2. `ls public/index.html public/posts/index.html public/about/index.html public/archives/index.html public/search/index.html public/index.xml` — 全部存在
  3. `grep -c '<article' public/index.html` — ≥ 1（首页有文章列表）
  4. `grep -c 'profile' public/index.html` — ≥ 1（Profile 模式渲染）
- **优先级**: 必测
- **验收人**: review subagent

#### N6: GitHub Actions workflow 语法正确，覆盖完整 CI 路径
- **验收方法**:
  1. `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/hugo.yaml'))"` — 无异常
  2. `grep -E 'peaceiris/actions-hugo@v3' .github/workflows/hugo.yaml` — 命中
  3. `grep -E 'actions/(deploy-pages|upload-pages-artifact|configure-pages)' .github/workflows/hugo.yaml` — 三个 action 都出现
  4. `grep -E 'submodules: *recursive' .github/workflows/hugo.yaml` — 命中
  5. `grep -E 'pages: *write' .github/workflows/hugo.yaml` — 命中
  6. `grep -E 'id-token: *write' .github/workflows/hugo.yaml` — 命中
- **优先级**: 必测
- **验收人**: review subagent

#### N7: `.gitignore` 排除 Hugo 产物与本地临时文件
- **验收方法**: `grep -E '^/?public$' .gitignore` 与 `grep -E '^/?resources$' .gitignore` 均命中
- **优先级**: 必测
- **验收人**: review subagent

#### N8: DNS 验证
- **验收方法**: `dig zssite.online +short` — 返回四条 185.199.108/109/110/111.153 中任意；`curl -I https://zssite.online/` — 返回 200
- **优先级**: MANUAL_ACK_REQUIRED（依赖用户外部 DNS 操作 + GitHub Pages HTTPS 证书签发）
- **验收人**: 用户人工

#### N9: Giscus 实际联通
- **验收方法**: 打开任一文章页确认评论框正常渲染，并在 `zsdfbb/zssite_online` Discussions 中看到对应 thread
- **优先级**: MANUAL_ACK_REQUIRED（依赖用户启用 Discussions + giscus.app 获取 ID）
- **验收人**: 用户人工

### 测试框架与命令

- 本项目为静态站点，无传统单元测试框架；通过 shell 命令对 build 产物做端到端验证
- Hugo 构建: `hugo --minify`
- YAML 验证: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/hugo.yaml'))"`
- 文件断言: `grep`、`ls`、`cat` 命令组合

## 波及文档

- `docs/design-final/` — 当前不存在，本次为建站启动，先不创建（待首次内容沉淀后建）

## 风险与注意事项

- **PaperMod v8.0 submodule pin**：wiki 与 README 对最低 Hugo 版本存在分歧（wiki ≥v0.146.0，README ≥v0.112.4）。pin Hugo 0.128.0，落在两者交集兼容区间；若 CI 报错需评估升级到 0.146+。
- **Giscus ID 占位**：先用 `R_PLACEHOLDER` / `DIC_PLACEHOLDER`，部署后用户回填再 push。第一次部署 Giscus iframe 会渲染但加载失败，属预期。
- **GitHub Pages 首次部署**：用户必须先在仓库 Settings → Pages 把 Source 改为 `GitHub Actions`。
- **DNS 传播**：A 记录变更通常 5-30 分钟生效，最长 24 小时；HTTPS 证书签发通常 5-15 分钟，最长 24 小时。
- **仓库默认分支**：当前为 `master`，workflow 触发用 `$default-branch` 避免硬编码。
