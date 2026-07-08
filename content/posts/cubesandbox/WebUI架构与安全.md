---
title: "WebUI (Dashboard) 架构、处理流程与安全配置"
date: 2026-07-08T00:00:00+08:00
draft: false
tags: ["cubesandbox"]
categories: ["cubesandbox"]
comments: true
showToc: true
weight: 100
---


> 调研时间: 2026/07/08
> 调研范围: `/home/zs/Develop/TraceCubeSandbox/web/` 全量 React/TypeScript 源码
> 目的: 系统性梳理 WebUI (管理控制台) 的架构、处理流程与安全配置
>
> 每节都带文件位置证据，可以直接引用。

---

## 1. 概述

WebUI 是 CubeSandbox 的**浏览器端管理控制台**，基于 React 18 + TypeScript 构建，提供沙箱、模板、节点、API 密钥和集群可观测性的可视化界面。

| 属性 | 值 |
|------|-----|
| **语言** | TypeScript (React 18) |
| **构建工具** | Vite 6 |
| **路由** | react-router-dom v6 |
| **数据获取** | TanStack Query v5 |
| **样式** | Tailwind CSS v3 + HSL 变量 |
| **UI 组件** | Radix UI (shadcn 风格) |
| **状态管理** | Zustand v5 |
| **国际化** | i18next |
| **API 客户端** | openapi-typescript (生成类型) |
| **Mock 工具** | MSW (Mock Service Worker) |
| **开发端口** | 5173 |
| **代理** | Vite proxy /cubeapi → 127.0.0.1:3000 |

**核心职责**:
- 沙箱管理: 创建/查看/暂停/恢复/销毁
- 模板管理: 查看/构建/删除/兼容性矩阵
- 节点管理: 集群健康监控
- API 密钥管理
- AI Agent 实例 (AgentHub)
- 系统设置 (主题/语言/集群信息/账户)
- 可观测性 (预留给 Prometheus/Grafana)
- 网络管理 (预留)

---

## 2. 架构

### 2.1 顶层目录结构

来源: `web/` 目录遍历

