---
title: "CubeAPI 架构、处理流程与安全配置"
date: 2026-07-08T00:00:00+08:00
draft: false
tags: ["cubesandbox"]
categories: ["cubesandbox"]
comments: true
showToc: true
weight: 100
---


> 调研时间: 2026/07/07
> 调研范围: `/home/zs/Develop/TraceCubeSandbox/CubeAPI/` 全量 Rust 源码
> 目的: 系统性梳理 CubeAPI (CubeSandbox 的 HTTP 网关) 的架构、处理流程与安全配置
> 配套文档: [../安全边界/T1-cubeapi-ingress.md](../安全边界/T1-cubeapi-ingress.md) (边界视角,本文档是"内部视角")
>
> 每节都带文件位置证据,可以直接引用。

---

## 1. 概述

CubeAPI 是 CubeSandbox 项目的 **HTTP API 网关**,对外提供 E2B-兼容的 REST API,让客户端能创建/查询/销毁 sandbox。

| 属性 | 值 |
|------|-----|
| **语言** | Rust (异步运行时 `tokio`) |
| **Web 框架** | `axum` + `tower` (HTTP 路由 / 中间件) |
| **RPC 框架** | `ttrpc` (与下游 CubeMaster / Cubelet 通信) |
| **依赖 (Cargo.toml)** | `axum = "0.7"`, `tower-http`, `reqwest`, `utoipa`, `tokio`, `seccompiler` |
| **默认监听** | `0.0.0.0:3000` (可通过 `--bind` 改) |
| **上游服务** | CubeMaster (gRPC over ttrpc) |
| **目标兼容** | E2B OpenAPI (`templateID`, `sandboxID`, `envVars`, `metadata`) |

**核心职责**:
- HTTP 请求接入 + 鉴权 (auth callback / bearer / X-API-Key / session)
- 速率限制 (per-API-key token bucket)
- 沙箱生命周期管理 (create / list / get / kill / pause / resume / refresh)
- 模板管理 (templates CRUD)
- 快照管理 (snapshots / rollback)
- WebUI 内部 API (DB session + cluster / node / config 查看)
- 凭据注入 (envVars / metadata 转 annotations)
- OpenAPI 导出 (`--export-openapi` CLI flag)

---

## 2. 架构

### 2.1 顶层目录结构

```
CubeAPI/
├── Cargo.toml                    # 依赖 + 二进制 (cube-api)
├── src/
│   ├── main.rs                   # 入口 (CLI flag + tokio runtime)
│   ├── config/
│   │   └── mod.rs                # 配置结构 + env 解析
│   ├── routes.rs                 # axum 路由注册
│   ├── middleware/
│   │   ├── auth.rs               # unified_auth() 中间件
│   │   └── rate_limit.rs         # token bucket 速率限制
│   ├── handlers/                 # HTTP 端点实现
│   │   ├── sandboxes.rs          # /sandboxes CRUD
│   │   ├── templates.rs          # /templates CRUD
│   │   ├── snapshots.rs          # /snapshots
│   │   ├── auth.rs               # /auth/login + /auth/change-password
│   │   ├── config.rs             # /config (公开配置)
│   │   ├── cluster.rs            # /cluster/overview, /cluster/versions
│   │   ├── nodes.rs              # /nodes
│   │   ├── store.rs              # /store/meta, /store/refresh
│   │   ├── agenthub.rs           # /agenthub/instances|...
│   │   └── sandbox_logs.rs       # /sandboxes/:id/logs
│   ├── services/
│   │   ├── sandboxes.rs          # CreateSandboxRequest 组装 + 元数据处理
│   │   ├── templates.rs          # 模板转发逻辑
│   │   └── ...                   # 服务层 (handler → CubeMaster 转发)
│   ├── models/
│   │   └── mod.rs                # NewSandbox / Sandbox / OpenAPI schema
│   ├── cubemaster/
│   │   └── mod.rs                # ttrpc client 封装
│   ├── state.rs                  # AppState (rate limiter, http client, config)
│   ├── crypto.rs                 # password hashing (argon2?)
│   └── openapi.rs                # utoipa OpenAPI 导出
```

### 2.2 模块分层

