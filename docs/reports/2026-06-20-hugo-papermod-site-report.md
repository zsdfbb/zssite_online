# Hugo + PaperMod + GitHub Pages 建站报告

## 项目概述

- **仓库**: `/home/zs/Develop/zssite_online` (git remote: `git@github.com:zsdfbb/zssite_online.git`)
- **日期**: 2026-06-20
- **工作流**: sequential-workflow (full 方案)
- **目标**: 把空仓库改造成 Hugo 静态博客，启用 PaperMod 主题，自动部署到 GitHub Pages

## 实施结果

### 任务清单与状态

| Task | 类型 | 描述 | 状态 |
|---|---|---|---|
| T1 | impl | PaperMod submodule 初始化（pin v8.0） | PASS |
| T2 | test | submodule pin 验证 | PASS |
| T3 | impl | hugo.toml + .gitignore 写入 | PASS |
| T4 | test | hugo.toml + .gitignore 验证 | PASS |
| T5 | impl | static/CNAME + assets/.gitkeep | PASS |
| T6 | test | CNAME 验证 | PASS |
| T7 | impl | layouts/partials/comments.html（Giscus） | PASS |
| T8 | test | Giscus partial 静态验证 | PASS |
| T9 | impl | content/posts/hello-world.md + about.md | PASS（review 后追加 archives.md + search.md） |
| T10 | test | content 渲染验证 | PASS |
| T11 | impl | .github/workflows/hugo.yaml | PASS |
| T12 | test | workflow YAML 验证 | PASS |
| T13 | test | 整体构建端到端验证 | PASS |

### Review 轮次

- 每个 (impl, test) 对均通过 1 轮 review
- T9+T10 首次 review 给出 **CONDITIONAL PASS**（submodule 文件被直接修改）；通过将 3 个修补迁移到 `layouts/partials/templates/` 项目级覆盖目录修复后通过

### 触发 1 轮 review 重派的情况

- T9：subagent 直接修改 `themes/PaperMod/` 内的 3 个模板文件以适配本地 Hugo 0.163，导致 submodule 工作树 dirty。修复方案：将 3 个文件原样复制到项目 `layouts/partials/templates/`（Hugo override pattern），然后 `git checkout --` 回滚 submodule 修改。重派了 1 个修复 subagent + 1 轮 verification。

## 文件清单（最终交付）

### 新增文件

| 文件 | 大小 | 说明 |
|---|---|---|
| `hugo.toml` | 2562 B | Hugo 配置：baseURL/PaperMod/Profile mode/Giscus/menu/outputs |
| `.gitignore` | 53 B | 排除 /public, /resources, *.log, .DS_Store |
| `.gitmodules` | 127 B | PaperMod submodule pin v8.0 |
| `static/CNAME` | 13 B | 自定义域名 zssite.online |
| `assets/images/.gitkeep` | 0 B | 头像占位目录 |
| `layouts/partials/comments.html` | 1725 B | Giscus iframe partial（覆盖主题 placeholder） |
| `layouts/partials/templates/opengraph.html` | 2476 B | Hugo 0.163 partial-path 兼容（项目级覆盖） |
| `layouts/partials/templates/schema_json.html` | 3794 B | 同上 |
| `layouts/partials/templates/twitter_cards.html` | 1321 B | 同上 |
| `content/posts/hello-world.md` | 656 B | 示例文章（comments: true） |
| `content/about.md` | 367 B | About 页面（comments: false） |
| `content/archives.md` | 84 B | 触发 PaperMod archives layout |
| `content/search.md` | 82 B | 触发 PaperMod search layout |
| `.github/workflows/hugo.yaml` | 1291 B | 构建 + 部署 workflow |

### Submodule

- `themes/PaperMod/` (commit `a2eb47b`, tag `v8.0`)，已通过 `git submodule add -b v8.0 --depth=1` pin

## 测试合同验证（N1-N9）