```
web/
├── package.json                  # 项目配置 + 依赖
├── vite.config.ts                # Vite 配置 + proxy + 版本注入
├── tailwind.config.js            # Tailwind 主题 + 颜色
├── tsconfig.json                 # TypeScript 严格模式
├── postcss.config.js             # PostCSS 配置
├── index.html                    # HTML 入口
├── public/
│   ├── mockServiceWorker.js      # MSW Service Worker (dev mock)
│   └── assets/
│       ├── cube-logo.svg         # 应用 Logo
│       └── cube.svg              # 图标
├── src/
│   ├── main.tsx                  # 入口 (QueryClient + Router + ThemeProvider)
│   ├── vite-env.d.ts             # Vite 类型声明
│   ├── lib/
│   │   ├── api.ts                # fetch 封装 + ApiError 类型
│   │   ├── session.ts            # localStorage session 管理
│   │   ├── utils.ts              # 工具函数
│   │   ├── mockFlag.ts           # Mock 模式开关
│   │   ├── sandboxActionError.ts # 沙箱操作错误格式化
│   │   └── templateConfig.ts     # 模板配置工具
│   ├── api/
│   │   ├── client.ts             # 类型化 API 客户端 (命名空间)
│   │   └── generated/
│   │       └── schema.ts         # openapi-typescript 生成的类型
│   ├── hooks/
│   │   ├── useRuntimeConfig.ts   # 运行时配置 hook
│   │   ├── useControlPlaneVersion.ts  # 控制面版本 hook
│   │   └── useGlobalHotkeys.ts   # 全局快捷键 hook
│   ├── components/
│   │   ├── ui/                   # 通用 UI 组件 (button/card/input/badge/skeleton 等)
│   │   ├── layout/               # 布局组件 (未使用)
│   │   ├── AuthGuard.tsx         # 路由守卫
│   │   ├── AppShell.tsx          # 应用外壳 (Rail + TopBar + Outlet)
│   │   ├── Rail.tsx              # 侧边导航栏
│   │   ├── TopBar.tsx            # 顶部栏
│   │   ├── ThemeProvider.tsx     # 主题提供者
│   │   ├── ThemeToggle.tsx       # 主题切换
│   │   ├── LanguageSwitcher.tsx  # 语言切换
│   │   ├── CommandPalette.tsx    # 命令面板 (Cmd+K)
│   │   ├── SandboxActionErrorBanner.tsx  # 操作错误提示
│   │   └── agents/               # Agent 组件
│   ├── pages/                    # 页面组件
│   │   ├── Login.tsx             # 登录页
│   │   ├── Overview.tsx          # 概览页
│   │   ├── Sandboxes.tsx         # 沙箱列表
│   │   ├── SandboxNew.tsx        # 创建沙箱
│   │   ├── SandboxDetail.tsx     # 沙箱详情
│   │   ├── Templates.tsx         # 模板列表
│   │   ├── TemplateDetail.tsx    # 模板详情
│   │   ├── Nodes.tsx             # 节点列表
│   │   ├── NodeDetail.tsx        # 节点详情
│   │   ├── Versions.tsx          # 版本信息
│   │   ├── Keys.tsx              # API 密钥
│   │   ├── AgentHub.tsx          # AI Agent 管理
│   │   ├── Settings.tsx          # 系统设置
│   │   ├── Network.tsx           # 网络 (预留)
│   │   ├── Observability.tsx     # 可观测性 (预留)
│   │   ├── TemplateStore.tsx     # 模板商店
│   │   └── Placeholder.tsx       # 占位页
│   ├── store/                    # Zustand stores
│   │   ├── ui.ts                 # UI 状态 (命令面板)
│   │   └── theme.ts              # 主题状态 (light/dark/system)
│   ├── state/
│   │   └── agentStore.ts         # Agent 全局状态
│   ├── data/
│   │   ├── agents.ts             # Agent 数据定义
│   │   └── templateStore.ts      # 模板商店数据
│   ├── types/                    # TypeScript 类型
│   ├── i18n/                     # 国际化配置
│   │   ├── index.ts              # i18next 初始化
│   │   ├── resources.ts          # 资源映射
│   │   └── types.d.ts            # i18n 类型声明
│   ├── locales/                  # 翻译文件
│   │   ├── zh/                   # 中文
│   │   └── ...                   # 各模块翻译
│   ├── styles/
│   │   └── globals.css           # 全局样式 + HSL 变量 + 组件样式
│   └── mocks/                    # MSW Mock
│       ├── browser.ts            # MSW worker 初始化
│       ├── handlers/             # Mock handlers
│       └── fixtures/             # Mock 数据
```

### 2.2 页面路由

来源: `web/src/main.tsx:49-72`

```
/login          → LoginPage           (公开)
/               → OverviewPage        (受保护)
/sandboxes      → SandboxesPage       (受保护)
/sandboxes/new  → SandboxNewPage      (受保护)
/sandboxes/:sandboxID → SandboxDetailPage (受保护)
/templates      → TemplatesPage       (受保护)
/templates/:templateID → TemplateDetailPage (受保护)
/nodes          → NodesPage           (受保护)
/nodes/:nodeID  → NodeDetailPage      (受保护)
/versions       → VersionsPage        (受保护)
/keys           → KeysPage            (受保护)
/store          → TemplateStorePage   (受保护)
/agenthub       → AgentHubPage        (受保护)
/settings       → SettingsPage        (受保护)
/observability  → ObservabilityPage   (受保护/预留)
/network        → NetworkPage         (受保护/预留)
/*              → 重定向到 /           (通配)
```

所有受保护路由嵌套在 `<AuthGuard>` 组件内，未认证用户被重定向到 `/login`。

### 2.3 数据流

