---
title: "CubeProxy 架构、处理流程与安全配置"
date: 2026-07-08T00:00:00+08:00
draft: false
tags: ["cubesandbox"]
categories: ["cubesandbox"]
comments: true
showToc: true
weight: 100
---


> 调研时间: 2026/07/08
> 调研范围: `/home/zs/Develop/TraceCubeSandbox/CubeProxy/` 全量 Lua + Go 源码
> 目的: 系统性梳理 CubeProxy (数据面反向代理) 的架构、处理流程与安全配置

---

## 1. 概述

CubeProxy 是 CubeSandbox 的**面向客户端的数据面反向代理**，运行在 OpenResty (nginx + LuaJIT) 之上，负责将传入的 HTTP/HTTPS 请求路由到正确的 sandbox 容器后端。

| 属性 | 值 |
|------|-----|
| **语言** | Lua (数据面) + Go (sidecar 自动暂停/恢复) |
| **运行时** | OpenResty 1.21.4.1-6-alpine-fat |
| **sidecar** | Go 1.25.7 静态编译 (CGO_ENABLED=0, netgo+osusergo) |
| **监听端口** | 8080 (TLS) + 8081 (明文) |
| **管理 API** | 127.0.0.1:8082 (状态/元数据管理) |
| **sidecar HTTP** | 127.0.0.1:8083 (内部恢复端点) |
| **服务发现** | Redis (`cube:v1:shared:sandbox:proxy:*`) |
| **sidecar 流** | Redis Stream (`cube:v1:shared:sandbox:lifecycle:events`) |
| **上游服务** | CubeMaster (HTTP POST /cube/sandbox/update) |
| **构建方式** | Go sidecar 主机静态编译 + COPY 进 Docker 镜像 |

**核心职责**:
- 请求路由: 基于主机名 (`<port>-<sandbox_id>.cube.app`) 和路径 (`/sandbox/<id>/<port>/...`)
- 服务发现: Redis 查找 sandbox 后端地址 (HostIP + SandboxIP + 端口映射)
- TLS 终止 (端口 8080)
- 流量令牌验证 (AllowPublicTraffic 开关)
- 自动暂停/恢复 (Go sidecar)
- gRPC 流式端点支持 (无缓冲)
- 审计日志 + last_active 时间戳上报

---

## 2. 架构

### 2.1 目录结构

```
CubeProxy/
├── nginx.conf                           # 主配置 (数据面 + 管理 server)
├── start.sh                             # 容器入口 (sidecar + crond + nginx)
├── Dockerfile                           # 镜像构建
├── Makefile                             # 构建入口 (prebuild-sidecar + docker build)
├── root                                 # crond 日志轮转配置
├── rotate_nginx_log.sh                  # 日志轮转脚本
├── conf/includes/
│   ├── envd_streaming_host_route.inc    # gRPC 流式路由 (主机模式)
│   └── envd_streaming_path_route.inc    # gRPC 流式路由 (路径模式)
├── lua/
│   ├── utils.lua                        # 工具函数 (统一错误响应)
│   ├── redis_keys.lua                   # Redis 键约定 (命名空间 + 回退)
│   ├── redis_iresty.lua                 # Redis 客户端封装
│   ├── sandbox_backend.lua              # 后端解析 (Redis + 缓存 + 令牌验证)
│   ├── sandbox_state.lua                # 状态门控 (暂停检测 + 触发恢复)
│   ├── rewrite_phase.lua                # 主机名路由 (Host header 解析)
│   ├── path_rewrite_phase.lua           # 路径路由 (/sandbox/ 解析)
│   ├── balancer_phase.lua               # 负载均衡 (set_current_peer)
│   ├── header_filter_phase.lua          # 响应标头注入 (X-Cube-Request-Id)
│   ├── log_phase.lua                    # 访问日志 + last_active 更新
│   ├── admin_phase.lua                  # 管理 API 端点
│   └── init_worker_phase.lua            # Worker 初始化 (math.randomseed)
└── sidecar/                             # Go sidecar
    ├── go.mod
    ├── cmd/sidecar/main.go              # 入口 (组件装配 + 并行协程)
    └── internal/
        ├── config/config.go             # 配置加载 (环境变量 → struct)
        ├── lifecycle/schema.go          # Redis 键 + 事件结构 (与 CubeMaster 字节兼容)
        ├── registry/registry.go         # 沙箱注册表 (goroutine-safe map)
        ├── redisstream/stream.go        # Redis Stream 消费者 (XReadGroup)
        ├── cubemasterclient/client.go   # CubeMaster HTTP 客户端 (pause/resume/kill)
        ├── proxypush/client.go          # nginx 管理 API 推送 + 拉取
        ├── resumer/resumer.go           # 恢复逻辑 (合并并发 + SETNX 锁)
        ├── sweeper/sweeper.go           # 空闲扫描器 (pause/kill 决策)
        └── httpapi/server.go            # 内部 HTTP API (/internal/resume, /healthz, /readyz)
```

