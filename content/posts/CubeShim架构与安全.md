---
title: "CubeShim 架构、处理流程与安全配置"
date: 2026-07-08T00:00:00+08:00
draft: false
tags: ["cubesandbox"]
categories: ["cubesandbox"]
comments: true
showToc: true
weight: 100
---


> 调研时间: 2026/07/08
> 调研范围: `/home/zs/Develop/TraceCubeSandbox/CubeShim/` 全量 Rust 源码
> 目的: 系统性梳理 CubeShim (containerd Shim v2) 的架构、处理流程与安全配置
> 配套文档: [security-boundaries/T3-cubesandbox-node.md](../安全边界/T3-cubesandbox-node.md) (边界视角,本文档是"内部视角")
>
> 每节都带文件位置证据,可以直接引用。

---

## 1. 概述

CubeShim 是 CubeSandbox 的 **containerd Shim v2 实现** (`io.containerd.cube.v2`),作为 containerd 与 KVM MicroVM 之间的桥梁。

| 属性 | 值 |
|------|-----|
| **语言** | Rust 2021 edition (rust-toolchain 1.77.2) |
| **框架** | containerd-shim-rs (Shim v2 ttrpc) |
| **依赖 (Cargo.toml)** | tokio, cube-hypervisor, containerd-shim, protobuf, ttrpc |
| **二进制** | `containerd-shim-cube-rs` (shim daemon) + `cube-runtime` (CLI 工具) |
| **上游** | containerd (通过 Shim v2 API) |
| **下游** | cube-agent (vsock/ttrpc) + cube-hypervisor (KVM) |

**核心职责**:
- 实现 containerd Shim v2 API,注册为 `io.containerd.cube.v2`
- VM 生命周期管理 (创建/启动/暂停/恢复/删除)
- 容器生命周期管理 (创建/启动/执行/销毁)
- I/O 和信号代理 (host ↔ guest)
- 快照创建与恢复 (VM snapshot/restore)
- Sandbox 状态上报 (events publish)
- OCI spec 解析与传递给 cube-agent

---

## 2. 架构

### 2.1 顶层目录结构

```
CubeShim/
├── Cargo.toml                    # Workspace: shim + protoc + cube-runtime
├── rust-toolchain.toml           # Rust 1.77.2
├── shim/                         # 主 shim 二进制
│   ├── src/
│   │   ├── main.rs               # 入口 (tokio runtime + shim 启动)
│   │   ├── lib.rs                # 模块声明
│   │   ├── service/
│   │   │   ├── srv.rs            # Shim trait (new/start_shim/delete_shim)
│   │   │   ├── task_srv.rs       # Task trait (create/start/kill/exec/...)
│   │   │   ├── tools.rs          # 工具函数
│   │   │   └── update_ext.rs     # 扩展 API (RollbackSnapshot)
│   │   ├── sandbox/
│   │   │   ├── sb.rs             # SandBox 核心编排器 (1448 行)
│   │   │   ├── config.rs         # 配置解析 (OCI annotations)
│   │   │   ├── disk.rs           # 块设备路径
│   │   │   ├── net.rs            # 网络配置 (Interfaces/Routes/ARP)
│   │   │   ├── pmem.rs           # 持久内存
│   │   │   └── device.rs         # VFIO 设备透传
│   │   ├── hypervisor/
│   │   │   ├── cube_hypervisor.rs # CubeHypervisor 封装
│   │   │   ├── config.rs         # HypConfig / VmConfig
│   │   │   └── snapshot.rs       # 快照元数据
│   │   ├── container/
│   │   │   ├── mod.rs            # Container 结构体 + 生命周期
│   │   │   ├── container_mgr.rs  # 容器状态管理 (TaskState)
│   │   │   ├── exec.rs           # Exec/Tty I/O 转发
│   │   │   └── rootfs.rs         # Rootfs 配置
│   │   ├── snapshot/
│   │   │   ├── mod.rs            # 快照 CLI 支持 (离线+在线)
│   │   │   └── cmd.rs            # SnapshotArgs + CLI parse
│   │   ├── common/
│   │   │   ├── mod.rs            # 类型别名 + 常量
│   │   │   ├── types.rs          # PropagationMount
│   │   │   └── utils.rs          # 工具函数 (Utils / AsyncUtils / CPath)
│   │   ├── cube/
│   │   │   └── mod.rs
│   │   └── log/
│   │       ├── mod.rs            # 异步日志 + 轮转
│   │       └── stat_defer.rs     # StatDefer 计时工具
├── protoc/                       # Protobuf 代码生成
│   ├── protos/                   # agent.proto, health.proto, oci.proto, etc.
│   └── src/                      # 生成的 Rust 绑定 (ttrpc client)
├── cube-runtime/                 # CLI 管理工具
│   └── src/
│       ├── main.rs               # snapshot/login/completions
│       ├── login.rs              # 调试串口登录
│       ├── completions.rs        # Shell 补全
│       ├── parser.rs             # 参数解析
│       └── utils.rs              # CLI 工具函数
└── docs/shimapi/                 # 扩展 API 文档
    └── rollback-snapshot.md
```