```
User browser              WebUI (React)                  CubeAPI
  │                              │                          │
  │ 用户操作 (页面交互)           │                          │
  │ ────────────────────────────▶│                          │
  │                              │                          │
  │                              │ ① TanStack Query          │
  │                              │   - 自动缓存 + 重试      │
  │                              │   - staleTime: 2-60s     │
  │                              │   - refetchInterval: 5-30s│
  │                              │                          │
  │                              │ ② api/client.ts          │
  │                              │   - 命名空间 API 封装     │
  │                              │   - 调用 lib/api.ts       │
  │                              │                          │
  │                              │ ③ lib/api.ts             │
  │                              │   - BASE: /cubeapi/v1     │
  │                              │   - 注入 X-API-Key        │
  │                              │   - 注入 X-Session-Token  │
  │                              │   - 注入 Content-Type      │
  │                              │   - 异常封装 ApiError     │
  │                              │                          │
  │                              │ ④ Vite proxy (开发环境)   │
  │                              │   /cubeapi → localhost:3000│
  │                              │ ────────────────────────▶│
  │                              │ ◀────────────────────────│
  │                              │                          │
  │                              │ ⑤ Zustand store           │
  │                              │   - 全局状态管理           │
  │                              │   - theme.ts: 主题持久化   │
  │                              │   - ui.ts: 命令面板状态    │
  │                              │   - agentStore.ts: Agent   │
  │                              │                          │
  │                              │ ⑥ i18next                 │
  │                              │   - 浏览器语言检测         │
  │                              │   - localStorage 缓存      │
  │                              │                          │
  │ ◀───────────────────────────│                          │
  │ 渲染 UI                      │                          │
```

---

## 3. 处理流程

### 3.1 认证流程

来源: `web/src/pages/Login.tsx:14-46`, `web/src/components/AuthGuard.tsx:17-54`, `web/src/lib/session.ts:7-40`

```
LoginPage                     CubeAPI
  │                              │
  │ POST /cubeapi/v1/auth/login  │
  │ { "username", "password" }   │
  │ ────────────────────────────▶│
  │                              │
  │                              │ ① 密码验证 (argon2)
  │                              │ ② 生成 session token (uuid)
  │                              │ ③ TTL 24h
  │                              │
  │ ◀───────────────────────────│
  │ { token, username,           │
  │   expiresInSecs }            │
  │                              │
  │ ④ setSession(token, username)│
  │   localStorage:              │
  │   - cube.session (token)     │
  │   - cube.sessionUser (name)  │
  │   - cube.authStatus (allowed)│
  │                              │
  │ ⑤ 跳转到 redirectTo (或 /)   │
```

**AuthGuard 守卫逻辑**:

来源: `web/src/components/AuthGuard.tsx:17-54`

```
AuthGuard mount
  │
  ├── GET /cubeapi/v1/auth/session
  │   │
  │   ├── 响应: { authRequired: false, authenticated: false }
  │   │   → 放行 (无数据库/开放模式)
  │   │
  │   ├── 响应: { authRequired: true, authenticated: true }
  │   │   → 放行 (有效 session)
  │   │
  │   └── 响应: { authRequired: true, authenticated: false }
  │       → 重定向到 /login (session 过期/无效)
  │
  └── 网络异常
      → 回退到 sessionStorage 缓存的 authStatus
      → 无缓存 → 重定向到 /login
```

### 3.2 API 调用流程

来源: `web/src/lib/api.ts:31-56`, `web/src/api/client.ts`

```
Page/Component
  │
  │ ① 调用命名空间 API (e.g., sandboxApi.list())
  │
  ├── api/client.ts
  │   - 封装请求参数
  │   - 调用 api() 函数
  │
  ├── lib/api.ts: api()
  │   ├── 构建 URL: /cubeapi/v1 + path + query
  │   ├── 注入标头:
  │   │   ├── X-API-Key: localStorage.cube.apiKey
  │   │   └── X-Session-Token: localStorage.cube.session
  │   ├── fetch() 发送请求
  │   ├── 401/403/500 → 抛出 ApiError(status, message, body)
  │   └── 200/201 → 返回 JSON 响应
  │
  └── TanStack Query
      - 缓存响应
      - refetchInterval 自动刷新
      - 错误重试 (retry: 1)
```

### 3.3 沙箱生命周期操作

来源: `web/src/api/client.ts:191-214`, `web/src/pages/Sandboxes.tsx:22-173`

