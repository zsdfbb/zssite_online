---
title: "Cubelet 架构、处理流程与安全配置"
date: 2026-07-08T00:00:00+08:00
draft: false
tags: ["cubesandbox"]
categories: ["cubesandbox"]
comments: true
showToc: true
weight: 100
---


> 调研时间: 2026/07/08
> 调研范围: `/home/zs/Develop/TraceCubeSandbox/Cubelet/` 全量 Go 源码
> 目的: 系统性梳理 Cubelet (CubeSandbox 的节点代理) 的架构、处理流程与安全配置
> 配套文档: [security-boundaries/T3-cubelet.md](../安全边界/T3-cubelet.md) (边界视角,本文档是"内部视角")
>
> 每节都带文件位置证据,可以直接引用。

---

## 1. 概述

Cubelet 是 CubeSandbox 的**节点级代理**,运行在每个物理计算节点上。它是 containerd 的 fork 扩展版本,增加了 MicroVM sandboxing 支持。

| 属性 | 值 |
|------|-----|
| **语言** | Go 1.24.8 + Rust (cubecow 存储引擎通过 CGo 链接) |
| **运行时** | containerd 扩展版 (OCI兼容) |
| **RPC 协议** | ttrpc (与 CubeShim 通信) + gRPC (与 network-agent 通信) |
| **HTTP API** | 节点元数据上报 (与 CubeMaster) |
| **CLI 工具** | cubecli (管理/调试) |
| **存储后端** | cubecow (XFS reflink 写时复制引擎) |
| **网络后端** | TAP 设备 + eBPF TC 过滤器 |
| **配置** | TOML 配置文件 + YAML 动态热重载 |

**核心职责**:
- Sandbox 生命周期管理 (创建/销毁/更新/执行/快照)
- 镜像管理 (拉取/缓存/GC)
- 存储管理 (cubecow CoW 卷管理)
- 网络管理 (TAP 设备 + 网络插件)
- 节点注册与心跳 (向 CubeMaster 上报状态)
- 资源管理 (cgroups/CPU/内存/磁盘配额)
- 工作流编排 (workflow 引擎)
- 与 CubeShim 交互管理 KVM MicroVM

---

## 2. 架构

### 2.1 顶层目录结构

```
Cubelet/
├── cmd/
│   ├── cubelet/                    # cubelet 守护进程入口
│   │   ├── main.go                 # 入口 + CLI flags (+ 双重 fork mount ns)
│   │   ├── main_linux.go           # Linux mount/netns 绑定
│   │   ├── main_unix.go            # 信号处理 (SIGTERM/SIGINT/SIGUSR1)
│   │   ├── config.go               # 配置加载 + dump/migrate
│   │   ├── builtins.go             # 通过 blank import 注册所有内置插件
│   │   └── notify_linux.go         # systemd 就绪/停止通知
│   └── cubecli/                    # CLI 管理工具
├── services/
│   ├── server/                     # ttrpc/gRPC/HTTP/TCP 服务器装配
│   │   ├── server.go               # Server 结构体 + 装配
│   │   ├── config/config.go        # 配置结构 + TOML 加载
│   │   ├── operation.go            # HTTP 操作接口
│   │   ├── tap_provider.go         # TAP 设备 provider
│   │   └── plugins_compat.go       # 插件兼容层
│   ├── cubebox/                    # Cubebox 服务全局实现 (创建/销毁/快照/回滚等)
│   ├── images/                     # 镜像管理 + GC
│   ├── gc/                         # 通用 GC 服务
│   ├── nbi/                        # NBI 服务
│   └── version/                    # 版本服务
├── plugins/
│   ├── workflow/                   # 工作流引擎
│   │   ├── engine.go               # Flow 接口 + Engine 编排
│   │   └── plugin/plugin.go        # TOML→工作流注册
│   ├── cube/internals/
│   │   └── sandbox/                # Sandbox 管理器 (shim v2 controller)
│   │       ├── cube_sandbox_manager.go  # controllerLocal - containerd shim manager
│   │       └── cube_sandbox_store.go    # sandbox store 封装
│   ├── network/                    # 网络插件 (TAP 代理)
│   ├── storage/                    # 存储插件 (cubecow)
│   ├── cgroup/                     # cgroup 管理 (v1/v2)
│   ├── images/                     # 镜像插件
│   ├── backup/                     # 备份插件
│   ├── cbri/                       # CRI 实现
│   ├── chi/                        # vsocket-manager
│   ├── mount/                      # 挂载管理
│   ├── snapshots/                  # 快照插件
│   ├── metadata/                   # 元数据插件
│   ├── transfer/                   # 传输管理器
│   └── controller/                 # 控制器插件
├── pkg/
│   ├── cubelet/                    # 核心节点逻辑
│   │   ├── cubelet.go              # Cubelet 结构体 + NewCubelet()
│   │   └── node_status.go          # 节点状态上报 + 注册 + 心跳
│   ├── masterclient/               # CubeMaster HTTP 客户端
│   ├── container/                  # OCI 容器配置构建
│   │   ├── container.go            # GenOpt() - 默认 OCI spec
│   │   ├── capability/capability.go    # Linux capabilities 管理
│   │   ├── seccomp/seccomp.go          # Seccomp BPF 过滤配置
│   │   ├── uid/uid.go                  # UID/GID 映射
│   │   ├── rootfs/rootfs.go            # Rootfs 配置 + CoW 清理
│   │   ├── cgroup/cgroup.go            # cgroup 资源限制
│   │   ├── cpu/cpu.go                  # CPU 配置
│   │   ├── env/env.go                  # 环境变量
│   │   ├── command/command.go          # entrypoint/cmd
│   │   ├── tmpfs/tmpfs.go              # tmpfs 挂载配置
│   │   ├── sysctl/sysctl.go            # sysctl 设置
│   │   └── rlimit/rlimit.go            # rlimit 设置
│   ├── cubecow/                     # Rust CGo 存储桥接 (cubecow.h)
│   │   ├── cubecow.go              # CGo binding
│   │   ├── engine.go               # Engine 结构体 + 操作
│   │   └── errors.go               # 语义错误码
│   ├── config/                     # 动态配置 (YAML 热重载)
│   ├── constants/                  # 常量定义 (插件 ID / 注解 / 标签)
│   ├── pathutil/                   # 路径安全验证
│   │   ├── validate.go             # ValidateSafeID / ValidatePathUnderBase
│   ├── utils/                      # 通用工具
│   │   └── pathsec.go              # SafeJoinPath 防路径穿越
│   └── networkagentclient/         # network-agent gRPC 客户端
├── network/                        # 网络插件根目录
│   └── plugin.go                   # delegateNetworkManager + TAP 管理
├── storage/                        # 存储插件根目录
│   └── plugin.go                   # cubecow CoW 引擎启动
├── api/                            # Protobuf 定义 (ttrpc)
│   └── services/
│       ├── cubebox/v1/             # Cubebox API protobuf
│       └── errorcode/v1/           # 错误码
├── config/
│   └── config.toml                 # 默认 TOML 配置
├── dynamicconf/
│   └── conf.yaml                   # 热重载 YAML 配置
├── internal/                       # 内部包 (cube img store, opts 等)
└── integration/                    # 集成测试
```