```
┌──────────────────────────────────────────────────────────────────┐
│  HTTP 层 (axum routes + middleware)                                │
│    • routes.rs         — 路由注册                                  │
│    • middleware/auth    — unified_auth() 鉴权                      │
│    • middleware/rate_limit — token bucket                          │
├──────────────────────────────────────────────────────────────────┤
│  Handler 层 (业务端点)                                              │
│    • handlers/sandboxes, templates, snapshots, ...                 │
│    • 参数解析 + 响应序列化 + 错误码                                 │
├──────────────────────────────────────────────────────────────────┤
│  Service 层 (业务逻辑封装)                                          │
│    • services/sandboxes — CreateSandboxRequest 组装                │
│    • metadata["host-mount"] → annotations 重命名                   │
│    • envVars 校验                                                   │
├──────────────────────────────────────────────────────────────────┤
│  Client 层 (下游 RPC)                                              │
│    • cubemaster/mod.rs — ttrpc client to CubeMaster                │
│    • 调用 CreateSandboxRequest / GetSandboxRequest / ...           │
└──────────────────────────────────────────────────────────────────┘
```

### 2.3 与上下游服务关系

```
┌─────────────┐        HTTPS          ┌─────────────┐
│   Client    │ ─────────────────────▶│   CubeAPI   │
│  (SDK/curl) │ ◀─────────────────────│  (axum)     │
└─────────────┘     Bearer/JSON       └──────┬──────┘
                                             │ ttrpc (mTLS)
                                             ▼
                                     ┌──────────────┐
                                     │  CubeMaster  │
                                     │  (Go)        │
                                     └──┬───────┬───┘
                                        │       │
                                  ttrpc │       │ ttrpc
                                        ▼       ▼
                                   ┌────────┐ ┌────────┐
                                   │Cubelet │ │Cubelet │
                                   │ (node) │ │ (node) │
                                   └────────┘ └────────┘
```

CubeAPI 是**纯网关**,不直接与 Cubelet 通信,所有请求经 CubeMaster 转发。

---

## 3. 处理流程

### 3.1 创建 Sandbox (核心流程)

```
Client                CubeAPI                              CubeMaster
  │                      │                                      │
  │ POST /sandboxes      │                                      │
  │ Authorization: Bearer│                                      │
  │ {                    │                                      │
  │   templateID,        │                                      │
  │   metadata,          │                                      │
  │   envVars            │                                      │
  │ }                    │                                      │
  │ ────────────────────▶                                      │
  │                      │                                      │
  │                      │ ① middleware/unified_auth            │
  │                      │   - 提取 Bearer / X-API-Key          │
  │                      │   - 转发到 auth_callback_url         │
  │                      │   - callback 200 → 放行              │
  │                      │                                      │
  │                      │ ② middleware/rate_limit               │
  │                      │   - per-API-key token bucket         │
  │                      │   - 100 req/s 默认                    │
  │                      │                                      │
  │                      │ ③ handlers/sandboxes::create_sandbox  │
  │                      │   - 解析 NewSandbox                   │
  │                      │   - 校验 envVars                      │
  │                      │   - 调用 services::create_sandbox      │
  │                      │                                      │
  │                      │ ④ services/sandboxes::create_sandbox  │
  │                      │   - 提取 metadata["host-mount"]        │
  │                      │   - 重命名为 annotation "host-mount" │
  │                      │   - 构造 CreateSandboxRequest        │
  │                      │   - 调 cubemaster client              │
  │                      │ ────────────────────────────────────▶ │
  │                      │                                      │ ⑤ CubeMaster
  │                      │                                      │   scheduler + sandbox_run.go
  │                      │                                      │   - validateHostPath (host-mount)
  │                      │                                      │   - 调度 Cubelet 节点
  │                      │                                      │   - 创建 microVM
  │                      │                                      │   - 返回 Sandbox 响应
  │                      │ ◀──────────────────────────────────── │
  │                      │                                      │
  │ ◀────────────────────│ 201 Created                          │
  │ {                    │                                      │
  │   sandboxID,         │                                      │
  │   envdAccessToken,   │                                      │
  │   trafficAccessToken │                                      │
  │ }                    │                                      │
```