| 操作 | API 路径 | 方法 | 前端位置 |
|------|----------|------|----------|
| **列表** | `/v2/sandboxes` | GET | `SandboxesPage.tsx` |
| **创建** | `/sandboxes` | POST | `SandboxNewPage.tsx` |
| **详情** | `/sandboxes/:id` | GET | `SandboxDetailPage.tsx` |
| **销毁** | `/sandboxes/:id` | DELETE | `SandboxesPage.tsx` |
| **暂停** | `/sandboxes/:id/pause` | POST | `SandboxesPage.tsx` |
| **恢复** | `/sandboxes/:id/resume` | POST | `SandboxesPage.tsx` |
| **超时** | `/sandboxes/:id/timeout` | POST | `SandboxDetailPage.tsx` |
| **日志** | `/v2/sandboxes/:id/logs` | GET | `SandboxDetailPage.tsx` |

### 3.4 AgentHub 快照与克隆操作

来源: `web/src/pages/AgentHub.tsx:927-1120`

AgentHub 的快照和克隆采用**异步操作模型**:
- 创建快照请求立即返回 `operationId`
- 前端乐观占位: 在时间线顶部插入"存档中..."节点
- 后台轮询操作流水 (`listOperations`) 获取完成状态
- 操作完成 → 替换占位为真实数据
- 操作失败 → 移除占位并显示错误

克隆同样使用乐观占位:
- 立即在列表中插入 N 张"分身中..."卡片
- 后台批量调用 clone API
- 所有请求完成 → 替换占位或显示第一个错误

---

## 4. 数据存储

### 4.1 localStorage 键位表

来源: `web/src/lib/session.ts`, `web/src/store/theme.ts`, `web/src/pages/Keys.tsx`, `web/src/lib/mockFlag.ts`

| Key | 位置 | 用途 | 持久化 |
|-----|------|------|--------|
| `cube.session` | `session.ts:7` | Session Token | 永久 (localStorage) |
| `cube.sessionUser` | `session.ts:8` | 用户名 | 永久 (localStorage) |
| `cube.authStatus` | `session.ts:9` | 认证状态缓存 | 会话 (sessionStorage) |
| `cube.apiKey` | `Keys.tsx:21` | API Key | 永久 (localStorage) |
| `cube-theme` | `theme.ts:8` | 主题模式 | 永久 (localStorage) |
| `cube.lang` | `i18n/index.ts:27` | 语言偏好 | 永久 (localStorage) |
| `cube.useMock` | `mockFlag.ts:11` | Mock 开关 | 永久 (localStorage) |

### 4.2 TanStack Query 缓存配置

来源: `web/src/main.tsx:37-41`

```typescript
const qc = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 2_000 },
  },
});
```

各模块的刷新间隔:
| Query | refetchInterval | 用途 |
|-------|----------------|------|
| `['cluster']` | 10s | 集群概览 |
| `['sandboxes']` | 5s | 沙箱列表 |
| `['templates']` | 30s | 模板列表 |
| `['templates', 'compat']` | 30s | 模板兼容矩阵 |
| `['nodes']` | 15s | 节点列表 |
| `['sandbox', id]` | 5s | 沙箱详情 |
| `['sandbox-logs', id]` | 10s | 沙箱日志 |
| `['runtime-config']` | 60s staleTime | 运行时配置 |

---

## 5. 安全机制

### 5.1 认证机制

来源: `web/src/components/AuthGuard.tsx:17-54`, `web/src/lib/api.ts:35-36`

**两层凭证传递**:

```
API Key:     localStorage("cube.apiKey")   → X-API-Key header
Session:     localStorage("cube.session")  → X-Session-Token header
```

- 两个标头同时注入，由服务端决定优先使用哪个
- `AuthGuard` 所有非 `/login` 路由前调用 `/auth/session` 校验
- `/auth/session` 返回 `{ authRequired, authenticated }` 决定放行/重定向

### 5.2 安全特性

| # | 特性 | 位置 | 说明 |
|---|------|------|------|
| S1 | AuthGuard 路由守卫 | `src/components/AuthGuard.tsx:17-54` | 未认证重定向到 `/login` |
| S2 | 双重标头注入 (API Key + Session) | `src/lib/api.ts:35-44` | 同时注入 X-API-Key 和 X-Session-Token |
| S3 | Vite proxy 开发隔离 | `vite.config.ts:42-47` | 仅开发环境生效 |
| S4 | TypeScript 严格模式 | `tsconfig.json:13` | 编译时类型安全 |
| S5 | 暗色主题无安全隐患 | `tailwind.config.js` | - |
| S6 | sessionStorage 认证状态回退 | `src/components/AuthGuard.tsx:34` | 网络异常时回退到已缓存状态 |
| S7 | 登出清除 localStorage | `src/lib/session.ts:27-31` | 清除所有会话数据 |
| S8 | 密码修改本地校验 | `src/pages/Settings.tsx:239-245` | 长度/确认密码客户端校验 |