### 2.2 模块分层

```
┌─────────────────────────────────────────────────────────────────────────┐
│  HTTP 数据面 (nginx.conf + Lua 阶段)                                      │
│    • 8081 (明文) / 8080 (TLS) server                                      │
│    • rewrite_phase → sandbox_state.gate → sandbox_backend                 │
│    • balancer_phase → proxy_pass → header_filter → log_phase              │
├─────────────────────────────────────────────────────────────────────────┤
│  管理 API 层 (lua/admin_phase.lua)                                        │
│    • 127.0.0.1:8082 环回绑定                                              │
│    • meta/state/last_active 共享字典操作                                   │
├─────────────────────────────────────────────────────────────────────────┤
│  Sidecar HTTP (httpapi/server.go)                                         │
│    • 127.0.0.1:8083                                                       │
│    • /internal/resume → resumer.Resume()                                  │
├─────────────────────────────────────────────────────────────────────────┤
│  Sidecar 核心循环                                                        │
│    • redisstream — 消费者组消费 CubeMaster 发布的事件                      │
│    • sweeper — 每 5s 扫描 registry 判定空闲沙箱                           │
│    • resumer — 合并并发恢复请求                                           │
│    • pollLastActive — 轮询 /admin/last_active 拉取活跃时间戳              │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.3 与上下游服务关系

```
                ┌──────────────────────────────────────────────────┐
                │                 Client (SDK/curl)                │
                │    HTTP/HTTPS (host-based or path-based URL)     │
                └──────────┬───────────────────────┬───────────────┘
                           │                       │
                    8080 TLS                 8081 plain
                           ▼                       ▼
                ┌──────────────────────────────────────────┐
                │              CubeProxy                    │
                │  ┌─────────────────┐  ┌────────────────┐  │
                │  │  nginx + LuaJIT  │  │  Go sidecar     │  │
                │  │  (数据面)        │  │  (自动暂停/恢复) │  │
                │  └────────┬────────┘  └───────┬────────┘  │
                │           │                    │            │
                │      proxy_pass           admin:8082        │
                │           │             internal:8083        │
                └───────────┼────────────────────┼────────────┘
                            │                    │
                            ▼                    ▼
                     ┌─────────────┐    ┌─────────────────┐
                     │ Sandbox VM   │    │   CubeMaster    │
                     │ (envd 进程)   │    │  (HTTP pause/   │
                     └─────────────┘    │   resume/kill)   │
                                        └────────┬────────┘
                                                 │ Redis Stream +
                                                 │ HSet meta
                                                 ▼
                                        ┌─────────────────┐
                                        │     Redis        │
                                        │ (服务发现 +      │
                                        │  生命周期事件)    │
                                        └─────────────────┘
```

---

## 3. 请求处理流程

### 3.1 完整处理流程 (主机名路由)

```
Client                        CubeProxy                           Sandbox
  │                              │                                  │
  │ HTTP/HTTPS 请求               │                                  │
  │ Host: 49983-<id>.cube.app    │                                  │
  │ ────────────────────────────▶│                                  │
  │                              │                                  │
  │                              │ ① rewrite_phase.lua              │
  │                              │   - 解析 Host 头:                │
  │                              │     container_port = "49983"     │
  │                              │     ins_id = "7c8fbcd4..."       │
  │                              │   - 格式: <port>-<sandbox_id>   │
  │                              │                                  │
  │                              │ ② sandbox_state.lua:gate(ins_id) │
  │                              │   - 查 cube_sandbox_state 字典   │
  │                              │   - 如果 paused → 触发恢复       │
  │                              │   - 如果 pausing → 503           │
  │                              │   - 如果 killing/killed → 410    │
  │                              │                                  │
  │                              │ ③ sandbox_backend.lua             │
  │                              │   - resolve_backend(ins_id, port) │
  │                              │   - 查 local_cache 共享字典      │
  │                              │   - 缓存未命中 → HGETALL Redis   │
  │                              │   - 流量令牌校验                  │
  │                              │   - 本地 vs 远程路由决策         │
  │                              │                                  │
  │                              │ ④ balancer_phase.lua              │
  │                              │   - set_current_peer(back_ip,port)│
  │                              │                                  │
  │                              │ ⑤ proxy_pass → Sandbox 容器     │
  │                              │ ───────────────────────────────▶│
  │                              │ ◀─────────────────────────────── │
  │                              │                                  │
  │                              │ ⑥ header_filter_phase.lua        │
  │                              │   - X-Cube-Request-Id 回显       │
  │                              │                                  │
  │                              │ ⑦ log_phase.lua                  │
  │                              │   - 格式化访问日志               │
  │                              │   - 更新 cube_sandbox_last_active│
  │ ◀───────────────────────────│                                  │