### 2.2 模块分层

```
┌──────────────────────────────────────────────────────────────┐
│  Shim 层 (containerd-shim-rs)                                 │
│    • service/srv.rs — Shim trait 实现                         │
│    • service/task_srv.rs — Task trait 实现                    │
│    • service/update_ext.rs — 扩展 API                         │
├──────────────────────────────────────────────────────────────┤
│  Sandbox 层 (核心编排)                                          │
│    • sandbox/sb.rs — SandBox 结构体 (VM + 容器管理)           │
│    • sandbox/config.rs — 配置解析                             │
│    • sandbox/net.rs — 网络配置                                │
├──────────────────────────────────────────────────────────────┤
│  Hypervisor 层 (KVM VMM 封装)                                  │
│    • hypervisor/cube_hypervisor.rs — VmmInstance 封装         │
│    • hypervisor/config.rs — VM 配置                           │
│    • hypervisor/snapshot.rs — 快照元数据                      │
├──────────────────────────────────────────────────────────────┤
│  Container 层 (容器管理)                                       │
│    • container/mod.rs — Container 结构体                      │
│    • container/exec.rs — I/O 转发                             │
│    • container/rootfs.rs — Rootfs 配置                        │
├──────────────────────────────────────────────────────────────┤
│  Agent 层 (与 cube-agent 通信)                                 │
│    • protoc/ — ttrpc 客户端生成                               │
│    • vsock — Unix socket vsock 连接                           │
└──────────────────────────────────────────────────────────────┘
```

### 2.3 与上下游服务关系

```
┌────────────┐   Shim v2 (ttrpc)   ┌──────────────┐   vsock/ttrpc   ┌────────────┐
│ containerd │ ◀──────────────────▶│   CubeShim   │ ───────────────▶│ cube-agent  │
│ (Cubelet)  │   Unix socket       │   (Rust)     │   /run/vc/vm/   │ (PID 1)    │
└────────────┘                     └──────┬───────┘                 └────────────┘
                                          │ chapi (HTTP)
                                          ▼
                                  ┌────────────────┐
                                  │ cube-hypervisor │
                                  │ (KVM VMM)      │
                                  └────────────────┘
```

CubeShim 是**每个 sandbox 一个独立进程**,通过 Unix socket 与 containerd 通信 (Shim v2 ttrpc),通过 vsock 与 VM 内 cube-agent 通信,通过 HTTP chapi 与 cube-hypervisor (VMM) 通信。

---

## 3. 处理流程

### 3.1 创建 Sandbox + 容器