### 5.3 已知风险

| # | 风险 | 等级 | 说明 |
|---|------|------|------|
| R1 | Token 明文存储 localStorage | 🟠 中 | XSS 可窃取 session token 和 API key |
| R2 | 客户端路由守卫 | 🟡 中 | 仅前端控制，服务端无对应校验 |
| R3 | 无 CSP 标头 | 🟡 中 | 需在反向代理配置 |
| R4 | MSW Mock Service Worker 生产隐患 | 🟢 低 | 如果打包时意外包含 mock 可能干扰正常请求 |
| R5 | 默认凭据 admin/admin | 🟡 中 | 登录页预设 username="admin"，需部署后改密 |

---

## 6. 配置项

### 6.1 构建时配置

来源: `web/vite.config.ts:32-35`

```typescript
define: {
  __APP_VERSION__: JSON.stringify(resolveAppVersion()),
}
```

版本解析优先级:
1. 环境变量 `CUBE_VERSION` (CI/CD 构建时注入)
2. `git describe --tags --abbrev=0` (最近 tag)
3. `package.json` version 字段 (兜底)

### 6.2 环境变量

| 变量 | 默认值 | 用途 | 位置 |
|------|--------|------|------|
| `VITE_USE_MOCK` | 未设置 | 启用 MSW Mock | `mockFlag.ts:23` |
| `VITE_FORCE_LANG` | 未设置 | 强制语言 | `i18n/index.ts:9` |
| `VITE_HIDE_AGENT_RECOVER` | 未设置 | 隐藏 Agent 恢复功能 | `AgentHub.tsx:44` |
| `CUBE_VERSION` | 自动推导 | 应用版本 | `vite.config.ts:19` |

### 6.3 Vite 代理配置

来源: `web/vite.config.ts:42-47`

```typescript
server: {
  port: 5173,
  proxy: {
    '/cubeapi': 'http://127.0.0.1:3000',
  },
}
```

| 配置项 | 值 | 说明 |
|--------|------|------|
| API 代理 | `http://127.0.0.1:3000` | 开发服务器 |
| 端口 | 5173 | Vite 开发端口 |
| 前端基础路径 | `/cubeapi/v1` | API 前缀 |

### 6.4 npm scripts

| 命令 | 用途 |
|------|------|
| `npm run dev` | 启动 Vite 开发服务器 |
| `npm run build` | TypeScript 检查 + 生产构建 |
| `npm run preview` | 预览生产构建 |
| `npm run lint` | TypeScript 类型检查 |
| `npm run api:export` | 从 CubeAPI 导出 OpenAPI 规范 |
| `npm run api:generate` | 从 OpenAPI 生成 TypeScript 类型 |
| `npm run api:sync` | 导出 + 生成 |

---

## 7. 关键文件清单