来源: `Cubelet/cmd/cubelet/main.go:56-63` (入口常量), `Cubelet/cmd/cubelet/config.go:147-168` (默认配置)

### 2.2 模块分层

```
┌──────────────────────────────────────────────────────────────┐
│  入口层                                                        │
│    • cmd/cubelet/main.go — 双重 fork + mount namespace        │
│    • cmd/cubelet/main_linux.go — CLONE_NEWNET 绑定            │
├──────────────────────────────────────────────────────────────┤
│  Server 层 (ttrpc + gRPC + HTTP + Debug)                      │
│    • services/server — Server 装配 + operation/tap provider   │
│    • services/cubebox — 沙箱生命周期实现                        │
│    • services/images — 镜像管理                                │
│    • services/gc — 垃圾回收                                    │
│    • services/nbi — NBI service                                │
│    • services/version — 版本服务                                │
├──────────────────────────────────────────────────────────────┤
│  业务/插件层                                                   │
│    • plugins/workflow — 工作流编排                              │
│    • plugins/cube/internals/sandbox — containerd shim v2      │
│    • plugins/network — 网络管理 (TAP)                          │
│    • plugins/storage — 存储管理 (cubecow)                      │
│    • plugins/cgroup — cgroup 管理                              │
│    • plugins/images — 镜像插件                                  │
│    • plugins/chi — vsocket-manager                             │
├──────────────────────────────────────────────────────────────┤
│  客户端层 (下游通信)                                            │
│    • pkg/masterclient — CubeMaster HTTP 客户端                 │
│    • pkg/networkagentclient — network-agent gRPC 客户端        │
│    • plugins/cube/internals/sandbox — CubeShim shim v2        │
├──────────────────────────────────────────────────────────────┤
│  基础设施层                                                    │
│    • containerd 扩展 — OCI runtime shim v2 / ttrpc             │
│    • pkg/cubecow — Rust CGo 桥接 (libcubecow.a)               │
│    • pkg/config — YAML 动态配置热重载                           │
│    • pkg/pathutil / pkg/utils — 路径安全 + 工具函数             │
└──────────────────────────────────────────────────────────────┘
```

来源: `Cubelet/services/server/server.go:97-191` (Server.New 装配顺序), `Cubelet/plugins/workflow/engine.go:260-265` (Workflow 结构)

