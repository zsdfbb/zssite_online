# Hugo + PaperMod + GitHub Pages 执行计划

> 每个任务按目录划分，同一目录下的所有变更由一个 agent 完成。按依赖顺序执行。每个 impl 任务有对应的 test 任务。

## 上下文引用

参考设计计划：`docs/design-plans/2026-06-20-hugo-papermod-site.md`

## 任务清单

> **每个任务必须有 `type` 字段（`impl` 或 `test`）**。测试任务是一等公民——Phase 3 必须创建、必须完成、必须被证据支持。

### Task 1: themes/ — PaperMod submodule 初始化 [type=impl]
- **涉及目录**: `themes/`, 根目录 `.gitmodules`
- **涉及文件**: `themes/PaperMod/` (submodule), `.gitmodules`
- **描述**: 用 git submodule add -b v8.0 --depth=1 添加 PaperMod v8.0 主题
- **关联测试任务**: T2
- **依赖**: 无

### Task 2: tests/ — submodule pin 验证 [type=test]
- **涉及目录**: `themes/PaperMod/`, 根目录 `.gitmodules`
- **涉及文件**: 验证脚本（不写新文件）
- **描述**: 验证 submodule 已 clone 且固定在 v8.0 tag
- **验证方法**:
  - `cat .gitmodules | grep 'branch = v8.0'` — 命中
  - `git -C themes/PaperMod describe --tags | grep -E '^v8\.0'` — 命中
  - `ls themes/PaperMod/layouts/partials/comments.html` — 存在
- **关联 impl**: T1

### Task 3: 根目录 config — hugo.toml + .gitignore [type=impl]
- **涉及目录**: 根目录
- **涉及文件**: `hugo.toml`, `.gitignore`
- **描述**: 写入 Hugo 配置文件（含 profileMode、menu、outputs、pagination、homeInfoParams、params.giscus 占位）与 gitignore
- **关联测试任务**: T4
- **依赖**: 无

### Task 4: tests/ — hugo.toml + .gitignore 验证 [type=test]
- **涉及目录**: 根目录
- **涉及文件**: 验证脚本（不写新文件）
- **描述**: 验证 hugo.toml 语法 + Profile 模式 + baseURL + .gitignore 覆盖产物
- **验证方法**:
  - `hugo config > /dev/null` — 退出码 0
  - `grep -E '^\[params\.profileMode\]' hugo.toml` — 命中
  - `grep -E 'enabled *= *true' hugo.toml` — 命中（在 profileMode 块内）
  - `grep -E 'baseURL *= *"https://zssite.online/"' hugo.toml` — 命中
  - `grep -E '^/?public$' .gitignore` — 命中
  - `grep -E '^/?resources$' .gitignore` — 命中
- **关联 impl**: T3

### Task 5: static/ + assets/ — CNAME 与资源占位 [type=impl]
- **涉及目录**: `static/`, `assets/`
- **涉及文件**: `static/CNAME`, `assets/images/.gitkeep`
- **描述**: 写入 CNAME 文件（zssite.online）与 images 占位 .gitkeep
- **关联测试任务**: T6
- **依赖**: 无

### Task 6: tests/ — CNAME 验证 [type=test]
- **涉及目录**: `static/`, `public/`
- **涉及文件**: 验证脚本（不写新文件）
- **描述**: 验证 CNAME 文件存在且 Hugo 构建后产物中包含
- **验证方法**:
  - `cat static/CNAME | grep -E '^zssite\.online$'` — 命中
  - `hugo --minify --gc` 后 `cat public/CNAME | grep -E '^zssite\.online$'` — 命中
- **关联 impl**: T5

### Task 7: layouts/partials/ — Giscus partial [type=impl]
- **涉及目录**: `layouts/partials/`
- **涉及文件**: `layouts/partials/comments.html`
- **描述**: 自写 Giscus iframe partial（含 repo / repoId / category / categoryId 占位 / mapping / theme / lang 等）
- **关联测试任务**: T8
- **依赖**: 无

