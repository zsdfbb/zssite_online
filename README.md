# zssite.online

风来的个人博客——记录学习、研究、折腾、躺平的日志。

## 技术栈

- [Hugo](https://gohugo.io/) — 静态站点生成器
- [PaperMod](https://github.com/adityatelange/hugo-PaperMod/) — 主题
- [Giscus](https://giscus.app/) — 评论系统
- GitHub Pages — 部署

## 创建新文章

```bash
hugo new posts/文章标题.md
```

自动应用 `archetypes/posts.md` 模板，生成如下前置元数据：

```yaml
---
title: "文章标题"        # 页面标题
date: 2026-06-20        # 自动填入当前时间
draft: false            # 设为 true 则为草稿，本地预览需加 -D
tags: []                # 标签，用于分类检索
categories: []          # 分类，比 tags 更高层级
comments: true
showToc: true
cover:                  # 文章封面图（可选）
  image: ""
  alt: ""
  caption: ""
---
```

写完内容后将 `draft` 改为 `false` 或直接删除此行即可发布。

### 归类说明

- **categories**（大类）— 如 `meta`、`dev`、`ops`、`ai`、`invest`，一篇文章一般只归入一个类别
- **tags**（标签）— 更细粒度的关键词，如 `hugo`、`python`、`linux`，可以多个

主题 PaperMod 会自动生成按 categories 和 tags 的归档页面。

## 本地预览

```bash
hugo server -D     # -D 同时显示草稿
```

## 部署

推送到 `master` 分支，GitHub Actions 自动构建并部署到 GitHub Pages。