### 2.3 与上下游服务关系

```
┌──────────────┐   ttrpc/mTLS   ┌──────────┐  gRPC+unix  ┌────────────────┐
│  CubeMaster  │ ◀────────────▶│ Cubelet  │ ───────────▶│ network-agent   │
│  (Go)        │   HTTP/JSON    │  (Go)    │   ttrpc     │ (Go)           │
└──────────────┘   (心跳+注册)   └────┬─────┘             └────────────────┘
                                      │
                         ttrpc/vsock │ containerd shim v2
                                      ▼
                               ┌──────────┐
                               │ CubeShim  │
                               │ (Rust)    │
                               └────┬─────┘
                                    │
                               KVM  │ ioctl
                                    ▼
                               ┌──────────┐
                               │cube-hyper│
                               │ -visor    │
                               └──────────┘
```

**通信链路**:
- CubeMaster ↔ Cubelet: ttrpc (双向) + HTTP/JSON (节点注册/心跳: `POST /internal/meta/nodes/register`)
- Cubelet → network-agent: gRPC over Unix socket (`grpc+unix:///tmp/cube/network-agent-grpc.sock`)
- Cubelet ↔ CubeShim: containerd shim v2 协议 (ttrpc over vsock/Unix socket)

来源: `Cubelet/pkg/masterclient/client.go:137-143` (RegisterNode / UpdateNodeStatus 端点), `Cubelet/config/config.toml:87` (network_agent_endpoint)

---

## 3. 处理流程

### 3.1 创建 Sandbox 流程

```
CubeMaster                     Cubelet                          CubeShim
  │                              │                                │
  │ CreateSandboxRequest         │                                │
  │ (ttrpc)                      │                                │
  │ ────────────────────────────▶│                                │
  │                              │                                │
  │                              │ ① plugins/workflow 引擎       │
  │                              │   engine.go:run("create")      │
  │                              │   - 按 config.toml 编排流程    │
  │                              │   - semaphore 并发控制          │
  │                              │                                │
  │                              │ ② Step 1: createid +          │
  │                              │    appsnapshot                 │
  │                              │   - 生成 sandbox ID            │
  │                              │   - 检查应用快照                │
  │                              │                                │
  │                              │ ③ Step 2 (parallel):          │
  │                              │   - images: 确保镜像            │
  │                              │   - volume: 准备卷              │
  │                              │   - storage/plugin:             │
  │                              │     cubecow CreateVolume        │
  │                              │   - network/plugin:             │
  │                              │     TAP 设备创建 + IP 分配      │
  │                              │   - netfile: 网络配置文件        │
  │                              │   - cube-sandbox-store          │
  │                              │                                │
  │                              │ ④ Step 3: cgroup               │
  │                              │   - cgroup v1/v2 资源限制       │
  │                              │                                │
  │                              │ ⑤ Step 4: cubebox              │
  │                              │   - services/cubebox/service   │
  │                              │   - 启动 CubeShim (shim v2)   │
  │                              │   - 传递 OCI spec              │
  │                              │ ───────────────────────────▶  │
  │                              │                                │ ⑥ CubeShim
  │                              │                                │   - cube-hypervisor VM
  │                              │                                │   - KVM 创建 VM
  │                              │                                │   - cube-agent (PID 1)
  │                              │                                │   - 启动容器进程
  │                              │ ◀───────────────────────────  │
  │                              │                                │
  │                              │ ⑦ 聚合响应 + 指标上报            │
  │ ◀───────────────────────────│                                │
  │ CreateSandboxResponse        │                                │
```

**关键文件位置**:
- 入口: `Cubelet/cmd/cubelet/main.go:65-109` (双重 fork + mount ns)
- Workflow 引擎: `Cubelet/plugins/workflow/engine.go:334-392` (`run` 方法, 流控 + 并行 + 回滚)
- 工作流步骤定义: `Cubelet/config/config.toml:146-156` (init/create/destroy/cleanup 步骤)
- Sandbox 管理器: `Cubelet/plugins/cube/internals/sandbox/cube_sandbox_manager.go:45-80` (containerd sandbox controller)
- Cubebox 服务: `Cubelet/services/cubebox/service.go` (Create/Run/Destroy/Exec/Probe)
- 存储: `Cubelet/storage/plugin.go:206-219` (cubecow 引擎初始化)
- 网络: `Cubelet/network/plugin.go:57-88` (TAP 插件注册 + db 恢复)

### 3.2 节点注册与心跳流程