**关键文件位置**:
- 入口: `CubeAPI/src/routes.rs:116-326` (POST /sandboxes 注册)
- Handler: `CubeAPI/src/handlers/sandboxes.rs:158` (`create_sandbox` 函数)
- Service: `CubeAPI/src/services/sandboxes.rs:146-229` (CreateSandboxRequest 组装)
- 关键常量: `CubeAPI/src/services/sandboxes.rs:29` (`HOSTDIR_MOUNT_KEY = "host-mount"`)

### 3.2 host-mount 子流程

如果 `POST /sandboxes` 请求体含 `metadata.host-mount` (JSON 字符串),触发:

```
① CubeAPI services::create_sandbox:172-177
   - 从 metadata["host-mount"] 提取 JSON string
   - 转为 annotation "host-mount"
   - 写入 CreateSandboxRequest.Labels

② CubeMaster 收到请求
   - pkg/service/sandbox/sandbox_run.go 调用 hostdir_mount.go
   - injectHostDirMounts(req) 解析 annotation

③ hostdir_mount.go:114-124 validateHostPath(hostPath)
   - filepath.Clean 解析 ..
   - 检查是否落在 allowed_host_mount_prefixes 内
   - 不在白名单 → 500 internal error

④ 校验通过 → 注入到 req.Volumes + req.Containers[*].VolumeMounts
   - CubeMaster 调度 Cubelet
   - VM 启动时挂载 host 路径到 guest
```

**示例请求体**:

```json
{
  "templateID": "tpl-xxx",
  "metadata": {
    "host-mount": "[{\"hostPath\":\"/data/shared/rw\",\"mountPath\":\"/mnt/rw\",\"readOnly\":false}]"
  }
}
```

注意 `host-mount` 字段的值**必须是 JSON 字符串** (而非对象),Python SDK `examples/host-mount/create_with_mount.py:37` 显式 `json.dumps(...)`。

### 3.3 沙箱生命周期其他流程

| 操作 | 端点 | Handler | 关键逻辑 |
|------|------|---------|---------|
| **列表** | `GET /sandboxes` | `list_sandboxes` | 转发 CubeMaster `ListSandboxesRequest`,分页 |
| **详情** | `GET /sandboxes/:id` | `get_sandbox` | 转发 CubeMaster `GetSandboxRequest` |
| **销毁** | `DELETE /sandboxes/:id` | `kill_sandbox` | 转发 CubeMaster `DeleteSandboxRequest` |
| **日志** | `GET /sandboxes/:id/logs` | `get_sandbox_logs` | 转发 CubeMaster,流式响应 |
| **超时** | `POST /sandboxes/:id/timeout` | `set_sandbox_timeout` | 修改 sandbox metadata |
| **暂停/恢复** | `POST /sandboxes/:id/pause\|resume` | `pause_sandbox` / `resume_sandbox` | 转发 CubeMaster |
| **刷新** | `POST /sandboxes/:id/refreshes` | `refresh_sandbox` | 重置 TTL |
| **快照** | `POST /sandboxes/:id/snapshots` | `create_snapshot` (240s timeout) | 转发 CubeMaster |
| **回滚** | `POST /sandboxes/:id/rollback` | `rollback_sandbox` (240s timeout) | 转发 CubeMaster |
| **连接** | `POST /sandboxes/:id/connect` | `connect_sandbox` | 返回 vsock 连接信息 |

所有路由在 `CubeAPI/src/routes.rs` 注册。

---

## 4. 路由与端点

### 4.1 公网 API (E2B 兼容,根路径前缀)

来源: `CubeAPI/src/routes.rs:116-326`