```
containerd                    CubeShim                              cube-agent
  │                              │                                     │
  │ Create (shim v2)             │                                     │
  │ ────────────────────────────▶│                                     │
  │                              │                                     │
  │                              │ ① service/srv.rs                   │
  │                              │   - 解析 CLI flags                 │
  │                              │   - 创建 SandBox 结构体            │
  │                              │                                     │
  │                              │ ② service/task_srv.rs::create      │
  │                              │   - 加载 OCI spec (config.json)    │
  │                              │   - 首次调用 → sb.init()            │
  │                              │   - 解析 OCI annotations            │
  │                              │                                     │
  │                              │ ③ sandbox/sb.rs::init              │
  │                              │   - 解析 annotations → config      │
  │                              │   - 创建 VM 工作目录                │
  │                              │   - 校验快照恢复条件                │
  │                              │                                     │
  │                              │ ④ sandbox/sb.rs::create_sandbox    │
  │                              │   - prepare_resource (内核/内存/网络)│
  │                              │   - boot_vm (PVH direct boot)      │
  │                              │   - 等待 vsock 就绪                 │
  │                              │   - connect_agent (vsock)          │
  │                              │   - 重置 guest 时间/RNG            │
  │                              │   - createSandbox RPC (agent)      │
  │                              │ ─────────────────────────────────▶ │
  │                              │   CreateSandbox                    │
  │                              │   CreateContainer                  │
  │                              │ ◀────────────────────────────────  │
  │                              │                                     │
  │                              │ ⑤ 返回 TaskCreate 事件             │
  │ ◀───────────────────────────│                                     │
  │                              │                                     │
```

**关键文件位置**:
- 入口: `CubeShim/shim/src/main.rs`
- Shim trait: `CubeShim/shim/src/service/srv.rs`
- Task trait: `CubeShim/shim/src/service/task_srv.rs:30-200` (`create` 方法)
- Sandbox 初始化: `CubeShim/shim/src/sandbox/sb.rs:160-210` (`init` 方法)
- Sandbox 创建: `CubeShim/shim/src/sandbox/sb.rs:350-480` (`create_sandbox` 方法)
- VM 启动: `CubeShim/shim/src/sandbox/sb.rs:580-750` (`boot_vm` / `restore_vm`)
- OCI 配置: `CubeShim/shim/src/sandbox/config.rs` (全量 annotation 解析)

### 3.2 快照创建流程

```
cube-runtime CLI                 CubeShim
  │                              │
  │ snapshot --path <path>       │
  │ ────────────────────────────▶│
  │                              │
  │                              │ ① snapshot/mod.rs::handle
  │                              │   - do_snapshot() (离线) 或
  │                              │     do_app_snapshot() (在线)
  │                              │
  │                              │ ② do_snapshot (离线):
  │                              │   - launch_vmm (冷启动 VM)
  │                              │   - boot_vm (PVH 引导)
  │                              │   - wait_vm_ready
  │                              │   - create_snapshot (内存+设备)
  │                              │   - store_metadata (kernel/image/disk)
  │                              │
  │                              │ ③ do_app_snapshot (在线):
  │                              │   - api_pause_vm (HTTP PUT pause)
  │                              │   - api_snapshot_vm (HTTP PUT snapshot)
  │                              │   - store_metadata
  │                              │   - api_resume_vm (HTTP PUT resume)
  │                              │   - 即使快照失败也尝试 resume
  │                              │
  │                              │ ④ 路径安全检查
  │                              │   - check_path() 防覆盖系统路径
  │                              │
  │ ◀───────────────────────────│
```

**关键文件位置**:
- 快照入口: `CubeShim/shim/src/snapshot/mod.rs`
- CLI 参数: `CubeShim/shim/src/snapshot/cmd.rs`
- 在线快照 HTTP API: `CubeShim/shim/src/snapshot/mod.rs` (`api_pause_vm` / `api_snapshot_vm` / `api_resume_vm`)
- 快照元数据: `CubeShim/shim/src/hypervisor/snapshot.rs`

### 3.3 paused -> resumed 快速恢复流程