```
Cubelet (启动)                     CubeMaster
  │                                  │
  │ ① initialNode()                  │
  │   node_status.go:152-205         │
  │   - 构建节点元数据               │
  │   - 主机名 / 节点 IP / 标签      │
  │   - 实例类型 / ProviderID        │
  │   - 容量 + 可分配资源             │
  │   - 主机配额 (CPU overcommit ×2) │
  │   - 默认 NodeLabels              │
  │                                  │
  │ ② registerWithAPIServer()        │
  │   node_status.go:52-77           │
  │   - 指数退避重试 (100ms~7s)      │
  │   - POST /internal/meta/nodes/  │
  │     register                     │
  │ ───────────────────────────────▶│
  │                                  │
  │ ③ syncNodeStatus() (每 10s)     │
  │   node_status.go:263-276         │
  │   - 更新节点条件 (Ready)         │
  │   - 镜像列表 / 本地模板          │
  │   - 资源分配快照                  │
  │   - 磁盘使用率                    │
  │   - 组件版本                      │
  │ ───────────────────────────────▶│
  │                                  │
  │ ④ fastStatusUpdateOnce()         │
  │   启动后 120s 内 1s/次的快速     │
  │   状态更新,直到 Ready=True       │
```

**关键文件**: `Cubelet/pkg/cubelet/node_status.go:52-77` (registerWithAPIServer), `Cubelet/pkg/cubelet/node_status.go:263-276` (syncNodeStatus), `Cubelet/pkg/cubelet/node_status.go:506-528` (buildRegisterRequest)

**节点注册请求结构**:
```go
// Cubelet/pkg/masterclient/client.go:38-53
type RegisterNodeRequest struct {
    NodeID              string             // 主机实例 ID
    HostIP              string             // 内网 IP
    Labels              map[string]string  // hostname, instance-type, os/arch
    Capacity            ResourceSnapshot   // CPU/内存容量
    Allocatable         ResourceSnapshot   // 可分配资源
    InstanceType        string
    ClusterLabel        string             // 调度器标签 (默认 "default-cluster")
    QuotaCPU            int64              // CPU 配额 (overcommit ×2)
    QuotaMemMB          int64              // 内存配额 (×5/4)
    CreateConcurrentNum int64              // 创建并发数
    MaxMvmNum           int64              // 最大 MVM 数
    Versions            []ComponentVersion // 组件版本
}
```

### 3.3 Sandbox 生命周期其他操作

| 操作 | 接口 | 关键实现文件 |
|------|------|-------------|
| **销毁** | `Destroy(request)` → `plugins/workflow → destroy flow` | `services/cubebox/destroy.go` |
| **执行** | `Exec(request)` → 容器内执行命令 | `services/cubebox/exec.go` |
| **探针** | `Probe(request)` → 容器健康检查 | `services/cubebox/probe.go` |
| **更新** | `Update(request)` → 设备增删/暂停/恢复 | `services/cubebox/update.go` |
| **快照** | 创建运行时/应用快照 | `services/cubebox/snapshot_runtime_binding.go` |
| **回滚** | 从快照恢复 | `services/cubebox/rollback.go` |
| **事件** | 容器事件监听 | `services/cubebox/events.go` |
| **模板** | 运行时模板管理 | `services/cubebox/template_ops.go` |

---

## 4. 路由与端点

Cubelet **不暴露**独立的外部 HTTP/gRPC 端点。其监听地址分两类:

### 4.1 Unix Socket (本地进程间通信)

| 端点 | 路径 | 协议 | 用途 |
|------|------|------|------|
| gRPC 主端点 | `/data/cubelet/cubelet.sock` | gRPC | containerd CRI API |
| ttrpc 端点 | `/data/cubelet/cubelet.sock.ttrpc` | ttrpc | containerd 内部通信 |
| cubetap | `/data/cubelet/cubetap.sock` | ttrpc | TAP 设备管理 |
| operation | `/data/cubelet/cubelet-operation.sock` | HTTP | 操作接口 (默认禁用) |

### 4.2 TCP 监听

| 端点 | 地址 | 用途 |
|------|------|------|
| gRPC TCP | `:9999` | 外部 gRPC (调试/管理) |
| HTTP | `:9998` | 指标 `/v1/metrics` + snhost |
| Debug | `:9966` | pprof + expvar + loglevel 调试接口 |

### 4.3 对外 HTTP 端点 (与 CubeMaster 通信)

来源: `Cubelet/pkg/masterclient/client.go:133-143`

| 方法 | 路径 | 用途 |
|------|------|------|
| `GET` | `/internal/meta/readyz` | 健康检查 |
| `POST` | `/internal/meta/nodes/register` | 节点注册 |
| `POST` | `/internal/meta/nodes/{nodeID}/status` | 心跳状态更新 |

---

## 5. 认证机制

### 5.1 分层认证