| 模块 | 路径 | 关键内容 |
|------|------|----------|
| **入口** | `web/src/main.tsx:1-89` | React DOM 渲染 + QueryClient + 路由 |
| **路由** | `web/src/main.tsx:49-72` | 路由配置 (Routes) |
| **AuthGuard** | `web/src/components/AuthGuard.tsx:17-54` | 路由守卫逻辑 |
| **API 封装** | `web/src/lib/api.ts:31-56` | fetch 封装 + ApiError |
| **Session 管理** | `web/src/lib/session.ts:7-40` | localStorage session CRUD |
| **类型化客户端 (Sandbox)** | `web/src/api/client.ts:191-214` | sandboxApi |
| **类型化客户端 (Template)** | `web/src/api/client.ts:216-251` | templateApi |
| **类型化客户端 (Cluster)** | `web/src/api/client.ts:257-268` | clusterApi |
| **类型化客户端 (Auth)** | `web/src/api/client.ts:413-420` | authApi |
| **类型化客户端 (AgentHub)** | `web/src/api/client.ts:453-575` | agentHubApi |
| **生成类型** | `web/src/api/generated/schema.ts` | OpenAPI 生成类型 |
| **登录页** | `web/src/pages/Login.tsx:14-107` | 登录表单 + 错误处理 |
| **概览页** | `web/src/pages/Overview.tsx:14-224` | 集群 KPIs + 最近沙箱 |
| **沙箱列表** | `web/src/pages/Sandboxes.tsx:22-231` | 搜索/过滤/CRUD |
| **创建沙箱** | `web/src/pages/SandboxNew.tsx:173-261` | 模板选择 + metadata |
| **沙箱详情** | `web/src/pages/SandboxDetail.tsx:34-80` | 详情 + 日志 + 操作 |
| **模板管理** | `web/src/pages/Templates.tsx:567-838` | 创建/删除/兼容矩阵 |
| **节点列表** | `web/src/pages/Nodes.tsx:14-140` | 健康/资源使用率 |
| **API 密钥** | `web/src/pages/Keys.tsx:11-62` | localStorage 存储 |
| **AgentHub** | `web/src/pages/AgentHub.tsx:135-2461` | Agent CRUD + 快照 + 克隆 |
| **系统设置** | `web/src/pages/Settings.tsx:459-487` | 主题/语言/集群/账户 |
| **主题 Store** | `web/src/store/theme.ts:30-38` | Zustand theme store |
| **UI Store** | `web/src/store/ui.ts:12-16` | Zustand command palette |
| **Agent Store** | `web/src/state/agentStore.ts` | Agent 全局状态 |
| **i18n 初始化** | `web/src/i18n/index.ts:4-40` | i18next + 语言检测 |
| **全局样式** | `web/src/styles/globals.css:1-239` | HSL 变量 + 暗色/亮色主题 |
| **Vite 配置** | `web/vite.config.ts:1-48` | 代理 + 版本注入 |
| **Tailwind 配置** | `web/tailwind.config.js:1-112` | 主题 + 颜色 + 字体 |
| **TypeScript 配置** | `web/tsconfig.json:1-23` | 严格模式 |
| **MSW Worker** | `web/src/mocks/browser.ts:4-19` | dev mock 初始化 |
| **Mock 开关** | `web/src/lib/mockFlag.ts:21-35` | Mock 启用检测 |

---

## 8. 组件树与布局

```
<React.StrictMode>
  <QueryClientProvider>
    <ThemeProvider>
      <BrowserRouter>
        <Routes>
          /login  →  <LoginPage />
          <AuthGuard>
            <AppShell>
              <Rail />              {/* 左侧固定导航栏 */}
              <main>
                <TopBar />          {/* 顶部栏 */}
                <Outlet />          {/* 页面内容 */}
              </main>
              <CommandPalette />    {/* Cmd+K 命令面板 */}
              <ToastProvider />     {/* Toast 通知 */}
              <HotkeyMount />       {/* 全局快捷键 */}
            </AppShell>
          </AuthGuard>
        </Routes>
      </BrowserRouter>
    </ThemeProvider>
  </QueryClientProvider>
</React.StrictMode>
```

**Rail 导航栏项**:

来源: `web/src/components/Rail.tsx:23-35`

| 顺序 | 路径 | 图标 | i18n key | 说明 |
|------|------|------|----------|------|
| 1 | `/` | LayoutDashboard | overview | 概览 |
| 2 | `/sandboxes` | Boxes | sandboxes | 沙箱 |
| 3 | `/templates` | Package | templates | 模板 |
| 4 | `/nodes` | Server | nodes | 节点 |
| 5 | `/versions` | Layers | versions | 版本 |
| 6 | `/network` | Network | network | 网络 |
| 7 | `/observability` | Activity | observability | 可观测性 |
| 8 | `/keys` | KeyRound | apiKeys | API 密钥 |
| 9 | `/store` | Store | store | 模板商店 |
| 10 | `/agenthub` | Bot | agentHub | AI Agent |
| 11 | `/settings` | Settings | settings | 设置 |

---

## 9. API 命名空间总览

来源: `web/src/api/client.ts`