| 方法 | 路径 | Handler | 鉴权 | 用途 |
|------|------|---------|------|------|
| `GET` | `/health` | `health_check` | ❌ 豁免 | LB 探活 |
| `GET` | `/sandboxes` | `list_sandboxes` | ✅ | 列出 sandbox |
| `POST` | `/sandboxes` | `create_sandbox` | ✅ | 创建 sandbox |
| `GET` | `/v2/sandboxes` | `list_sandboxes_v2` | ✅ | 列出 v2 |
| `GET` | `/sandboxes/:id` | `get_sandbox` | ✅ | 详情 |
| `DELETE` | `/sandboxes/:id` | `kill_sandbox` | ✅ | 销毁 |
| `GET` | `/sandboxes/:id/logs` | `get_sandbox_logs` | ✅ | 日志流 |
| `GET` | `/v2/sandboxes/:id/logs` | `get_sandbox_logs_v2` | ✅ | v2 日志 |
| `POST` | `/sandboxes/:id/timeout` | `set_sandbox_timeout` | ✅ | 设置超时 |
| `POST` | `/sandboxes/:id/refreshes` | `refresh_sandbox` | ✅ | 刷新 TTL |
| `POST` | `/sandboxes/:id/pause` | `pause_sandbox` | ✅ | 暂停 |
| `POST` | `/sandboxes/:id/resume` | `resume_sandbox` | ✅ | 恢复 |
| `POST` | `/sandboxes/:id/connect` | `connect_sandbox` | ✅ | 获取连接 |
| `POST` | `/sandboxes/:id/snapshots` | `create_snapshot` | ✅ (240s) | 创建快照 |
| `POST` | `/sandboxes/:id/rollback` | `rollback_sandbox` | ✅ (240s) | 回滚 |
| `GET` | `/snapshots` | `list_snapshots` | ✅ | 列快照 |
| `GET` | `/templates` | `list_templates` | ✅ | 列模板 |
| `POST` | `/templates` | `create_template` | ✅ | 创建模板 |
| `GET` | `/templates/:id` | `get_template` | ✅ | 详情 |
| `POST` | `/templates/:id` | `rebuild_template` | ✅ (240s) | 重建 |
| `PATCH` | `/templates/:id` | `update_template` | ✅ | 更新 |
| `DELETE` | `/templates/:id` | `delete_template` | ✅ (240s) | 删除 |
| `GET` | `/templates/:id/builds` | `list_template_builds` | ✅ | 列构建 |
| ... | `/templates/:id/builds/...` | ... | ✅ | 构建子资源 |

### 4.2 内部 API (WebUI/dashboard,前缀 `/cubeapi/v1`)

| 方法 | 路径 | 用途 |
|------|------|------|
| `GET` | `/cubeapi/v1/health` | dashboard 健康检查 |
| `POST` | `/cubeapi/v1/auth/login` | WebUI 登录 (rate-limited) |
| `POST` | `/cubeapi/v1/auth/change-password` | 改密码 (rate-limited) |
| `POST` | `/cubeapi/v1/auth/logout` | 登出 |
| `GET` | `/cubeapi/v1/auth/session` | 查 session |
| `GET` | `/cubeapi/v1/cluster/overview` | 集群概览 |
| `GET` | `/cubeapi/v1/cluster/versions` | 版本信息 |
| `GET` | `/cubeapi/v1/nodes` / `/nodes/:nodeID` | 节点信息 |
| `GET` | `/cubeapi/v1/config` | 查公开配置 |
| `GET` | `/cubeapi/v1/store/meta` | store 元信息 |
| `POST` | `/cubeapi/v1/store/refresh` | 刷新 store |
| `GET` | `/cubeapi/v1/agenthub/instances` | AgentHub 实例 |
| `POST` | `/cubeapi/v1/agenthub/instances/refresh` | 刷新 |
| `GET` | `/cubeapi/v1/agenthub/templates` / `/snapshots` | 模板/快照 |
| ... | `/agenthub/...` | AgentHub 子资源 |

内部 API 走 `x-session-token` header (DB session),与公网 API 鉴权方式不同。

---

## 5. 认证机制

### 5.1 三层鉴权

CubeAPI 的鉴权**委托外部**,不内置 OAuth/JWT。

```
Client → CubeAPI → [extracting credential] → External auth_callback_url
                         │                         │
                         │              callback 200? → 放行
                         │              callback 非 200 → 401
                         │
                         └── 提取: Authorization header (Bearer)
                                   X-API-Key header
                                   x-session-token (内部 API)
```

### 5.2 `unified_auth()` 中间件

来源: `CubeAPI/src/middleware/auth.rs:1-120`