| 通信对 | 认证方式 | 来源 |
|--------|---------|------|
| Cubelet ↔ CubeMaster | 无 TLS (纯 HTTP) | `pkg/masterclient/client.go:111-118` — `New()` 创建裸 `http.Client` |
| Cubelet → network-agent | Unix socket 文件权限 | `config/config.toml:87` — `grpc+unix:///tmp/cube/network-agent-grpc.sock` |
| Cubelet ↔ CubeShim | containerd shim v2 进程间通信 | 本地 Unix socket, 文件权限控制 |
| Cubelet gRPC/TTRPC | Unix socket UID/GID 权限 | `config/config.toml:20-25` — `uid=0, gid=0` |

### 5.2 节点级隔离

```go
// Cubelet/cmd/cubelet/main.go:148-221
// newCubeMnt() — 启动时创建隔离的 mount namespace
cmd.SysProcAttr = &syscall.SysProcAttr{
    Cloneflags: syscall.CLONE_NEWPID | syscall.CLONE_NEWNS,
}
```

Cubelet 在独立的 mount namespace 中运行,通过 `--make-rslave /` 设置从属传播:
- 主机挂载事件 → 传播到 Cubelet mount ns (单向)
- Cubelet 内的挂载 → 不会回流到主机

来源: `Cubelet/cmd/cubelet/main.go:206` — `{"nsenter", "-t", pid, "-m", "mount", "--make-rslave", "/"}`

### 5.3 网络命名空间绑定

```go
// Cubelet/cmd/cubelet/main_linux.go:50-66
// bindNamespaceToPath — 创建隔离的网络命名空间并绑定到文件系统
err = unix.Unshare(unix.CLONE_NEWNET)
err = unix.Mount(getCurrentThreadNetNSPath(), targetPath, "none", unix.MS_BIND, "")
```

---

## 6. 速率限制

- 无内置应用层限速 — 由上游 CubeAPI 处理
- 间接保护:
  - **工作流并发限制**: `config.toml` 中配置 create/destroy 的 `concurrent` 值 (默认 create=100, destroy=100)
  - **semaphore.Limiter**: `plugins/workflow/engine.go:338 — flow.Limiter.TryAcquire()` — 超过并发返回 `ConcurrentFailed` 错误码
  - **OOM 监管**: cgroup 插件设置 VM 内存上限
  - **磁盘配额**: cubecow 存储限制 (free_blocks_threshold, free_inodes_threshold)

来源: `Cubelet/plugins/workflow/engine.go:334-349` (并发控制), `Cubelet/config/config.toml:150-153` (concurrent=100)

---

## 7. 配置项

### 7.1 配置层次

| 层级 | 来源 | 说明 |
|------|------|------|
| Tier 1 | CLI flags | `--config`, `--root`, `--state`, `--log-level`, `--dynamic-conf-path` |
| Tier 2 | TOML 配置 | `config/config.toml` (主配置, 含插件配置) |
| Tier 3 | TOML `imports` | 可选的子配置文件 (递归合并) |
| Tier 4 | YAML 动态配置 | `dynamicconf/conf.yaml` — 支持运行时热重载 |
| Tier 5 | 环境变量 | `CUBE_TAP_SERV_PATH` 等 |

来源: `Cubelet/cmd/cubelet/main.go:304-324` (配置加载顺序)

### 7.2 CLI flags

来源: `Cubelet/cmd/cubelet/main.go:240-303`

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `--config, -c` | `/etc/containerd/config.toml` | 配置文件路径 |
| `--log-level, -l` | `warn` | 日志级别 |
| `--address, -a` | 默认: `/data/cubelet/cubelet.sock` | gRPC 地址 |
| `--root` | `/data/cubelet/root` | 数据根目录 |
| `--state` | `/data/cubelet/state` | 运行时状态目录 (tmpfs) |
| `--logpath` | `/data/log/Cubelet` | 日志输出目录 |
| `--log-roll-num` | 10 | 日志文件轮转数量 |
| `--log-roll-size` | 500 | 日志文件大小 (MB) |
| `--state-tmpfs-size` | 500 | tmpfs 目录大小 (MB) |
| `--go-max-procs` | 32 | GOMAXPROCS |
| `--go-gc-percent` | 500 | GC 百分比 |
| `--dynamic-conf-path` | `/usr/local/services/cubetoolbox/Cubelet/dynamicconf/conf.yaml` | 动态配置路径 |
| `--no-dynamic-path` | false | 禁用动态配置 |

### 7.3 关键 TOML 配置项