```
containerd                    CubeShim                              cube-agent
  │                              │                                     │
  │ Resume (shim v2)             │                                     │
  │ ────────────────────────────▶│                                     │
  │                              │                                     │
  │                              │ ① service/task_srv.rs::resume      │
  │                              │   - 调用 sb.resume_vm()             │
  │                              │                                     │
  │                              │ ② sandbox/sb.rs::resume_vm         │
  │                              │   - hypervisor.resume_vm_cube()     │
  │                              │   - VmResumeFromSnapshot            │
  │                              │   - 连接 agent + 重置 RNG           │
  │                              │                                     │
  │                              │ ③ connect_agent → 恢复通信          │
  │                              │ ─────────────────────────────────▶ │
  │                              │ ◀────────────────────────────────  │
  │                              │                                     │
  │ ◀───────────────────────────│                                     │
```

---

## 4. 路由与端点

### 4.1 Shim v2 API (ttrpc, Unix socket)

由 `containerd-shim-rs` 框架实现,通过 `TaskService` 暴露:

| 方法 | Handler | 位置 | 说明 |
|------|---------|------|------|
| `Create` | `task_srv.rs::create` | `service/task_srv.rs:30` | 创建 sandbox + 首个容器 |
| `Start` | `task_srv.rs::start` | `service/task_srv.rs:100` | 启动容器/exec 进程 |
| `Delete` | `task_srv.rs::delete` | `service/task_srv.rs:130` | 删除容器/exec |
| `Kill` | `task_srv.rs::kill` | `service/task_srv.rs:140` | 发送信号 |
| `Exec` | `task_srv.rs::exec` | `service/task_srv.rs:170` | 创建 exec 进程 |
| `Wait` | `task_srv.rs::wait` | `service/task_srv.rs:110` | 等待进程退出 |
| `Pause` | `task_srv.rs::pause` | `service/task_srv.rs:190` | 暂停 VM (pod-scoped) |
| `Resume` | `task_srv.rs::resume` | `service/task_srv.rs:195` | 恢复 VM (pod-scoped) |
| `State` | `task_srv.rs::state` | `service/task_srv.rs:150` | 查询状态 |
| `Shutdown` | `task_srv.rs::shutdown` | `service/task_srv.rs:160` | 销毁 sandbox |
| `Update` | `task_srv.rs::update` | `service/task_srv.rs:120` | 更新资源 + 扩展 API |
| `Connect` | `task_srv.rs::connect` | `service/task_srv.rs:145` | 返回 shim PID |

### 4.2 扩展 API (通过 Update 注解)

| 操作 | 注解 | Handler | 位置 |
|------|------|---------|------|
| RollbackSnapshot | `cube.shimapi.update.rollback.restore_config` | `update_ext.rs::do_rollback_snapshot` | `service/update_ext.rs` |

通过 `Update` RPC 的 annotations 触发,无需额外端点。

### 4.3 Cube Runtime CLI

| 子命令 | Handler | 位置 | 说明 |
|--------|---------|------|------|
| `snapshot` | `snapshot::cmd::execute` | `shim/src/snapshot/cmd.rs` | 离线/在线快照创建 |
| `login` | `login::execute` | `cube-runtime/src/login.rs` | 调试串口登录 |
| `completions` | `completions::generate_completions` | `cube-runtime/src/completions.rs` | Shell 补全生成 |

所有 CLI 路由在 `cube-runtime/src/main.rs` 注册。

---

## 5. 认证机制

CubeShim 不实现独立认证,所有通信基于进程间和虚拟机隔离:

| 通信对 | 协议 | 认证方式 | 安全假设 |
|--------|------|---------|---------|
| containerd ↔ CubeShim | ttrpc (Unix socket) | 文件权限 (本地 IPC) | 同一 host 的可信进程 |
| CubeShim ↔ cube-agent | vsock/ttrpc | 虚拟机隔离 | Guest 不可伪造 host 端 vsock |
| CubeShim ↔ cube-hypervisor | HTTP chapi (Unix socket) | 文件权限 (本地 IPC) | 同一 host 的可信进程 |
| CubeShim ↔ containerd events | RemotePublisher (ttrpc) | TTRPC_ADDRESS env var | 本地 IPC |