### 9.1 sandboxApi

| 方法 | API 调用 | 用途 |
|------|----------|------|
| `list(params?)` | `GET /v2/sandboxes` | 列沙箱 |
| `get(id)` | `GET /sandboxes/:id` | 沙箱详情 |
| `kill(id)` | `DELETE /sandboxes/:id` | 销毁 |
| `pause(id)` | `POST /sandboxes/:id/pause` | 暂停 |
| `resume(id, body)` | `POST /sandboxes/:id/resume` | 恢复 |
| `setTimeout(id, seconds)` | `POST /sandboxes/:id/timeout` | 设置超时 |
| `logs(id, params?)` | `GET /v2/sandboxes/:id/logs` | 日志 |
| `create(body)` | `POST /sandboxes` | 创建 |

### 9.2 templateApi

| 方法 | API 调用 | 用途 |
|------|----------|------|
| `list()` | `GET /templates` | 列模板 |
| `get(id)` | `GET /templates/:id` | 模板详情 |
| `create(body)` | `POST /templates` | 创建 |
| `rebuild(id)` | `POST /templates/:id` | 重建 |
| `remove(id)` | `DELETE /templates/:id` | 删除 |
| `compat()` | `GET /templates/compat` | 兼容矩阵 |
| `getBuildStatus(id, buildID)` | `GET /templates/:id/builds/:buildID/status` | 构建状态 |
| `getBuildLogs(id, buildID)` | `GET /templates/:id/builds/:buildID/logs` | 构建日志 |
| `adoptCompatBaseline(id)` | `POST /templates/compat/:id/adopt-baseline` | 采纳基线 |

### 9.3 clusterApi

| 方法 | API 调用 | 用途 |
|------|----------|------|
| `overview()` | `GET /cluster/overview` | 集群概览 |
| `nodes()` | `GET /nodes` | 节点列表 |
| `node(id)` | `GET /nodes/:id` | 节点详情 |
| `config()` | `GET /config` | 运行时配置 |

### 9.4 authApi

| 方法 | API 调用 | 用途 |
|------|----------|------|
| `session()` | `GET /auth/session` | 检查 session |
| `login(body)` | `POST /auth/login` | 登录 |
| `logout()` | `POST /auth/logout` | 登出 |
| `changePassword(body)` | `POST /auth/change-password` | 改密码 |

### 9.5 storeApi

| 方法 | API 调用 | 用途 |
|------|----------|------|
| `meta()` | `GET /store/meta` | 镜像元信息 |
| `refresh()` | `POST /store/refresh` | 刷新 store |

### 9.6 agentHubApi

| 方法 | API 调用 | 用途 |
|------|----------|------|
| `list()` | `GET /agenthub/instances` | 列 Agent 实例 |
| `create(body)` | `POST /agenthub/instances` | 创建 Agent |
| `delete(id)` | `DELETE /agenthub/instances/:id` | 删除 |
| `restart(id)` | `POST /agenthub/instances/:id/restart` | 重启 |
| `pause(id)` | `POST /agenthub/instances/:id/pause` | 暂停 |
| `resume(id)` | `POST /agenthub/instances/:id/resume` | 恢复 |
| `upgrade(id)` | `POST /agenthub/instances/:id/upgrade` | 升级 |
| `updateModel(id, body)` | `PUT /agenthub/instances/:id/model` | 更新模型 |
| `listSnapshots(id)` | `GET /agenthub/instances/:id/snapshots` | 列快照 |
| `createSnapshot(id, body)` | `POST /agenthub/instances/:id/snapshots` | 创建快照 |
| `deleteSnapshot(id, snapshotId)` | `DELETE .../snapshots/:snapshotId` | 删除快照 |
| `rollback(id, body)` | `POST .../:id/rollback` | 回滚 |
| `clone(id, body)` | `POST .../:id/clone` | 克隆 |
| `recover(id)` | `POST .../:id/recover` | 恢复 |
| `publishTemplate(id, body)` | `POST .../:id/publish-template` | 发布模板 |
| `getSettings()` | `GET /agenthub/settings` | Agent 设置 |
| `updateSettings(body)` | `PUT /agenthub/settings` | 更新设置 |

---