来源: `Cubelet/config/config.toml`

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `root` | `/data/cubelet/root` | 数据根目录 |
| `state` | `/data/cubelet/state` | 运行时状态 (tmpfs 挂载) |
| `pid_file` | `/run/cube-let.pid` | PID 文件 |
| `dynamic_config_path` | .../dynamicconf/conf.yaml | 热重载配置路径 |
| `grpc.address` | `/data/cubelet/cubelet.sock` | gRPC Unix socket |
| `grpc.tcp_address` | `:9999` | gRPC TCP 端口 |
| `http.address` | `:9998` | HTTP 指标端口 |
| `debug.address` | `:9966` | pprof/expvar 调试端口 |
| `cubetap.address` | `/data/cubelet/cubetap.sock` | TAP 管理 socket |
| `operation_server.disable` | `true` | 操作服务器默认禁用 |
| `network.enable_network_agent` | `true` | 启用 network-agent |
| `network.network_agent_endpoint` | `grpc+unix:///tmp/cube/network-agent-grpc.sock` | network-agent 地址 |
| `storage.storage_backend` | `cubecow` | 存储后端类型 |
| `storage.data_path` | `/data/cubelet/storage` | 存储数据路径 |
| `workflow.flows.create.concurrent` | `100` | 创建工作流并发数 |
| `workflow.flows.destroy.concurrent` | `100` | 销毁工作流并发数 |

### 7.4 动态配置项 (YAML 热重载)

来源: `Cubelet/dynamicconf/conf.yaml`

```yaml
common:
  disable_host_cgroup: true
  disable_host_netfile: true
  default_dns_servers:
    - 119.29.29.29
  sandbox_exec_cmd_time_out: 5s

host:
  scheduler_label: "default-cluster"
  quota:
    mcpu_limit: 0            # 0=自动检测
    mem_limit: ""             # 空=自动检测
    mvm_limit: 0              # 0=按内存自动计算
    creation_concurrent_num: 0
    paused_resource_release_ratio: 0.0
  gc:
    code_expiration_time: "72h"
    image_expiration_time: "24h"

meta_server_config:
  meta_server_endpoint: "127.0.0.1:8089"
  node_status_max_images: 40000
```

来源: `Cubelet/pkg/config/config.go` (动态配置结构体, `preHandle()` 默认值)

### 7.5 默认配额计算

| 资源 | 公式 | 来源 |
|------|------|------|
| CPU | `cpuCount × 1000 × 2` (overcommit ×2) | `node_status.go:640-643` |
| 内存 | `memTotal × 5/4` (系数 1.25) | `node_status.go:665` |
| 最大 MVM | `quotaMemMB / 512` (每个 MVM 默认 512MB) | `node_status.go:675` |

---

## 8. 关键文件清单

| 模块 | 路径 | 关键内容 |
|------|------|----------|
| **入口 (双重 fork)** | `cmd/cubelet/main.go` | `main()` — mount ns 隔离 + 启动 |
| **入口 (Linux)** | `cmd/cubelet/main_linux.go` | `createNewCubeMnt()` — netns 绑定 |
| **配置加载** | `cmd/cubelet/config.go` | `platformAgnosticDefaultConfig()` — 默认配置工厂 |
| **Server 装配** | `services/server/server.go` | `New()` — 插件初始化 + 服务注册 |
| **Server 配置** | `services/server/config/config.go` | `LoadConfig()` — TOML→结构体 (拒绝未知字段) |
| **Cubelet 结构** | `pkg/cubelet/cubelet.go` | `Cubelet` 结构体 + `Run()` 启动循环 |
| **节点状态上报** | `pkg/cubelet/node_status.go` | 注册/心跳/ReadyCondition 构建 |
| **Master 客户端** | `pkg/masterclient/client.go` | HTTP 客户端 + 轮询 HA |
| **Sandbox 管理器** | `plugins/cube/internals/sandbox/cube_sandbox_manager.go` | containerd sandbox controller |
| **Sandbox Store** | `plugins/cube/internals/sandbox/cube_sandbox_store.go` | sandbox 持久化 |
| **Workflow 引擎** | `plugins/workflow/engine.go` | `Flow` 接口 + `run()` 编排 |
| **Workflow 注册** | `plugins/workflow/plugin/plugin.go` | TOML→workflow 映射 |
| **Cubebox 服务** | `services/cubebox/service.go` | Create/Destroy/Exec/Probe |
| **存储插件** | `storage/plugin.go` | cubecow 引擎初始化 + 启动依赖检查 |
| **cubecow 桥接** | `pkg/cubecow/` | Rust CGo 桥接 (CreateVolume, DeleteVolume, 快照等) |
| **网络插件** | `network/plugin.go` | TAP 设备管理 + network-agent 集成 |
| **容器配置** | `pkg/container/container.go` | `GenOpt()` — 默认 OCI spec 生成 |
| **Seccomp** | `pkg/container/seccomp/seccomp.go` | Seccomp BPF 配置 |
| **Capabilities** | `pkg/container/capability/capability.go` | Linux capabilities 添加/删除 |
| **UID/GID** | `pkg/container/uid/uid.go` | 用户命名空间映射 (默认 root) |
| **Rootfs** | `pkg/container/rootfs/rootfs.go` | rootfs 准备 + CoW 清理 (SafeJoinPath) |
| **Rlimit** | `pkg/container/rlimit/rlimit.go` | NOFILE 限制 (默认 1024) |
| **Tmpfs** | `pkg/container/tmpfs/tmpfs.go` | nosuid/noexec/nodev tmpfs |
| **路径验证** | `pkg/pathutil/validate.go` | `ValidateSafeID`, `ValidatePathUnderBase` |
| **路径安全** | `pkg/utils/pathsec.go` | `SafeJoinPath` — 防路径穿越 |
| **常量** | `pkg/constants/const.go` | 插件 ID / Master 注解 / 挂载类型 |
| **动态配置** | `pkg/config/config.go` | YAML 热加载 + validation |
| **配置 (TOML)** | `config/config.toml` | 主配置 (152 行) |
| **配置 (动态)** | `dynamicconf/conf.yaml` | 热重载 YAML 配置 |
| **网络 agent** | `pkg/networkagentclient/` | network-agent gRPC/HTTP 客户端 |
| **版本收集** | `pkg/cubelet/versioninfo/` | 组件版本收集器 |