| ID | 测试目标 | 验证方法 | 状态 |
|---|---|---|---|
| N1 | PaperMod submodule pin v8.0 | `cat .gitmodules` 含 `branch = v8.0`；`git describe --tags` = `v8.0` | PASS |
| N2 | hugo.toml 语法 + Profile + baseURL | `hugo config` exit 0；`[params.profileMode]` 块就位；`enabled = true` | PASS |
| N3 | Giscus partial 渲染 | `grep giscus.app layouts/partials/comments.html` ≥ 1；`public/posts/hello-world/index.html` 渲染 giscus iframe | PASS |
| N4 | CNAME 在产物中 | `public/CNAME` = `zssite.online`（13 字节，byte-identical） | PASS |
| N5 | Hugo 构建无错 + 路径齐全 | `hugo --minify` exit 0；6 个关键路径（index/posts/posts/hello-world/about/archives/search/index.xml/CNAME）全存在 | PASS |
| N6 | workflow YAML 正确 | `yaml.safe_load` 无异常；peaceiris + configure-pages + upload-pages-artifact + deploy-pages 都在；submodules/pages/id-token 权限齐全 | PASS |
| N7 | .gitignore 覆盖产物 | `/public`、`/resources` 都在 | PASS |
| N8 | DNS 验证 | `dig zssite.online +short` → 需返回 185.199.108/109/110/111.153 | MANUAL_ACK_REQUIRED |
| N9 | Giscus 实际联通 | 文章页评论框渲染 + 仓库 Discussions 出现 thread | MANUAL_ACK_REQUIRED |

## 关键决策回顾

1. **PaperMod 用 git submodule 而非 Hugo Modules** — 无 Go toolchain 依赖
2. **PaperMod pin 到 v8.0 tag** — 避免未来 breaking change 突袭
3. **workflow 用 actions/deploy-pages（官方）** — 原生 Pages API 集成
4. **触发器用 `$default-branch`** — 当前仓库默认分支是 master，硬编码 main 会漏触发
5. **baseURL 通过 `steps.pages.outputs.base_url` 注入** — 覆盖 hugo.toml 默认值，保证部署后绝对 URL 正确
6. **Giscus 自写 partial** — PaperMod v8.0 不内置，需覆盖 `layouts/partials/comments.html`
7. **Hugo Extended（CI 0.128.0）** — 当前 PaperMod 不需要 SCSS，但 extended 留扩展余地
8. **3 个主题模板修补迁移到项目 `layouts/partials/templates/`** — Hugo override pattern，避免污染 pinned submodule

## 风险与注意事项

### 已缓解

- **PaperMod v8.0 与 Hugo 0.163+ partial 路径不兼容**：CI 用 Hugo 0.128.0 无此问题；本地 0.163+ 通过项目级 override 解决
- **submodule 工作树 dirty**：通过 override pattern + `git checkout --` 回滚，submodule 保持 clean

### 仍存在

- **Hugo deprecation WARN**：本地 0.163 输出 WARN（`languageCode` / `LanguageDirection` / `LanguageCode`）。Hugo 0.158+ 弃用，0.163 仍兼容，未来版本可能移除。CI 用 0.128 无此 WARN。
- **Giscus 占位 ID**：`R_PLACEHOLDER` / `DIC_PLACEHOLDER` 是占位。脚本标签会渲染但 Giscus 加载失败属预期。用户需访问 giscus.app 获取真实 ID 后回填。
- **仓库默认分支为 master**：`git log` 显示 master 是默认分支。workflow 触发用 `$default-branch`，已兼容；如未来切换到 main 也无需改 workflow。

## 待用户处理（MANUAL_ACK）

| 步骤 | 操作 | 状态 |
|---|---|---|
| 1 | 在 GitHub 仓库 Settings → Pages 把 Source 改为 `GitHub Actions` | 待用户 |
| 2 | 在 DNS 提供商配置 zssite.online 的 4 条 A 记录：`185.199.108.153`、`185.199.109.153`、`185.199.110.153`、`185.199.111.153` | 待用户 |
| 3 | 在仓库 Settings → General → Features 启用 **Discussions** | 待用户 |
| 4 | 访问 giscus.app/zh-CN 选 `zsdfbb/zssite_online` + Announcements category，复制 `repoId` 和 `categoryId` 回填到 `hugo.toml` | 待用户 |
| 5 | `git push origin master` 触发首次部署 | 待用户 |
| 6 | 验证 N8：`dig zssite.online +short` + `curl -I https://zssite.online/` | 待用户 |
| 7 | 验证 N9：打开任一文章页确认评论框 + Discussions 出现 thread | 待用户 |

## 后续优化（不在本次范围）

- 接入 Google Analytics / Plausible（`[params.analytics.google]`）
- 替换 `assets/images/profile.png` 与 `favicon.ico`
- 引入 Algolia DocSearch（文章量大时）
- 启用 `hugo` 的 `imageProcessing` 生成 WebP 缩略图

## 归档

- `docs/design-plans/2026-06-20-hugo-papermod-site.md`
- `docs/exec-plans/2026-06-20-hugo-papermod-site.md`