**关键点**:
1. **vsock 天然隔离**: KVM vsock 提供 host↔guest 安全通道,host 端由 VMM 分配固定 context ID
2. **Unix socket 权限**: Shim socket 地址文件位于 `/run/containerd/` 或沙箱工作目录,受文件系统权限保护
3. **无网络暴露**: CubeShim 不监听任何 TCP 端口,所有通信走本地 Unix socket 或 vsock

---

## 6. 配置项 (OCI 注解)

CubeShim 的所有配置通过 OCI spec 的 annotations 传递,在 `sandbox/config.rs` 中解析:

| Annotation 键 | 模块 | 类型 | 说明 |
|--------------|------|------|------|
| `cube.vmmres` | sandbox/config | JSON | VM 资源 (cpu/memory/preserve_memory/snap_memory) |
| `cube.vm.kernel.path` | sandbox/config | string | 内核路径 |
| `cube.vm.kernel.cmdline.append` | sandbox/config | string | 附加内核参数 (自动去重冲突) |
| `cube.net` | sandbox/net | JSON | 网络配置 (Interfaces/Routes/ARP/QoS) |
| `cube.net.vips` | sandbox/config | JSON | 虚拟 IP 配置 |
| `cube.disk` | sandbox/disk | JSON | 块设备配置 |
| `cube.pmem` | sandbox/pmem | JSON | 持久内存配置 |
| `cube.fs` | sandbox/config | JSON | Virtio-fs 后端配置 |
| `cube.virtiofs` | sandbox/config | JSON | 附加 Virtio-fs 挂载 |
| `cube.vfio.disk` | sandbox/device | JSON | VFIO 磁盘透传 |
| `cube.vfio.disk.rm` | sandbox/device | JSON | VFIO 磁盘移除 |
| `cube.vfio.net` | sandbox/device | JSON | VFIO 网络透传 |
| `cube.rootfs.info` | container/rootfs | JSON | Rootfs 配置 (pmem/overlay/mounts/ero) |
| `cube.rootfs.wlayer.path` | common/mod | string | Rootfs writable layer 路径 |
| `cube.rootfs.wlayer.subdir` | common/mod | string | Rootfs writable layer 子目录 |
| `cube.snapshot.disable` | sandbox/config | bool | 禁用快照启动 |
| `cube.snapshot.base.path` | sandbox/config | string | 快照基路径 |
| `cube.snapshot.memory.vol.url` | sandbox/config | string | 快照内存卷 URL |
| `cube.appsnapshot.create` | sandbox/config | string | 应用快照创建 |
| `cube.appsnapshot.restore` | sandbox/config | string | 应用快照恢复 |
| `cube.propagation.mounts` | common/types | JSON | 挂载传播配置 |
| `cube.propagation.container.mounts` | common/types | JSON | 容器内挂载传播 |
| `cube.container.log_forwarding` | common/mod | bool | 日志转发开关 |
| `cube.container.custom.file` | container/rootfs | JSON | 自定义容器文件 |
| `cube.shimapi.update.action` | service/update_ext | string | 扩展 API 动作标识 |
| `cube.shimapi.update.rollback.restore_config` | service/update_ext | JSON | 回滚恢复配置 |

**注解解析位置**: 所有 annotation 常量定义在:
- `sandbox/config.rs` (VM/网络/设备相关)
- `common/mod.rs` (WLayer/日志/传播相关)
- `container/rootfs.rs` (rootfs/custom file)
- `service/update_ext.rs` (扩展 API)

---

## 7. 关键文件清单