---

## 9. 安全注意事项

### 9.1 已知安全特性

| # | 安全特性 | 位置 | 风险等级 |
|---|---------|------|---------|
| S1 | **Seccomp BPF 过滤** — 默认 containerd seccomp profile + 自定义系统调用 | `pkg/container/seccomp/seccomp.go:53-66` | 🟢 低 — 默认启用 |
| S2 | **NoNewPrivileges** — OCI 默认关闭新特权 | `pkg/container/container.go:26` | 🟢 低 — containerd 默认 |
| S3 | **Read-only rootfs** — 可选只读根文件系统 | `pkg/container/rootfs/rootfs.go:51-53` | 🟢 低 — 可选 |
| S4 | **用户命名空间映射** — 通过 securityContext 设置 RunAsUser/Group | `pkg/container/uid/uid.go:25-58` | 🟡 中 — 默认 root (UID=0) |
| S5 | **Mount propagation 控制** — rprivate/rslave/rshared 可配置 | `pkg/constants/const.go:289-293` | 🟢 低 — 可配置 |
| S6 | **cgroup 隔离** — CPU/内存 cgroup v1/v2 | cgroup plugin | 🟢 低 |
| S7 | **SafeJoinPath** — 防路径穿越攻击 | `pkg/utils/pathsec.go:16-33` | 🟢 低 |
| S8 | **路径验证** — ValidateSafeID / ValidatePathUnderBase / ValidateNoTraversal | `pkg/pathutil/validate.go:25-66` | 🟢 低 |
| S9 | **镜像加密支持** — containerd ocicrypt 兼容 | `cmd/cubelet/config.go:171-199` | 🟢 低 — 可选 |
| S10 | **Tmpfs nosuid/noexec/nodev** — 临时文件系统安全挂载 | `pkg/container/tmpfs/tmpfs.go` | 🟢 低 |
| S11 | **Rlimit NOFILE** — 默认文件描述符限制 1024 | `pkg/container/rlimit/rlimit.go` | 🟢 低 |
| S12 | **Mount namespace 隔离** — Cubelet 运行在独立 mount ns | `cmd/cubelet/main.go:148-221` | 🟢 低 |
| S13 | **`--make-rslave /`** — mount 事件单向传播 (主机→Cubelet) | `cmd/cubelet/main.go:206` | 🟢 低 |
| S14 | **Unix socket 文件权限** — gRPC/TTRPC socket 的 UID/GID 控制 | `config/config.toml:21-25` | 🟢 低 |
| S15 | **tmpfs 状态目录** — 元数据放在内存文件系统 | `cmd/cubelet/main.go:663-683` | 🟢 低 |
| S16 | **TOML 未知字段拒绝** — 配置加载时拒绝未知键 | `services/server/config/config.go:148` | 🟢 低 |
| S17 | **cubecow 启动依赖检查** — 启动时验证外部命令存在 | `storage/plugin.go:187-204` | 🟢 低 |

### 9.2 已知风险

| # | 风险 | 位置 | 风险等级 |
|---|------|------|---------|
| R1 | **KubeletConfig.Insecurity 默认 true** — 安全开关默认关闭 | `pkg/cubelet/cubelet.go:53` | 🟠 中 |
| R2 | **与 CubeMaster 无 TLS** — 节点注册+心跳走纯 HTTP | `pkg/masterclient/client.go:111-118` | 🔴 高 — 内网默认无加密 |
| R3 | **Debug 端点公开 pprof/expvar** — `:9966` 默认监听到所有接口 | `services/server/server.go:285-295` | 🟠 中 — 信息泄露 |
| R4 | **默认 root (UID=0) 运行容器** — 无用户命名空间映射 | `pkg/container/uid/uid.go:21-23,57` | 🟡 中 |
| R5 | **特权模式可禁用所有安全限制** — capability 模块 | `pkg/container/capability/capability.go` | 🟠 中 — 需显式启用 |
| R6 | **egress 策略依赖外部下发** — network-agent 策略 | `network/plugin.go` | 🟡 中 |
| R7 | **Unix socket UID/GID 为 0 (root)** — 默认所有 socket root 所有 | `config/config.toml:21-25` | 🟡 中 |
| R8 | **parentExit() 无条件 SIGKILL 父进程** — 重新执行后杀死父进程 | `cmd/cubelet/main.go:123-130` | 🟡 中 — 设计如此 |
| R9 | **资源配额从 /proc/meminfo 自动检测** — 非安全配置源 | `pkg/cubelet/node_status.go:732-759` | 🟢 低 — 可被 YAML 覆盖 |