### Task 8: tests/ — Giscus partial 静态验证 [type=test]
- **涉及目录**: `layouts/partials/`
- **涉及文件**: 验证脚本（不写新文件）
- **描述**: 验证 partial 包含 Giscus iframe 标记
- **验证方法**:
  - `grep -c 'giscus.app' layouts/partials/comments.html` — ≥ 1
  - `grep -E 'data-repo *= *"zsdfbb/zssite_online"' layouts/partials/comments.html` — 命中
- **关联 impl**: T7

### Task 9: content/ — 示例文章 + about 页 [type=impl]
- **涉及目录**: `content/posts/`, `content/`
- **涉及文件**: `content/posts/hello-world.md`, `content/about.md`
- **描述**: 写入示例文章（含 `comments: true` front matter）+ about 页（Profile 模式展示）
- **关联测试任务**: T10
- **依赖**: 无

### Task 10: tests/ — content 渲染验证（含 Giscus 渲染） [type=test]
- **涉及目录**: `public/`
- **涉及文件**: 验证脚本（不写新文件）
- **描述**: 验证 hugo 构建后文章页渲染 Giscus iframe，about 页可达
- **验证方法**:
  - `hugo --minify` — 退出码 0
  - `ls public/posts/hello-world/index.html` — 存在
  - `grep -c giscus.app public/posts/hello-world/index.html` — ≥ 1
  - `ls public/about/index.html` — 存在
- **关联 impl**: T9
- **依赖**: T7（partial 必须先存在）

### Task 11: .github/workflows/ — CI workflow [type=impl]
- **涉及目录**: `.github/workflows/`
- **涉及文件**: `.github/workflows/hugo.yaml`
- **描述**: 写入 GitHub Actions workflow（peaceiris/actions-hugo + actions/configure-pages + upload-pages-artifact + deploy-pages，使用 `$default-branch` 触发）
- **关联测试任务**: T12
- **依赖**: 无

### Task 12: tests/ — workflow YAML 验证 [type=test]
- **涉及目录**: `.github/workflows/`
- **涉及文件**: 验证脚本（不写新文件）
- **描述**: 验证 workflow YAML 合法且关键 action / 配置齐全
- **验证方法**:
  - `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/hugo.yaml'))"` — 无异常
  - `grep -E 'peaceiris/actions-hugo@v3' .github/workflows/hugo.yaml` — 命中
  - `grep -E 'actions/(deploy-pages|upload-pages-artifact|configure-pages)' .github/workflows/hugo.yaml` — 三个都命中
  - `grep -E 'submodules: *recursive' .github/workflows/hugo.yaml` — 命中
  - `grep -E 'pages: *write' .github/workflows/hugo.yaml` — 命中
  - `grep -E 'id-token: *write' .github/workflows/hugo.yaml` — 命中
- **关联 impl**: T11

### Task 13: 整体构建 — 端到端构建验证 [type=test]
- **涉及目录**: `public/`
- **涉及文件**: 验证脚本（不写新文件）
- **描述**: 整合测试：跑 hugo --minify，验证所有关键路径与 Profile 渲染
- **验证方法**:
  - `hugo --minify` — 退出码 0
  - `ls public/index.html public/posts/index.html public/about/index.html public/archives/index.html public/search/index.html public/index.xml` — 全部存在
  - `grep -c '<article' public/index.html` — ≥ 1
  - `grep -c 'profile' public/index.html` — ≥ 1
- **关联 impl**: T1, T3, T5, T7, T9, T11
- **依赖**: T1, T3, T5, T7, T9, T11
- **MANUAL_ACK_REQUIRED**（在 "验证方法" 下方列出，不影响主测试通过）:
  - [ ] N8: DNS 验证（用户在 DNS 提供商配置 A 记录后）
  - [ ] N9: Giscus 实际联通（用户回填 repoId/categoryId 后）

## 验证清单

- [ ] **所有 (impl, test) 配对均已 completed**（Phase 3 review 放行条件）
- [ ] Task 13 整体构建验证通过（N5）
- [ ] CNAME 文件在产物中（N4）
- [ ] workflow YAML 合法且 action 齐全（N6）
- [ ] MANUAL_ACK 项（N8, N9）在 Phase 4 报告中原样保留，由用户勾选