**核心逻辑**:
```rust
fn extract_credential(req: &Request) -> Option<String> {
    req.headers().get("Authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.strip_prefix("Bearer "))
        .map(String::from)
        .or_else(|| {
            req.headers().get("X-API-Key")
                .and_then(|v| v.to_str().ok())
                .map(String::from)
        })
}

async fn unified_auth(req: Request, next: Next) -> Response {
    let cred = extract_credential(&req);
    let callback_url = state.config.auth_callback_url.as_deref();

    match callback_url {
        Some(url) => {
            // 转发到 callback,验证
            let resp = reqwest::Client::new()
                .post(url)
                .header("Authorization", cred.as_deref().unwrap_or(""))
                .header("X-Request-Path", req.uri().path())
                .header("X-Request-Method", req.method().as_str())
                .send().await?;

            if resp.status() == 200 { next.run(req).await }
            else { 401 }
        }
        None => {
            // 未配置 callback → 全部放行 (默认不安全!)
            next.run(req).await
        }
    }
}
```

**关键点**:
1. **Bearer 优先于 X-API-Key**: `extract_credential()` 先找 Bearer 头
2. **未配置 callback 时全放行**: 这是默认行为,部署时**必须**配置 `auth_callback_url` 否则公网可任意调用
3. **转发原始 credential**: 不验签,callback 必须自己验签
4. **转发 X-Request-Path + X-Request-Method**: 防止 callback 误授权 (读权限用户提权到删/改)

### 5.3 触发逻辑

来源: `CubeAPI/src/main.rs:60-73` (CLI flag)

```rust
let auth_callback_url = env::var("AUTH_CALLBACK_URL")
    .or_else(|_| cli_matches.value_of("auth-callback-url").map(String::from))
    .ok();

let rate_limit_per_sec = env::var("RATE_LIMIT_PER_SEC")
    .ok()
    .and_then(|s| s.parse().ok())
    .unwrap_or(100);
```

### 5.4 WebUI Session (内部 API 鉴权)

来源: `CubeAPI/src/handlers/auth.rs:1-100`

```
1. POST /cubeapi/v1/auth/login
   { "username": "admin", "password": "admin" }
   ↓
2. crypto.rs verify_password (argon2)
   ↓
3. 生成 opaque session token (uuid)
   ↓
4. 存储 (DB or memory) with TTL 24h
   ↓
5. 响应: { "token": "<uuid>", "username": "admin", "expiresInSecs": 86400 }
   ↓
6. 客户端后续请求带 -H "x-session-token: <uuid>"
   ↓
7. auth_required 中间件校验 (rate-limited on login + change-password)
```

**关键常量**: `CubeAPI/src/handlers/auth.rs:18-20`
```rust
const SESSION_HEADER: &str = "x-session-token";
const SESSION_TTL_SECS: i64 = 24 * 60 * 60;  // 24h
```

**默认账户**: 首次迁移时种子 `admin/admin` (见 `auth.rs:9-10` 注释)。

---

## 6. 速率限制

来源: `CubeAPI/src/state.rs:42-50` + `CubeAPI/src/config/mod.rs:33-37`

```rust
// state.rs
let quota = Quota::per_second(NonZeroU32::new(
    config.rate_limit_per_sec.max(1)
).unwrap());
let rate_limiter = Arc::new(RateLimiter::keyed(quota));

// config/mod.rs
#[serde(default = "default_rate_limit")]
pub rate_limit_per_sec: u32,

fn default_rate_limit() -> u32 { 100 }
```

**机制**:
- **算法**: token bucket (governor crate)
- **粒度**: per-API-key keyed (即不同 API key 独立 bucket)
- **默认**: 100 req/s per key
- **可调**: `RATE_LIMIT_PER_SEC` 环境变量 或 `--rate-limit-per-sec` CLI flag
- **共享 HTTP 客户端**: `reqwest::Client` 配置 `pool_max_idle_per_host=100` (防止 callback 调用耗尽 fd)

**路由挂载**:
- `/auth/login` + `/auth/change-password` 单独 rate limiter (`middleware/rate_limit.rs`)
- 其他路由仅在 `auth_callback_url` 配置时才挂 rate limit (`with_auth_and_rate_limit` 组合中间件)