```

**关键文件位置**:
- 主机名路由入口: `CubeProxy/lua/rewrite_phase.lua:7-10` (`parse_port_and_instance_from_host`)
- 路径路由入口: `CubeProxy/lua/path_rewrite_phase.lua:14` (`uri:match("^/sandbox/...")`)
- 状态门控: `CubeProxy/lua/sandbox_state.lua:36` (`gate()` 函数)
- 后端解析: `CubeProxy/lua/sandbox_backend.lua:119` (`resolve_backend()` 函数)
- 平衡器: `CubeProxy/lua/balancer_phase.lua:9` (`balancer.set_current_peer`)
- 标头注入: `CubeProxy/lua/header_filter_phase.lua:1` (`X-Cube-Request-Id`)
- 日志上报: `CubeProxy/lua/log_phase.lua:31-57` (last_active 写入)

### 3.2 路径路由子流程

当请求路径匹配 `/sandbox/<id>/<port>/...` 时，走路径路由:

```
① path_rewrite_phase.lua:14
   - uri:match("^/sandbox/([%w_%-]+)/(%d+)(/?.*)$")
   - 提取 ins_id, container_port, rest
   - ngx.var.ins_id = ins_id
   - ngx.var.container_port = container_port

② ngx.req.set_uri(rest, false)
   - 剥离 /sandbox/<id>/<port> 前缀
   - 不触发内部重定向

③ state.gate(ins_id) — 同主机名路由

④ sb.resolve_backend(ins_id, container_port) — 同主机名路由
```

路径路由额外处理:
- `proxy_redirect` 重写响应 Location 头: `~^/(.*)$` → `/sandbox/$ins_id/$container_port/$1`
- `proxy_cookie_path` 重写 cookie 路径: `/` → `/sandbox/$ins_id/$container_port/`
- 注入 `X-Forwarded-Prefix` 头供后端感知前缀

### 3.3 后端解析子流程

来源: `CubeProxy/lua/sandbox_backend.lua:119-205`

```
resolve_backend(ins_id, container_port):
  │
  ├─ ① 缓存路径 (local_cache 共享字典)
  │    - 查 cache_backend_ip_key + cache_backend_port_key
  │    - 如果命中 + meta_cached sentinel 存在:
  │       → 令牌校验 → 返回缓存
  │       → 刷新缓存 TTL
  │
  ├─ ② 缓存未命中 → load_sandbox_proxy_metadata(ins_id)
  │    - Redis 键: cube:v1:shared:sandbox:proxy:<id>
  │    - 回退键: bypass_host_proxy:<id> (兼容旧版)
  │    - HGETALL 获取 hash
  │    - 缓存到 local_cache (TTL 随机抖动)
  │
  ├─ ③ 令牌校验 (enforce_traffic_token)
  │    - AllowPublicTraffic == "false" → 需要令牌
  │    - 检查 e2b-traffic-access-token / cube-traffic-access-token
  │    - 不匹配 → 404 (不暴露存在)
  │
  ├─ ④ 路由决策
  │    - 如果 caller_host_ip == HostIP (同节点):
  │      → 后端 = SandboxIP:container_port (直连容器)
  │    - 如果跨节点:
  │      → 后端 = HostIP:metadata[container_port] (通过节点端口转发)
  │
  └─ ⑤ 写回缓存 → 返回 (host_ip, host_port)