| 模块 | 路径 | 关键内容 |
|------|------|----------|
| 入口 | `shim/src/main.rs` | tokio runtime + shim 启动 + rlimit + coredump |
| Shim trait | `shim/src/service/srv.rs` | new/start_shim/delete_shim |
| Task trait | `shim/src/service/task_srv.rs` | create/start/kill/exec/pause/resume (全 Shim v2 API) |
| 扩展 API | `shim/src/service/update_ext.rs` | RollbackSnapshot 恢复 |
| SandBox 编排 | `shim/src/sandbox/sb.rs` | VM + 容器管理 (~1448 行) |
| 配置解析 | `shim/src/sandbox/config.rs` | OCI annotations → Config |
| 网络配置 | `shim/src/sandbox/net.rs` | Interfaces/Routes/ARP/QoS |
| 块设备 | `shim/src/sandbox/disk.rs` | 设备路径 + 驱动名 |
| 持久内存 | `shim/src/sandbox/pmem.rs` | pmem 设备路径 |
| VFIO 设备 | `shim/src/sandbox/device.rs` | 磁盘/网络透传 |
| Hypervisor 封装 | `shim/src/hypervisor/cube_hypervisor.rs` | VmmInstance API + seccomp |
| VM 配置 | `shim/src/hypervisor/config.rs` | HypConfig / VmConfig (内核/CPU/内存/网络/磁盘) |
| 快照元数据 | `shim/src/hypervisor/snapshot.rs` | SnapshotInfo + 版本校验 |
| Container | `shim/src/container/mod.rs` | OCI spec 翻译 + 生命周期 |
| 容器管理 | `shim/src/container/container_mgr.rs` | TaskState + Exit/Wait 管理 |
| Exec | `shim/src/container/exec.rs` | Tty I/O 转发 + 日志转发 |
| Rootfs | `shim/src/container/rootfs.rs` | virtiofs/pmem/overlay 路径 |
| 快照 CLI | `shim/src/snapshot/mod.rs` | 离线+在线快照创建 |
| 快照参数 | `shim/src/snapshot/cmd.rs` | SnapshotArgs + clap 解析 |
| 公共常量 | `shim/src/common/mod.rs` | 注解键名 + 版本信息 |
| 工具函数 | `shim/src/common/utils.rs` | OCI 加载 / 路径构建 / agent 连接 |
| 日志 | `shim/src/log/mod.rs` | 异步日志 + 轮转 (30min) |
| CLI 工具 | `cube-runtime/src/main.rs` | snapshot/login/completions |
| 调试串口 | `cube-runtime/src/login.rs` | vsock 调试登录 |
| Proto 绑定 | `protoc/src/` | agent.proto / oci.proto / health.proto ttrpc 生成 |

---

## 8. 安全注意事项

### 8.1 安全特性

| # | 特性 | 位置 | 说明 |
|---|------|------|------|
| S1 | **Seccomp BPF 过滤** | `hypervisor/cube_hypervisor.rs:79-87` | Hypervisor 进程 syscall 白名单 (mkdir, getsockopt, setsockopt, faccessat2) |
| S2 | **Core dump 限制** | `main.rs` | `/proc/self/coredump_filter` 设 `0x33`,RLIMIT_CORE 设 2GB |
| S3 | **RLIMIT_NOFILE 限制** | `main.rs` | 防止 fd 耗尽攻击 |
| S4 | **日志路径验证** | `container/container_mgr.rs` | 日志路径组装,防止路径穿越 |
| S5 | **RNG reseed** | `sandbox/sb.rs` | 快照恢复后重新播种 `/dev/urandom` (从 host 读 2 字节) |
| S6 | **快照路径安全检查** | `snapshot/mod.rs:check_path()` | 防止快照路径覆盖系统路径 |
| S7 | **快照元数据校验** | `hypervisor/snapshot.rs:SnapshotInfo::eq()` | 版本/配置一致性校验 (ch_version, kernel_version, image_version, VmRes) |
| S8 | **OCI spec 清理** | `container/mod.rs:get_pb_spec()` | 清除敏感字段 (noNewPrivileges, selinuxLabel, devices, cgroup/net/pid namespace) |
| S9 | **内核参数冲突检测** | `hypervisor/config.rs:check_cmdline_conflicts()` | 防止重复内核参数导致安全绕过 |
| S10 | **快照一致性校验** | `hypervisor/snapshot.rs:align_pmems()` | 对齐 pmem 列表,跳过 placeholder 插入 |
| S11 | **panic 日志落盘** | `log/mod.rs` | 设置 panic hook,panic 时写入日志文件后退出 |
| S12 | **App snapshot 互斥** | `sandbox/config.rs` | `app_snapshot_create` 与 `app_snapshot_restore` 互斥,防止冲突 |

