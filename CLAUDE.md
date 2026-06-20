# zssite.online

> 风来的个人分享网站，记录学习、研究、折腾、躺平的日志。

## 项目类型

Hugo 静态站点，PaperMod 主题，部署于 GitHub Pages。

## 常用命令

- `hugo new posts/文章标题.md` — 创建新文章
- `hugo server -D` — 本地预览（含草稿）
- `hugo` — 构建到 `public/` 目录

## 文章约定

- `draft: false` 默认直接发布
- **categories**（大类）— 记录计算机技术、AI 相关、投资相关等内容
- **tags**（标签）— 细粒度关键词，如 `hugo`、`python`、`llm`、`linux`

## 部署

推送 `master` 分支 → GitHub Actions 自动构建并部署。

## 站点配置

- 语言：`zh-cn`，全站中文
- Profile 模式启用，头像 `/images/profile.jpg`
- 评论：Giscus