```

### 3.4 gRPC 流式端点

三个已知的 gRPC 端点需无缓冲代理:

| 端点 | 用途 |
|------|------|
| `/process.Process/Start` | 启动进程 |
| `/process.Process/Connect` | 连接进程 (streaming) |
| `/filesystem.Filesystem/WatchDir` | 监听目录变更 |

流式路由配置 (`envd_streaming_host_route.inc` / `envd_streaming_path_route.inc`):
```nginx
proxy_buffering off;
proxy_intercept_errors off;   # 禁止 nginx 替换 gRPC 错误帧
```

nginx.conf 中匹配优先级:
1. 路径模式: `location ~ ^/sandbox/[^/]+/\d+/(?:process\.Process/(?:Start|Connect)|filesystem\.Filesystem/WatchDir)$`
2. 主机模式: `location ~ ^/(?:process\.Process/(?:Start|Connect)|filesystem\.Filesystem/WatchDir)$`
3. 通用前端: `location /sandbox/` / `location /`

---

## 4. 路由与端点

### 4.1 nginx 数据面

#### 4.1.1 server 块

| server | 端口 | 协议 | 用途 |
|--------|------|------|------|
| `server _` | 8081 | HTTP | 明文数据面 |
| `server _` | 8080 | HTTPS (SSL) | TLS 数据面 (证书: `cube.app+3.pem`) |
| `server _` | 127.0.0.1:8082 | HTTP | 管理 API (环回绑定) |

#### 4.1.2 数据面 location

来源: `CubeProxy/nginx.conf:103-345`

| location 模式 | 端口 | 路由方式 | 特性 | 流式 |
|---------------|------|---------|------|------|
| `~ ^/sandbox/[^/]+/\d+/(?:process\.Process/(?:Start\|Connect)\|filesystem\.Filesystem/WatchDir)$` | 8080/8081 | 路径 | 无缓冲,不拦截错误 | ✅ |
| `/sandbox/` | 8080/8081 | 路径 | 缓冲,proxy_redirect,cookie 重写 | ❌ |
| `~ ^/(?:process\.Process/(?:Start\|Connect)\|filesystem\.Filesystem/WatchDir)$` | 8080/8081 | 主机 | 无缓冲,不拦截错误 | ✅ |
| `/` (默认) | 8080/8081 | 主机 | 缓冲,rewrite_phase.lua | ❌ |
| `= /_sidecar_resume` | 8080/8081 | 内部 | `internal` 指令限制 | - |

所有数据面 location 共享:
```
proxy_http_version 1.1;
proxy_set_header Upgrade $http_upgrade;
proxy_set_header Connection "upgrade";     # WebSocket 支持
proxy_send_timeout 7206s;                  # 2h 超时
proxy_read_timeout 7206s;
proxy_connect_timeout 3s;
proxy_pass http://backend;                 # → balancer_phase.lua
```

#### 4.1.3 数据面路由匹配优先级

来源: `CubeProxy/nginx.conf` location 顺序

1. `= /_sidecar_resume` (精确匹配,internal)
2. `~ ^/sandbox/.../process.Process/...` (正则,流式)
3. `/sandbox/` (前缀匹配,路径路由)
4. `~ ^/process.Process/...` (正则,主机名模式流式)
5. `/` (前缀匹配,主机名路由)
6. 默认 404

### 4.2 管理 API (127.0.0.1:8082)

来源: `CubeProxy/lua/admin_phase.lua`

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/admin/meta/upsert` | 创建/更新 sandbox 元数据 (JSON 编码 → cube_sandbox_meta) |
| POST | `/admin/meta/delete` | 删除元数据 + 状态 + last_active |
| POST | `/admin/state` | 更新 sandbox 状态 (running/pausing/paused) |
| GET | `/admin/last_active` | 查询所有 last_active (支持 ?since= 增量) |
| GET | `/admin/healthz` | 健康检查 (含共享字典剩余空间) |

管理 API 可选令牌验证 (`$cube_admin_token` + `X-Cube-Admin-Token` header),不配时全放行。

### 4.3 Sidecar 内部 API (127.0.0.1:8083)

来源: `CubeProxy/sidecar/internal/httpapi/server.go`

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/internal/resume?sandbox_id=...&request_id=...` | 内部恢复 (ngx.location.capture 调用) |
| GET | `/healthz` | 健康检查 |
| GET | `/readyz` | 就绪检查 (含 registry 数量) |

WriteTimeout 35s (大于 nginx proxy_read_timeout 30s 的保底)。

---

## 5. 自动暂停/恢复 (Go sidecar)

### 5.1 架构

```
Redis Stream                      Sidecar                        nginx
  │                                  │                            │
  │ 生命周期事件 (create/delete)       │                            │
  │ ────────────────────────────────▶│                            │
  │                                  │                            │
  │ ┌─────────────────────────────────────────────────────┐       │
  │ │ ① redisstream 消费者 (consumeStream)                │       │
  │ │   - XReadGroup 消费 cube:v1:shared:...:events      │       │
  │ │   - Bootstrap: HGETALL cube:v1:shared:...:meta      │       │
  │ │   - handleEvent: create → Upsert+push               │       │
  │ │                  delete → Delete+push                │       │
  │ │                  update → Upsert+push+reset active   │       │
  │ │   - Ack 每个已处理事件                               │       │
  │ └─────────────────────────────────────────────────────┘       │
  │                                  │                            │
  │ ┌─────────────────────────────────────────────────────┐       │
  │ │ ② sweeper 扫描器 (sweepOnce / 5s)                   │       │
  │ │   - Registry.Snapshot() 获取全量沙箱                 │       │
  │ │   - 计算 idle = now - max(LastActiveMs, CreatedAt)  │       │
  │ │   - 预热期 (BootstrapWarmup=30s) 跳过引导写入的      │       │
  │ │   - AutoPause=true + 超时 → tryPause()               │       │
  │ │   - AutoPause=false + 超时 → tryKill()               │       │
  │ │     (timeout-kill: 原因 "timeout")                   │       │
  │ └─────────────────────────────────────────────────────┘       │
  │                                  │                            │
  │ ┌─────────────────────────────────────────────────────┐       │
  │ │ ③ resumer 恢复器                                    │       │
  │ │   - Resumer.Resume() 合并并发 (map[string]*call)    │       │
  │ │   - 首个请求驱动 CubeMaster.Resume()                 │       │
  │ │   - 后续请求等待第一个完成 (channel)                  │       │
  │ │   - SETNX 状态锁防冲突                               │       │
  │ │   - 成功后写 Redis "running" + 推 nginx + 更新 registry│     │
  │ └─────────────────────────────────────────────────────┘       │
  │                                  │                            │
  │ ┌─────────────────────────────────────────────────────┐       │
  │ │ ④ proxypush 推送客户端                               │       │
  │ │   - UpsertMeta → POST /admin/meta/upsert             │       │
  │ │   - DeleteMeta → POST /admin/meta/delete             │       │
  │ │   - SetState → POST /admin/state                     │       │
  │ │   - PullLastActive → GET /admin/last_active?since=   │       │
  │ │   - 广播到所有 CubeProxy 端点                        │       │
  │ └─────────────────────────────────────────────────────┘       │
  │                                  │                            │
  │ ┌─────────────────────────────────────────────────────┐       │
  │ │ ⑤ pollLastActive (5s)                               │       │
  │ │   - 从每个 CubeProxy 拉取 last_active                 │       │
  │ │   - Merged(ts) → registry.MergeLastActive()          │       │
  │ │   - min(now) 作为下次 since 水位线                    │       │
  │ └─────────────────────────────────────────────────────┘       │