### 8.2 已知风险

| # | 风险 | 位置 | 等级 | 说明 |
|---|------|------|------|------|
| R1 | **annotation 全量透传** | `sandbox/config.rs` | 🟠 中 | 客户端可设置 `cube.vm.kernel.*` 等敏感注解,无白名单校验 |
| R2 | **exec 在 app snapshot 中被禁用** | `sandbox/sb.rs` | 🟢 低 | 临时限制,恢复后应正常 |
| R3 | **快照路径无签名校验** | `snapshot/mod.rs` | 🟡 中 | 快照文件无完整性签名,攻击者可替换 |
| R4 | **agent 连接无双向认证** | `common/utils.rs:connect_agent()` | 🟡 中 | vsock 连接仅做简单 `"CONNECT 1024\n"` 握手,无证书校验 |
| R5 | **RLIMIT_CORE 2GB 较高** | `main.rs` | 🟢 低 | 2GB core dump 限制对生产环境可能偏大 |
| R6 | **日志文件无大小限制** | `log/mod.rs` | 🟢 低 | 30 分钟轮转,但无最大文件大小限制 |
| R7 | **panic hook 日志路径固定** | `log/mod.rs` | 🟢 低 | 日志路径硬编码 `/data/log/CubeShim/` |
| R8 | **快照恢复时无 kernel 完整性校验** | `snapshot/mod.rs` | 🟡 中 | 离线快照直接使用指定内核路径,未校验 hash |
| R9 | **rng reseed 仅 2 字节** | `sandbox/sb.rs` | 🟢 低 | 从 `/dev/urandom` 读 2 字节 reseed,熵量较小 |

---

## 9. 与 SVG 边界模型的关系

CubeShim 是 SVG 中 **T3 (Node Trust 真边界)** 的关键执行点:

| SVG 边界 | CubeShim 中的对应 |
|----------|------------------|
| T3 (Node Trust) | 整个 CubeShim 进程 (每个 sandbox 独立进程) |
| L3 (host 进程域) | Shim 进程 (seccomp + cgroup + rlimit) |
| L4 (host 内核域) | KVM ioctl + vsock 设备 |
| L5 (Guest 域) | vsock → cube-agent (guest 内 PID 1) |
| L7 (可观测性域) | Shim 日志 `/data/log/CubeShim/` + container 日志转发 |

详细边界视角见 [security-boundaries/T3-cubesandbox-node.md](../安全边界/T3-cubesandbox-node.md)。

---

## 10. 总结

1. **Shim v2 设计**: 每个 sandbox 一个独立 shim 进程,天然进程隔离;通过 Unix socket 与 containerd 通信,无网络面
2. **全量 annotation 透传**: 配置灵活但增加安全风险,客户端可通过 annotation 影响 VM 内核/资源参数
3. **快照加速**: 通过 VM snapshot/restore 实现秒级 sandbox 启动;支持两种模式:离线快照 (冷启动) 和在线快照 (运行时暂停)
4. **vsock 通信**: 利用 KVM vsock 实现 host-guest 安全通道,无需网络栈
5. **seccomp 分层**: VMM 线程和 vCPU 线程有独立的 seccomp 策略;shim 进程本身无 seccomp (依赖操作系统保护)
6. **OCI spec 安全清理**: 在转发到 guest agent 前,清除敏感字段 (selinuxLabel, devices, cgroup/net/pid namespace)
7. **七层日志**: 异步日志框架 (JSON 格式) + 30 分钟轮转 + panic hook 落盘
8. **三个二进制**: shim daemon (`containerd-shim-cube-rs`) + CLI 工具 (`cube-runtime`) + proto 绑定库 (`protoc`)