---

## 7. 配置项

### 7.1 配置结构

来源: `CubeAPI/src/config/mod.rs`

```rust
pub struct Config {
    pub bind: String,                  // 默认 "0.0.0.0:3000"
    pub auth_callback_url: Option<String>,
    pub rate_limit_per_sec: u32,       // 默认 100
    pub cubemaster_addr: String,
    pub ...                             // 其他 RPC / TLS 配置
}
```

### 7.2 环境变量与 CLI flag

| 配置项 | 环境变量 | CLI flag | 默认值 | 备注 |
|--------|----------|----------|--------|------|
| bind | `CUBE_API_BIND` | `--bind` | `0.0.0.0:3000` | |
| auth_callback_url | `AUTH_CALLBACK_URL` | `--auth-callback-url` | None | ⚠️ 未设 → 全放行 |
| rate_limit_per_sec | `RATE_LIMIT_PER_SEC` | `--rate-limit-per-sec` | 100 | |
| cubemaster_addr | `CUBE_MASTER_ADDR` | `--cubemaster-addr` | localhost:8089 | ttrpc |
| log_level | `RUST_LOG` | `--log-level` | info | tracing |
| export_openapi | - | `--export-openapi <path>` | None | 一键导出 OpenAPI yml |

### 7.3 启动示例

```bash
# 最小 (不安全!)
./cube-api

# 推荐 (启用 auth callback + 限速)
AUTH_CALLBACK_URL=https://auth.example.com/verify \
RATE_LIMIT_PER_SEC=200 \
./cube-api --bind 0.0.0.0:3000

# 导出 OpenAPI
./cube-api --export-openapi ./openapi.yml
```

### 7.4 OpenAPI 导出

来源: `CubeAPI/src/main.rs:121-122` + `src/openapi.rs`

```bash
./cube-api --export-openapi /path/to/openapi.yml
```

仓库根的 `openapi.yml` 是 dashboard surface 的子集;完整 spec 用此 CLI 导出。

---

## 8. 关键文件清单

| 模块 | 路径 | 关键内容 |
|------|------|----------|
| **入口** | `CubeAPI/src/main.rs` | CLI flag + tokio runtime + 路由装配 |
| **路由** | `CubeAPI/src/routes.rs` | axum Router 装配 (~200 行) |
| **配置** | `CubeAPI/src/config/mod.rs` | Config 结构 + env 解析 |
| **状态** | `CubeAPI/src/state.rs` | AppState (rate limiter + http client) |
| **Auth** | `CubeAPI/src/middleware/auth.rs` | unified_auth() |
| **限速** | `CubeAPI/src/middleware/rate_limit.rs` | governor token bucket |
| **Models** | `CubeAPI/src/models/mod.rs:168-251` | NewSandbox, Sandbox schemas |
| **Sandbox handler** | `CubeAPI/src/handlers/sandboxes.rs:158` | create_sandbox |
| **Sandbox service** | `CubeAPI/src/services/sandboxes.rs:146-229` | CreateSandboxRequest 组装 |
| **Host-mount 常量** | `CubeAPI/src/services/sandboxes.rs:29` | `HOSTDIR_MOUNT_KEY = "host-mount"` |
| **Auth handler** | `CubeAPI/src/handlers/auth.rs` | login + change-password + session |
| **Auth 常量** | `CubeAPI/src/handlers/auth.rs:18-20` | SESSION_HEADER + SESSION_TTL_SECS |
| **Crypto** | `CubeAPI/src/crypto.rs` | verify_password |
| **CubeMaster client** | `CubeAPI/src/cubemaster/mod.rs` | ttrpc client |
| **OpenAPI** | `CubeAPI/src/openapi.rs` | utoipa 导出 |

---

## 9. 安全注意事项

### 9.1 已知实现缺陷 (来自 SVG 边界模型)

| # | 缺陷 | 位置 | 风险 |
|---|------|------|------|
| **C4** | auth callback 默认关闭 → 所有请求放行 | `CubeAPI/src/middleware/auth.rs:78-81` | 🔴 高 — 必须显式启用 |
| **C5** | WebUI admin/admin 默认 + 无服务端 middleware | `CubeAPI/src/handlers/auth.rs:6-9,98` | 🟠 中高 — 客户端守卫,服务端无校验 |