```

### 5.2 状态机

```
                  ┌──────────┐
                  │ running  │ ←───── 正常服务中
                  └────┬─────┘
                       │
           idle 超时 (sweeper)
                       │
                  ┌────▼─────┐
                  │ pausing  │ ←───── SETNX 锁定中
                  └────┬─────┘
                       │
               CubeMaster Pause()
                       │
                  ┌────▼─────┐
                  │  paused  │ ←───── 暂停完成
                  └────┬─────┘
                       │
             client 请求触发 (resumer)
                       │
                  ┌────▼─────┐
                  │ resuming │ ←───── SETNX 锁定中
                  └────┬─────┘
                       │
               CubeMaster Resume()
                       │
                  ┌────▼─────┐
                  │ running  │ ←───── 恢复完成 (循环)
                  └──────────┘

   非 auto_pause 沙箱:
    running ──(idle 超时)──▶ killing ──(CubeMaster Kill)──▶ killed ──▶ 删除
```

### 5.3 空闲检测参数

来源: `CubeProxy/sidecar/internal/config/config.go:68-86`

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `DefaultIdleTimeout` | 5m | 空闲超时 (AutoPause 沙箱触发暂停,非 AutoPause 触发终止) |
| `IdleSweepInterval` | 5s | 扫描器周期 |
| `LastActivePoll` | 5s | last_active 拉取周期 |
| `BootstrapWarmup` | 30s | 启动预热期 (避免刚启动就误判) |
| `StateLockTTL` | 60s | SETNX 锁 TTL |

空闲计算逻辑:
```
baseline = max(LastActiveMs, CreatedAt, FirstSeenAt)
timeout  = Meta.TimeoutSeconds 或 DefaultIdleTimeout
idle_for = nowMs - baseline
if idle_for >= timeout → pause (auto_pause) or kill (non-auto_pause)
```

### 5.4 sidecar 启动流程

来源: `CubeProxy/sidecar/cmd/sidecar/main.go:36-128`

```
① config.Load() — 环境变量 → Config struct
② config.Validate() — 必填项检查
③ redis.NewClient() — 连接 Redis
④ bootstrap(): HGETALL 读取所有 meta → registry + push to CubeProxy
   - 设 FirstSeenAt = startupTs (用于预热门控)
⑤ stream.EnsureGroup(): 创建消费者组 (MKSTREAM)
⑥ 构造组件: resumer, sweeper, httpapi
⑦ 并发启动 4 个 goroutine:
   - consumeStream(): XReadGroup 事件循环
   - pollLastActive(): 轮询 last_active
   - sweep.Run(): 空闲扫描
   - apiSrv.Run(): HTTP 服务 (127.0.0.1:8083)
