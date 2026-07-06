---
title: "T1 CubeAPI ingress — 外部 → CubeAPI"
date: 2026-07-06T23:17:46+08:00
draft: false
tags: ["cubesandbox", "security", "T1"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/boundaries/t1-ingress/"
summary: "一句话定位: 公网用户 / LLM API / 第三方服务进入 CubeSandbox 的**唯一**访问入口。"
cover:
  image: ""
  alt: ""
  caption: ""
---
# T1 CubeAPI ingress — 外部 → CubeAPI

> 一句话定位: 公网用户 / LLM API / 第三方服务进入 CubeSandbox 的**唯一**访问入口。
> 边界类型: 真边界 (信任跃迁) —— 流量从不可信公网进入 host 信任域的第一道闸口。
> SVG 位置: x=340, y=145 (左上) ,T1 蓝框;纵向贯穿 L1 (WebUI 域)。

## 1. 边界概述

```
   ┌─────────────────────────────────────────────────────────┐
   │   External Internet (Untrusted)                         │
   │   公网用户 / LLM API / 第三方服务                       │
   └─────────────────────┬───────────────────────────────────┘
                         │ HTTPS + Bearer / x-session-token
                         ▼
            ╔═══════════════════════════════════╗
            ║   T1  CubeAPI ingress             ║  ← 本边界
            ║   auth callback + rate limit      ║
            ╚═══════════════╤═══════════════════╝
                            │ gRPC (mTLS)
                            ▼
                  L2 CubeMaster + Cubelet
```

**信任跃迁语义**: 进入 T1 前,所有调用者都被视为不可信;穿过 `unified_auth()` 中间件后,请求被标记为已认证的 host 信任域内部流量。WebUI 与外部 API 共用同一进程,但走两条独立路径: WebUI 用 `x-session-token` (DB session) ,外部 API 用 `Authorization: Bearer` 或 `X-API-Key` + 外部 callback。

## 2. 涉及的纵深防御层

| 层 | 名称 | 是否参与 | 在本边界的作用 |
|----|------|---------|--------------|
| L1 | WebUI 域 | ✅ | DB session 24h TTL、bearer / x-session-token 校验、客户端路由守卫 |
| L2 | 控制面域 | ✅ | gRPC 调用 CubeMaster、Redis 命名空间隔离、containerd namespace |
| L3 | host 进程域 | ✅ | CubeAPI 进程本身的 seccomp + capability drop + no_reaper |
| L4 | host 内核域 | ✅ | ingress 路径上的 eBPF 策略 (CubeVS)、TAP sysctl |
| L5 | guest OS 域 | ❌ | T1 不直接跨 T3 KVM 边界;后续请求经 L2/L3 后由 T3 处理 |
| L6 | 存储域 | ❌ | T1 边界本身不读写存储;仅转发 hostdir_mount 配置 (由 T2 校验) |
| L7 | 可观测性域 | ✅ | API 访问日志、auth callback 失败计数、rate-limit 命中 |

## 3. 机制清单

### 3.1 L1 (WebUI 域)

#### 机制: 外部 auth callback (unified_auth)

- **文件位置**: `CubeAPI/src/middleware/auth.rs:1-120`
- **作用**: 转发原始 credential + `X-Request-Path` + `X-Request-Method` 到外部 callback URL,callback 返回 200 即放行
- **配置/启用**: 启动参数 `--auth-callback-url https://your-auth-service/verify` 或环境变量 `AUTH_CALLBACK_URL`;**默认未设置时所有请求无认证放行**
- **与本边界的关联**: T1 的核心认证闸门;callback 校验 `X-Request-Path` + `X-Request-Method` 防止读权限提升到删/改

#### 机制: per-API-key 速率限制 (token bucket)

- **文件位置**: `CubeAPI/src/state.rs:42-50`,配置项 `CubeAPI/src/config/mod.rs:33-37`
- **作用**: token bucket per API key,默认 100 req/s,可通过 `RATE_LIMIT_PER_SEC` 环境变量调整
- **配置/启用**: `RATE_LIMIT_PER_SEC=200 ./cube-api` 或 `--rate-limit-per-sec 200`
- **与本边界的关联**: 在 T1 入口限速,防止单 API key 耗尽后端资源

#### 机制: WebUI DB-backed session

- **文件位置**: `CubeAPI/src/handlers/auth.rs:1-100`
- **作用**: 用户名/密码登录 → DB 存储的 opaque session token,通过 `x-session-token` header 传递,TTL 24h
- **配置/启用**: `POST /auth/change-password` 修改密码;`SESSION_HEADER = "x-session-token"` 硬编码,`SESSION_TTL_SECS = 24*60*60` 硬编码
- **与本边界的关联**: T1 中 WebUI 路径的认证;与 bearer token 是两条独立会话

#### 机制: /health 豁免

- **文件位置**: `CubeAPI/src/middleware/auth.rs` (auth 中间件判断) ,文档 `docs/guide/authentication.md:8-12`
- **作用**: `/health` 端点不被 auth callback 拦截,允许 LB 探活
- **配置/启用**: 硬编码
- **与本边界的关联**: T1 中唯一免认证的路径;**注意: 攻击者可滥用此端点做信息收集**,应限制为内部 LB 调用

### 3.2 L2 (控制面域)

#### 机制: CubeAPI → CubeMaster (gRPC)

- **文件位置**: `CubeAPI/src/cubemaster/mod.rs`、`CubeAPI/src/services/sandboxes.rs`
- **作用**: 沙箱创建/删除/查询请求经 gRPC (mTLS) 转发到 CubeMaster
- **配置/启用**: CubeMaster 地址在 `CubeAPI/src/config/mod.rs` 的 `cubemaster_addr` 字段
- **与本边界的关联**: T1 通过 L2 控制面域把"创建 sandbox"动作下发到 host 信任域

#### 机制: containerd namespace 隔离

- **文件位置**: `Cubelet/services/images/image_gc.go`、`Cubelet/services/cubebox/events.go`
- **作用**: containerd `namespaces.WithNamespace(ctx, ns)` 实现多租户逻辑隔离
- **配置/启用**: 通过 OCI spec `linux.namespaces[]` 配置
- **与本边界的关联**: T1 的 API key 落到 L2 后映射到 containerd namespace,实现租户隔离

### 3.3 L3 (host 进程域)

#### 机制: CubeAPI 进程的 seccomp + capability drop

- **文件位置**: `CubeAPI/Cargo.toml` (依赖 `seccompiler`)、启动入口 `CubeAPI/src/main.rs`
- **作用**: CubeAPI 进程自身被 seccomp 约束,只允许与 HTTP server / MySQL / Redis 通信所需的 syscall
- **配置/启用**: 编译期通过 `seccompiler` 库生成 BPF 过滤器,加载在主循环之前
- **与本边界的关联**: 即使 T1 失守让攻击者拿到 RCE,CubeAPI 进程被 seccomp 限制不能进一步提权

### 3.4 L4 (host 内核域)

#### 机制: CubeAPI 端口绑定 + sysctl

- **文件位置**: `CubeAPI/src/config/mod.rs:30-32` (`bind: "0.0.0.0:3000"` 默认)
- **作用**: 监听端口由 systemd / 容器编排固定,避免 CubeAPI 监听高位端口
- **配置/启用**: `bind` 字段启动时设置
- **与本边界的关联**: T1 的网络入口面

#### 机制: 共享 HTTP 客户端连接池

- **文件位置**: `CubeAPI/src/state.rs:50-55`
- **作用**: `reqwest::Client` 配置 `pool_max_idle_per_host=100`,防止外部 callback 调用耗尽 fd
- **配置/启用**: 硬编码
- **与本边界的关联**: T1 外部 callback 调用的资源上限控制 (本系列新增,原清单未列)

### 3.5 L7 (可观测性域)

#### 机制: API 访问日志

- **文件位置**: `CubeAPI/src/main.rs` (tracing/tracing-subscriber 配置) 、`/data/log/CubeMaster-dev/`
- **作用**: 每次 auth 成功/失败、rate-limit 命中、callback 调用均记录
- **配置/启用**: 默认开启,日志路径在 `CubeMaster/conf.yaml` 配置
- **与本边界的关联**: T1 的可观测性落点,异常模式可被 L7 的 audit 链路抓到

#### 机制: auth callback 失败计数

- **文件位置**: `CubeAPI/src/middleware/auth.rs:1-120` (错误返回路径) (本系列新增,原清单未列)
- **作用**: callback 返回 4xx/5xx 或超时时,记录失败次数,可触发 alert
- **配置/启用**: 默认开启
- **与本边界的关联**: T1 的失败指标,反映外部 auth 服务的健康度

## 4. 关键交互

- **数据流入自**: `External Internet` (公网,untrusted)
- **数据流出到**:
  - **T2 (Operator Trust)**: 当外部 API 调用"创建 sandbox"时,CubeAPI 通过 gRPC 把请求交给 CubeMaster,CubeMaster 加载 conf.yaml → 间接进入 T2 边界
  - **T3 (KVM CORE)**: 当 sandbox 实际启动时,经 L2/L3 创建 microVM,落到 T3
  - **T4 / T5**: T1 本身不直接出网,出网请求经 sandbox 内部 → T4;入站暴露经 CubeProxy → T5
- **同信任域 L 层依赖**: L1 (WebUI 域) → L2 (gRPC → CubeMaster) → L3 (CubeAPI 进程沙箱) → L4 (内核态隔离) → L7 (日志)

## 5. 设计权衡

1. **为什么 auth callback 委托外部**: CubeSandbox 自身不实现 OAuth/JWT,而是把"谁有权调用"的决策委托给外部服务。这避免了凭据管理在 CubeAPI 内,但代价是**默认无认证放行**——这是有意识的"基础设施不背锅默认",要求部署方必须显式开启。详见 SVG T1 警告。
2. **为什么 WebUI 与外部 API 共享进程**: 通过 session token 与 bearer token 两条路径隔离,既复用 HTTP server,又保持各自的会话状态。代价是 WebUI 客户端路由守卫**无服务端 middleware** (见清单.md §4.6)。
3. **为什么速率限制放在 L1 而非 L4**: L1 的 token bucket 是 per-key 的应用层语义,L4 的 eBPF 是 per-flow 的网络层语义。前者对合法用户友好 (突发流量正常),后者对恶意流量粗暴 (单流截断)。两层叠加是 CubeSandbox 的标准模式。
4. **为什么 `/health` 豁免**: kubelet/LB 探活必须绕过 auth。但这也意味着 `/health` 必须限制为内网可达,否则信息泄露面扩大。
5. **为什么 T1 不直接触及 L5/L6**: T1 是 host 信任域的边界,跨 T3 之前的所有数据流都在 host 侧;一旦进入 T3 才涉及 guest 侧机制。把 L5/L6 留给 T3 文档,本系列保持 T1 文档精练。