## 10. UI 主题系统

### 10.1 颜色系统

来源: `web/src/styles/globals.css`, `web/tailwind.config.js`

使用 HSL CSS 变量实现明暗双主题:

```css
/* 暗色 (默认) — Cube Midnight */
:root, .dark {
  --background: 215 35% 5%;        /* 深空黑 */
  --foreground: 210 40% 96%;       /* 亮白 */
  --primary: 220 85% 64%;          /* 蓝紫 */
  --cube-ok: 158 64% 50%;          /* 翠绿 */
  --cube-warn: 32 95% 60%;         /* 琥珀 */
  --cube-err: 0 72% 62%;           /* 玫瑰红 */
}

/* 亮色 — 暖白纸感 */
.light {
  --background: 40 20% 97%;        /* 象牙白 */
  --foreground: 20 15% 15%;        /* 墨色 */
  --primary: 212 55% 42%;          /* 深蓝 */
}
```

### 10.2 主题切换

来源: `web/src/store/theme.ts:6-38`

- 三种模式: `light` | `dark` | `system`
- 读取 `prefers-color-scheme` media query 实现 system 模式
- 切换时自动监听系统主题变化
- 持久化到 localStorage key `cube-theme`

---

## 11. 国际化系统

来源: `web/src/i18n/index.ts:4-40`

- **引擎**: i18next + react-i18next
- **检测**: i18next-browser-languagedetector (localStorage > navigator)
- **语言**: en (英语), zh (简体中文)
- **命名空间**: common, nav, topbar, command, overview, sandboxes, sandboxDetail, sandboxNew, templates, templateDetail, nodes, nodeDetail, network, keys, placeholder, settings, observability, store, agentHub, auth (共 20 个)
- **缓存**: localStorage key `cube.lang`
- **HTML lang**: 自动同步到 `<html lang>`

---

## 12. 开发辅助工具

### 12.1 MSW Mock

来源: `web/src/mocks/browser.ts`, `web/src/lib/mockFlag.ts`

- 开发环境 Mock Service Worker
- 启用方式:
  - `VITE_USE_MOCK=1` 环境变量
  - `?mock=1` URL query (自动持久化)
  - `localStorage.setItem('cube.useMock', '1')`
- 所有未匹配的请求 `bypass` 放行

### 12.2 命令面板

来源: `web/src/components/CommandPalette.tsx`

- 快捷键: Cmd+K / Ctrl+K
- 基于 `cmdk` 库实现
- 状态管理: Zustand `useCommandPaletteStore`

---

## 13. 总结: 架构设计与安全权衡

1. **现代 React 技术栈**: Vite 6 开发服务器 + TanStack Query 声明式数据获取 + Zustand 轻量状态管理，配合 shadcn 组件风格，兼顾开发效率和 UI 一致性。

2. **类型安全 API 客户端**: 通过 `openapi-typescript` 从 OpenAPI 规范自动生成 TypeScript 类型，实现端到端类型安全，减少运行时错误。

3. **客户端路由守卫**: AuthGuard 通过 `/auth/session` 端点检查认证状态，但仅为客户端安全措施。服务端 (CubeAPI) 对 WebUI 内部 API 的鉴权通过 `x-session-token` 独立实现。

4. **双重凭证注入**: API 调用同时携带 `X-API-Key` 和 `X-Session-Token`，由服务端决策使用何种认证方式，灵活支持 SDK 和 WebUI 两种使用场景。

5. **Token 明文存储 localStorage**: session token 和 API key 均以明文存储在 `localStorage` 中。这是 SPA 的常见做法 (非 HttpOnly cookie)，存在 XSS 窃取风险。

6. **国际化深度集成**: 20 个翻译命名空间覆盖所有页面，支持 en/zh 双语，语言偏好持久化到 localStorage。

7. **乐观更新模式**: AgentHub 的快照和克隆操作采用乐观占位 + 后台轮询模式，提升用户体验。

8. **预留可扩展性**: 可观测性和网络页面已预留路由，等待后端能力成熟后填充。

9. **Mock 系统**: 开发环境通过 MSW 实现前端模拟，支持通过环境变量/URL/localStorage 三种方式启用，可独立于后端进行开发和调试。