⑧ 首个 error 取消 context, 等待其他 goroutine 退出
```

### 5.5 事件处理

来源: `CubeProxy/sidecar/cmd/sidecar/main.go:201-259`

| 事件 | Op | 处理 |
|------|----|------|
| create | `lifecycle.OpCreate` | `reg.Upsert(meta)` + `push.UpsertMeta(ctx, meta)` |
| delete | `lifecycle.OpDelete` | `reg.Delete(sid)` + `push.DeleteMeta(ctx, sid)` |
| update | `lifecycle.OpUpdate` | `reg.Upsert(meta)` + `reg.ResetLastActive(sid)` + `push.UpsertMeta(ctx, meta)` |

---

## 6. 流量令牌验证

来源: `CubeProxy/lua/sandbox_backend.lua:26-44`

```lua
local function enforce_traffic_token(allow_public, expected_token, ins_id)
    if allow_public ~= "false" then
        return  -- 公开沙箱不验证
    end
    if utils:is_null(expected_token) then
        ngx.log(ngx.ERR, "LEVEL_ERROR||",
            string.format("request %s sandbox %s marked restricted but token missing in metadata",
                ngx.var.http_x_cube_request_id, ins_id))
        utils:respond_not_found()
    end
    local provided = ngx.var.http_e2b_traffic_access_token
                  or ngx.var.http_cube_traffic_access_token
    if not provided or provided ~= expected_token then
        ngx.log(ngx.ERR, "LEVEL_WARN||",
            string.format("request %s sandbox %s traffic token mismatch",
                ngx.var.http_x_cube_request_id, ins_id))
        utils:respond_not_found()
    end