### 9.3 路径穿越防护

```go
// pkg/utils/pathsec.go:16-33 — SafeJoinPath — 防止 ../ 路径穿越
func SafeJoinPath(baseDir, untrusted string) (string, error) {
    if untrusted == "" {
        return "", fmt.Errorf("path component must not be empty")
    }
    // 拒绝包含 / \ . .. 的组件
    if strings.ContainsAny(untrusted, `/\`) || untrusted == "." || untrusted == ".." ||
        strings.Contains(untrusted, "..") {
        return "", fmt.Errorf("invalid path component %q: contains path traversal characters", untrusted)
    }
    joined := filepath.Join(baseDir, untrusted)
    cleaned := filepath.Clean(joined)
    base := filepath.Clean(baseDir)
    if !strings.HasPrefix(cleaned, base+string(filepath.Separator)) && cleaned != base {
        return "", fmt.Errorf("path %q escapes base directory %q", cleaned, base)
    }
    return cleaned, nil
}
```

此函数在以下关键路径中使用:
- `pkg/container/rootfs/rootfs.go:110` — `CleanRootfs()` 容器 rootfs 清理
- `pkg/container/rootfs/rootfs.go:154` — `CreateRootfs()` 容器 rootfs 创建

### 9.4 输入验证函数

```go
// pkg/pathutil/validate.go — 输入净化
func ValidateSafeID(id string) error           // 拒绝 /\ 和 ..
func ValidatePathUnderBase(basePath, inputPath string) (string, error)  // 确保路径在基路径下
func ValidateNoTraversal(p string) error        // 检查 ..
func ValidateIfName(name string) error           // 网络接口名: ^[A-Za-z0-9_.:-]{1,32}$
func ValidateUUID(id string) error               // UUID: ^[A-Fa-f0-9-]{1,64}$
```

---

## 10. 与 SVG 边界模型的关系

| SVG 边界 | Cubelet 中的对应 |
|----------|------------------|
| T3 (Node Trust) | 整个 Cubelet 进程 — 节点级信任边界 |
| L3 (host 进程域) | Cubelet 进程本身 (seccomp + cgroup + mount ns 隔离) |
| L4 (host 内核域) | TAP 设备 + eBPF TC 过滤器 + network-agent |
| L5 (存储域) | cubecow COW 存储引擎 |
| L6 (网络域) | network/plugin + TAP 设备管理 + network-agent |
| L7 (可观测性域) | 节点状态上报 + Metrics (:9998) + Debug (:9966) |
| L8 (控制面域) | ttrpc ↔ CubeMaster + containerd shim v2 ↔ CubeShim |

详细边界视角见 [security-boundaries/T3-cubelet.md](../安全边界/T3-cubelet.md)。

---

## 11. 总结:安全设计权衡

1. **containerd 扩展设计**: 复用 OCI 生态同时增加 MicroVM 能力。代价是继承 containerd 的配置复杂度 (TOML + 多级导入)。
2. **双重 fork + mount ns 隔离**: 启动时创建隔离的 mount namespace 和 PID namespace,通过 `--make-rslave` 实现主机→Cubelet 的单向挂载传播,防止 Cubelet 内的挂载操作影响主机。
3. **网络委托给 network-agent**: 解耦 TAP 设备管理,但增加 gRPC Unix socket IPC 通信面,需依赖文件权限控制。
4. **存储委托给 cubecow (Rust)**: CGo 桥接带来高性能 reflink CoW,但引入 CGo 调用开销和跨语言错误映射复杂度。
5. **可配置安全策略**: Seccomp + Capabilities + cgroup + Read-only rootfs 均可按需配置,但要求运维正确配置安全上下文。
6. **多层级配置**: TOML + YAML 热重载灵活但增加配置复杂度,且默认值可能不安全 (Insecurity=true)。
7. **无 mTLS 通信**: 与 CubeMaster 的通信走纯 HTTP,默认无加密认证,依赖网络隔离保护。
8. **调试接口公开**: `:9966` 默认监听所有接口,暴露 pprof 和 expvar,需在生产环境关闭或限制访问。