### 9.2 默认凭据警告

来源: `CubeAPI/src/handlers/auth.rs:9-10` 注释

- 默认 WebUI 账户 `admin/admin` (首次迁移种子)
- 部署后必须 `POST /cubeapi/v1/auth/change-password` 改密码
- 或通过 `auth.rs:auth_required: false` 配置让 WebUI 跑开放模式 (不推荐)

### 9.3 速率限制盲区

- 仅 `/auth/login` + `/auth/change-password` 单独 rate limit
- 其他公网 API **只在配置 auth callback 后才挂 rate limit**
- 未配 callback 时所有路由 (含 sandbox 创建) 无限速保护 — **DoS 风险**

### 9.4 信息泄露面

- `/health` 端点**不**走 auth 中间件 (`routes.rs:69-70`) — 攻击者可滥用做 fingerprint
- `auth_required: false` 时 WebUI 跑开放模式 (auth callback 未配置)
- 错误信息可能泄露内部细节 (e.g., `"hostPath '/etc/passwd' is not within an allowed mount prefix"`)

### 9.5 host-mount 输入校验

虽然 host-mount 校验在 CubeMaster 侧 (`validateHostPath`),但**输入解析** 在 CubeAPI:
- `services/sandboxes.rs:172-177` 提取 metadata["host-mount"]
- 必须是 JSON 字符串 (非对象)
- malformed JSON → 解析失败 → 500 错误
- **TOCTOU 风险**: 字串校验在 CubeMaster,VM 启动时 host 路径可能已被攻击者替换符号链接

---

## 10. 与 SVG 边界模型的关系

CubeAPI 是 SVG 中 **T1 (外部 → CubeAPI)** 真边界的关键执行点:

| SVG 边界 | CubeAPI 中的对应 |
|----------|------------------|
| T1 ingress | 整个 CubeAPI 进程 |
| L1 (WebUI 域) | `/cubeapi/v1/auth/login` + DB session |
| L2 (控制面域) | ttrpc → CubeMaster |
| L3 (host 进程域) | CubeAPI 进程本身 (seccomp + cap drop) |
| L4 (host 内核域) | ingress 路径 eBPF policy |
| L7 (可观测性域) | API 访问日志 + auth callback 失败计数 |

详细边界视角见 [security-boundaries/T1-cubeapi-ingress.md](../安全边界/T1-cubeapi-ingress.md)。

---

## 11. 总结:安全设计权衡

1. **委托外部 auth**: CubeAPI 不内置 OAuth/JWT,而是把"谁有权调用"的决策委托给外部服务。优点是灵活(支持任意 IDP),缺点是**默认无认证放行**。
2. **Bearer 优先于 X-API-Key**: 让 SDK 友好 (E2B 用 Bearer) 同时兼容简单 X-API-Key 集成。
3. **WebUI 客户端路由守卫**: WebUI 用 session token 但**服务端无中间件**校验路由 → 见 C5。
4. **路由分层**: 公网 API (E2B 兼容) + 内部 API (WebUI dashboard) 用不同前缀 `/cubeapi/v1/`,鉴权方式也不同。
5. **OpenAPI 一键导出**: 客户端 SDK 可基于导出 spec 自动生成,减少文档漂移。
6. **共享 HTTP 客户端**: `pool_max_idle_per_host=100` 防止 callback 调用耗尽 fd,但也意味着 callback 慢会阻塞其他请求。

---

## 12. 学习路线建议

| Phase | 重点研读章节 |
|-------|-------------|
| **Phase 0** (HTTP 接入) | §4 路由 + §5 认证 + §6 速率限制 |
| **Phase 1** (沙箱生命周期) | §3 处理流程 + §8 关键文件 (services/sandboxes.rs) |
| **Phase 2** (OpenAPI 兼容) | §7 配置 + §8 (models/mod.rs:168-251) |
| **Phase 3** (WebUI 集成) | §4.2 内部 API + §5.4 session |
| **Phase 4** (host-mount 安全) | §3.2 host-mount 子流程 + §9.5 TOCTOU |