end
```

**关键行为**:
1. `AllowPublicTraffic` = `"false"` 时启用令牌验证
2. 支持两个 header: `e2b-traffic-access-token` (E2B 兼容) / `cube-traffic-access-token` (原生)
3. 令牌不匹配 → **返回 404**,不暴露 sandbox 是否存在
4. 令牌在 Redis 元数据中明文存储 (`AllowPublicTraffic` + `TrafficAccessToken` 字段)
5. 缓存路径: 即使缓存命中,也要重新校验令牌 (`sandbox_backend.lua:137-146`)
6. 旧版元数据无 `AllowPublicTraffic` 字段 → 默认公开 (向后兼容)

---

## 7. 安全机制

| # | 特性 | 位置 | 说明 |
|---|------|------|------|
| S1 | **流量令牌** | sandbox_backend.lua | AllowPublicTraffic=false → 需令牌,404 隐藏 sandbox |
| S2 | **管理环回绑定** | nginx.conf | `listen 127.0.0.1:8082` 仅本地可访问 |
| S3 | **internal 子请求** | nginx.conf | `/= /_sidecar_resume` 标记 `internal`,外部无法直接调用 |
| S4 | **server_tokens off** | nginx.conf | 隐藏 nginx 版本号 |
| S5 | **TLS 终止** | nginx.conf | 端口 8080 SSL,证书 `cube.app+3.pem` |
| S6 | **统一错误响应** | utils.lua | 400/404/503 统一 JSON,不泄露内部细节 |
| S7 | **SETNX 状态锁** | sidecar/redisstream | 防并发 pause/resume 操作 |
| S8 | **sidecar 令牌可选** | admin_phase.lua | `X-Cube-Admin-Token` header 验证 |
| S9 | **sidecar 环回绑定** | config.go 默认值 | `127.0.0.1:8083` 仅本地 |
| S10 | **Go 静态编译** | Makefile | `CGO_ENABLED=0`,减少攻击面 |
| S11 | **Redis 回退键** | redis_keys.lua | 新旧键双读,迁移期间不暴露 |
| S12 | **缓存 TTL 抖动** | sandbox_backend.lua:46-48 | `math.random(timeout_min, timeout_max)` 防雪崩 |

---

## 8. 配置项

### 8.1 nginx 共享字典

来源: `CubeProxy/nginx.conf:83-98`

| 字典名 | 大小 | 用途 |
|--------|------|------|
| `local_cache` | 100m | Redis 后端元数据缓存 (TTL 随机抖动) |
| `cube_sandbox_meta` | 16m | sandbox 元数据 (JSON,~512B/条) |
| `cube_sandbox_state` | 4m | sandbox 状态 (running/pausing/paused) |
| `cube_sandbox_last_active` | 8m | 最后活跃时间戳 (Unix ms) |

### 8.2 nginx 全局配置

来源: `CubeProxy/nginx.conf`

| 配置项 | 值 | 说明 |
|--------|-----|------|
| worker_processes | 12 | worker 进程数 |
| worker_connections | 100000 | 每 worker 连接数 |
| client_max_body_size | 256M | 请求体上限 |
| proxy_send_timeout | 7206s | 2h 发送超时 |
| proxy_read_timeout | 7206s | 2h 读取超时 |
| proxy_connect_timeout | 3s | 连接超时 |
| keepalive_requests | 10000 | 长连接最大请求数 |
| upstream keepalive | 1500 | 后端连接池 |
| large_client_header_buffers | 4 512k | Header 大小 |

### 8.3 Sidecar 环境变量

来源: `CubeProxy/sidecar/internal/config/config.go:19-64`

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `CUBE_SIDECAR_REDIS_ADDR` | 127.0.0.1:6379 | Redis 地址 |
| `CUBE_SIDECAR_REDIS_PASSWORD` | "" | Redis 密码 |
| `CUBE_SIDECAR_REDIS_DB` | 0 | Redis DB |
| `CUBE_SIDECAR_PROXY_ADMIN_URLS` | http://127.0.0.1:8082 | CubeProxy 管理端点 (逗号分隔) |
| `CUBE_SIDECAR_ADMIN_TOKEN` | "" | 推送到 nginx 的共享令牌 |
| `CUBE_SIDECAR_CUBEMASTER_URL` | http://127.0.0.1:8089 | CubeMaster HTTP 地址 |
| `CUBE_SIDECAR_LISTEN_ADDR` | 127.0.0.1:8083 | sidecar HTTP 监听地址 |
| `CUBE_SIDECAR_DEFAULT_IDLE_TIMEOUT` | 5m | 默认空闲超时 |
| `CUBE_SIDECAR_LAST_ACTIVE_POLL` | 5s | last_active 轮询间隔 |
| `CUBE_SIDECAR_IDLE_SWEEP_INTERVAL` | 5s | 空闲扫描间隔 |
| `CUBE_SIDECAR_BOOTSTRAP_WARMUP` | 30s | 启动预热时间 |
| `CUBE_SIDECAR_STATE_LOCK_TTL` | 60s | 状态锁 TTL |
| `CUBE_SIDECAR_CONSUMER_NAME` | hostname | Redis 消费者名 |

### 8.4 Redis 键约定

来源: `CubeProxy/lua/redis_keys.lua` + `CubeProxy/sidecar/internal/lifecycle/schema.go`

| 键 | 类型 | 用途 |
|----|------|------|
| `cube:v1:shared:sandbox:proxy:<id>` | Hash | 路由元数据 (新) |
| `bypass_host_proxy:<id>` | Hash | 路由元数据 (旧,回退) |
| `cube:v1:shared:sandbox:lifecycle:meta` | Hash | 生命周期元数据集 |
| `cube:v1:shared:sandbox:lifecycle:events` | Stream | 生命周期事件流 |
| `cube:v1:shared:sandbox:lifecycle:state:<id>` | String | 暂停/恢复状态锁 |

事件流字段:
| 字段 | 说明 |
|------|------|
| `op` | 操作类型: `create` / `delete` / `update` |
| `sandbox_id` | 沙箱 ID |
| `payload` | JSON 序列化的 `SandboxLifecycleMeta` |
| `ts` | 时间戳 (ms) |

---

## 9. 关键文件清单

| 模块 | 路径 | 关键内容 |
|------|------|----------|
| **nginx 配置** | `CubeProxy/nginx.conf` | 数据面 server (8080/8081) + 管理 server (8082) + 共享字典 |
| **容器入口** | `CubeProxy/start.sh` | sidecar 守护进程 + crond + nginx 启动 |
| **Dockerfile** | `CubeProxy/Dockerfile` | openresty:1.21.4.1-6-alpine-fat + sidecar 二进制 COPY |
| **构建入口** | `CubeProxy/Makefile` | prebuild-sidecar (CGO_ENABLED=0 静态编译) + docker build |
| **统一错误响应** | `CubeProxy/lua/utils.lua` | respond_bad_request/not_found/unavailable |
| **Redis 键** | `CubeProxy/lua/redis_keys.lua` | 命名空间 + 新旧回退 |
| **后端解析** | `CubeProxy/lua/sandbox_backend.lua` | Redis → 缓存 → 令牌验证 → 路由决策 |
| **状态门控** | `CubeProxy/lua/sandbox_state.lua` | 暂停检测 + _sidecar_resume 子请求 |
| **主机路由** | `CubeProxy/lua/rewrite_phase.lua` | Host header 解析 `<port>-<id>.domain` |
| **路径路由** | `CubeProxy/lua/path_rewrite_phase.lua` | `/sandbox/<id>/<port>/...` 解析 |
| **平衡器** | `CubeProxy/lua/balancer_phase.lua` | set_current_peer |
| **响应标头** | `CubeProxy/lua/header_filter_phase.lua` | X-Cube-Request-Id |
| **审计日志** | `CubeProxy/lua/log_phase.lua` | 访问日志 + last_active 写入 |
| **管理 API** | `CubeProxy/lua/admin_phase.lua` | meta/state/last_active CRUD |
| **Worker 初始化** | `CubeProxy/lua/init_worker_phase.lua` | math.randomseed |
| **gRPC 流式 (主机)** | `CubeProxy/conf/includes/envd_streaming_host_route.inc` | 无缓冲流式路由配置 |
| **gRPC 流式 (路径)** | `CubeProxy/conf/includes/envd_streaming_path_route.inc` | 无缓冲 + path rewrite 配置 |
| **sidecar 入口** | `CubeProxy/sidecar/cmd/sidecar/main.go` | 组件装配 + 并行协程 + bootstrap |
| **sidecar 配置** | `CubeProxy/sidecar/internal/config/config.go` | 环境变量加载 + 校验 |
| **生命周期常量** | `CubeProxy/sidecar/internal/lifecycle/schema.go` | Redis 键 + 事件结构 (与 CubeMaster 同步) |
| **注册表** | `CubeProxy/sidecar/internal/registry/registry.go` | goroutine-safe Entry map |
| **Redis Stream** | `CubeProxy/sidecar/internal/redisstream/stream.go` | XReadGroup 消费者 + SETNX 锁 |
| **CubeMaster 客户端** | `CubeProxy/sidecar/internal/cubemasterclient/client.go` | HTTP pause/resume/kill |
| **代理推送** | `CubeProxy/sidecar/internal/proxypush/client.go` | 广播 push + merged pull |
| **恢复器** | `CubeProxy/sidecar/internal/resumer/resumer.go` | 合并并发 + 状态锁 + 等待 |
| **扫描器** | `CubeProxy/sidecar/internal/sweeper/sweeper.go` | 空闲检测 + pause/kill 决策 |
| **HTTP API** | `CubeProxy/sidecar/internal/httpapi/server.go` | /internal/resume + healthz |

---

## 10. 安全注意事项

| # | 风险 | 等级 | 说明 |
|---|------|------|------|
| R1 | **令牌值 Redis 明文存储** | 🟡 中 | `TrafficAccessToken` 在 Redis 元数据中明文,需 Redis ACL 保护 |
| R2 | **管理 API 令牌可选** | 🟡 中 | `$cube_admin_token` 可配空,未配时 127.0.0.1:8082 全放行 |
| R3 | **后端解析缓存可能过时** | 🟢 低 | `local_cache` 依赖 CubeMaster 事件推送更新,TTL 到期前可能不一致 |
| R4 | **sidecar 占用额外内存** | 🟢 低 | registry 保持全量 Entry 内存 + 共享字典 |
| R5 | **sidecar crond 背景运行** | 🟢 低 | start.sh 中 crond 后台运行,仅负责日志轮转 |
| R6 | **无全局速率限制** | 🟢 低 | 数据面未配置速率限制,依赖 CubeAPI 或外部 WAF |
| R7 | **client_max_body_size 256M** | 🟢 低 | 大请求体可能耗尽 worker 内存 |

---

## 11. 与 SVG 边界模型的关系

| SVG 边界 | CubeProxy 中的对应 |
|----------|------------------|
| **T5 (Data plane ingress)** | 整个 CubeProxy 进程 (nginx + sidecar) |
| **L1 (WebUI 域)** | 流量令牌验证 / TLS 终止 (端口 8080) |
| **L3 (host 进程域)** | nginx worker 进程 + sidecar 进程 |
| **L5 (文件系统域)** | nginx.conf + Lua 脚本文件只读挂载 |
| **L7 (可观测性域)** | 审计日志 (access.log) + last_active 上报 |
| **L8 (控制面域)** | sidecar → CubeMaster HTTP (pause/resume/kill) |

---

## 12. 总结

1. **双路由模式**: 主机名 (`<port>-<sandbox_id>.cube.app`) + 路径 (`/sandbox/<id>/<port>/...`) 灵活适配不同网络环境
2. **自动暂停/恢复**: Go sidecar 通过 Redis Stream 消费生命周期事件,空闲检测 → 自动暂停,请求触发 → 自动恢复,实现低成本资源回收
3. **SETNX 锁 + 合并恢复**: `cube:v1:shared:sandbox:lifecycle:state:<id>` 键实现跨 sidecar 并发控制,Resumer 合并同一沙箱的并发恢复请求
4. **404 隐藏 sandbox**: 令牌不匹配返回 404 (非 403),攻击者无法区分"沙箱不存在"和"令牌错误"
5. **缓存多层次**: Redis → local_cache (Lua 共享字典) → 状态字典,减少 Redis 压力
6. **gRPC 流式支持**: `proxy_buffering off` + `proxy_intercept_errors off` 保证流式端点正确
7. **预热保护**: BootstrapWarmup=30s 防止刚重启的 sidecar 误判已存在的活跃沙箱
8. **OpenResty + Go sidecar**: Lua 高性能数据面 + Go 强类型管理面,各取所长

---

## 13. 学习路线建议

| Phase | 重点研读章节 |
|-------|-------------|
| **Phase 0** (请求路由) | §3 处理流程 + §4 路由与端点 + rewrite_phase.lua / path_rewrite_phase.lua |
| **Phase 1** (自动暂停/恢复) | §5 自动暂停/恢复 + main.go + sweeper + resumer |
| **Phase 2** (安全机制) | §6 流量令牌 + §7 安全机制 + §10 安全注意事项 |
| **Phase 3** (生命周期事件流) | §5.4 启动流程 + redisstream + lifecycle schema |
| **Phase 4** (gRPC 流式) | §3.4 gRPC 流式 + envd_streaming_*.inc |
