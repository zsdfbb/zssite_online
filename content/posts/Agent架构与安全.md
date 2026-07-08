---
title: "Agent (cube-agent) 架构、处理流程与安全配置"
date: 2026-07-08T00:00:00+08:00
draft: false
tags: ["cubesandbox"]
categories: ["cubesandbox"]
comments: true
showToc: true
weight: 100
---


> 调研时间: 2026/07/08
> 调研范围: `/home/zs/Develop/TraceCubeSandbox/agent/` 全量 Rust 源码
> 目的: 系统性梳理 cube-agent (沙箱内 Guest Agent) 的架构、处理流程与安全配置
>
> 每节都带文件位置证据,可以直接引用。

---

## 1. 概述

cube-agent 是每个 MicroVM sandbox 内部的 **PID 1 init 进程**,派生自 Kata Containers agent。它作为沙箱内所有容器的根进程,管理容器生命周期、I/O 转发、网络配置和设备热插拔。

| 属性 | 值 |
|------|-----|
| **语言** | Rust 2021 edition |
| **编译目标** | x86_64-unknown-linux-musl (静态链接) |
| **运行时间** | tokio (多线程) |
| **通信协议** | ttrpc over vsock |
| **协议端口** | vsock:1024 (默认) |
| **OCI 运行时** | rustjail (fork 隔离) |
| **可观测性** | Prometheus 指标 + OpenTelemetry vsock 导出 |
| **父进程** | PID 1 (init) |
| **构建系统** | Makefile + cargo (SECCOMP 可配置) |

核心职责:
- Guest 初始化 (挂载 procfs/sysfs/cgroup, 运行 rc.local)
- 容器生命周期管理 (OCI spec 解析 → rustjail fork)
- I/O 转发 (stdin/stdout/stderr vsock 代理)
- 网络配置 (Netlink 接口/路由/ARP)
- 资源热插拔 (CPU/内存/设备)
- RNG reseed (从宿主机注入熵)
- 控制台调试 (vsock PTY shell)
- OpenTelemetry 追踪 (vsock 导出)
- Prometheus 指标暴露

---

## 2. 架构

### 2.1 目录结构

```
agent/
├── Cargo.toml                    # 依赖 + 二进制 (cube-agent)
├── rust-toolchain.toml           # Rust 工具链固定
├── Makefile                      # 构建入口 (SECCOMP/STANDARD_OCI_RUNTIME 特性开关)
├── build.sh                      # 静态 musl 构建
├── src/                          # 主二进制
│   ├── main.rs                   # 入口: init + tokio runtime + rc.local
│   ├── config.rs                 # AgentConfig (cmdline / toml 解析)
│   ├── rpc.rs                    # ttrpc AgentService + HealthService 实现
│   ├── sandbox.rs                # Sandbox 结构体 (容器映射 + OOM + 网络)
│   ├── device.rs                 # 设备管理 (virtio-blk/PCI)
│   ├── mount.rs                  # 文件系统挂载 (virtiofs/9p/overlay)
│   ├── network.rs                # DNS 配置
│   ├── netlink.rs                # Netlink 接口/路由/ARP 管理
│   ├── pci.rs / uevent.rs       # 设备热插拔 (PCI/uevent 监控)
│   ├── random.rs                 # RNG reseed (/dev/random ioctl)
│   ├── console.rs                # 调试控制台 (vsock PTY shell)
│   ├── signal.rs                 # 信号处理 (SIGCHLD reaper)
│   ├── metrics.rs                # Prometheus 指标
│   ├── tracer.rs                 # OpenTelemetry 追踪
│   ├── time.rs                   # 时钟同步
│   ├── namespace.rs              # 命名空间管理
│   ├── linux_abi.rs              # Linux ABI 常量
│   ├── fixes.rs                  # 兼容性修复
│   ├── watcher.rs                # 文件系统监控 (bind mount)
│   └── util.rs                   # 工具函数 (async I/O copier)
├── rustjail/                     # OCI 容器运行时
│   └── src/
│       ├── container.rs          # LinuxContainer (fork/namespace/cgroup)
│       ├── process.rs            # Process (pipes/PTY/signal)
│       ├── specconv.rs           # CreateOpts (OCI spec 转换)
│       ├── seccomp.rs            # Seccomp BPF (可选, feature gate)
│       ├── capabilities.rs       # Capabilities 管理 (drop_privileges)
│       ├── mount.rs              # 容器挂载
│       ├── pipestream.rs         # PipeStream (async pipe wrapper)
│       ├── sync.rs / sync_with_async.rs  # 父子进程同步
│       ├── validator.rs          # OCI spec 校验
│       ├── console.rs            # 控制台 socket
│       └── cgroups/              # Cgroup 管理器 (FS/systemd/mock)
│           ├── fs/mod.rs         # FS cgroup 管理器
│           ├── systemd.rs        # systemd cgroup 管理器
│           ├── notifier.rs       # OOM 通知
│           └── mod.rs            # Manager trait
├── cube/                         # Cube 扩展
│   └── src/
│       ├── rootfs.rs             # Rootfs 覆盖层 (overlay/EROFS/pmem)
│       └── utils.rs              # 注解常量
├── vsock-exporter/              # OpenTelemetry vsock 导出
│   └── src/lib.rs
├── libs/
│   ├── logging/                  # 日志工具 (slog 封装)
│   ├── oci/                      # OCI spec Rust 绑定
│   └── protocols/                # ttrpc API 定义
│       ├── protos/agent.proto    # AgentService RPC
│       ├── protos/oci.proto      # OCI spec proto
│       ├── protos/types.proto    # 共享类型
│       └── src/                  # 生成的绑定
└── samples/
    └── configuration-all-endpoints.toml  # 配置示例
```

### 2.2 模块分层

```
┌──────────────────────────────────────────────────────────┐
│  初始化层 (PID 1)                                         │
│    • main.rs — 文件系统挂载 + tokio runtime                │
│    • signal.rs — SIGCHLD reaper + 子进程回收               │
│    • console.rs — /dev/console 初始化                      │
├──────────────────────────────────────────────────────────┤
│  RPC 层 (ttrpc Server)                                    │
│    • rpc.rs — AgentService + HealthService 实现           │
│    • libs/protocols — protobuf 生成绑定                   │
│    • 端点白名单检查 (is_allowed! 宏)                       │
├──────────────────────────────────────────────────────────┤
│  Sandbox 层 (沙箱编排)                                     │
│    • sandbox.rs — 容器映射 + 命名空间 + OOM 监控           │
│    • device.rs — 设备发现 + PCI 等待                       │
│    • mount.rs — 存储挂载 (virtiofs/9p/block)               │
│    • watcher.rs — bind mount 监控                          │
├──────────────────────────────────────────────────────────┤
│  rustjail 层 (OCI 运行时)                                  │
│    • container.rs — fork + namespace + cgroup              │
│    • process.rs — 进程管道/PTY + exec.fifo                 │
│    • capabilities.rs — drop_privileges 权能降级             │
│    • seccomp.rs — seccomp BPF (feature gate)              │
│    • cgroups/ — cgroup 管理 (FS systemd 双后端)            │
├──────────────────────────────────────────────────────────┤
│  I/O 转发层                                                │
│    • rpc.rs (WriteStdin/ReadStdout/ReadStderr)            │
│    • util.rs — interruptable_io_copier                    │
│    • rustjail/process.rs — PipeStream + PTY               │
├──────────────────────────────────────────────────────────┤
│  网络层                                                   │
│    • netlink.rs — rtnetlink 接口/路由/ARP                  │
│    • network.rs — DNS 配置 (/etc/resolv.conf)              │
│    • rpc.rs — UpdateInterface/UpdateRoutes/iptables        │
├──────────────────────────────────────────────────────────┤
│  可观测性层                                                │
│    • metrics.rs — Prometheus 指标                          │
│    • tracer.rs — OpenTelemetry tracing                    │
│    • vsock-exporter — OTel vsock 导出                     │
└──────────────────────────────────────────────────────────┘
```

### 2.3 交互关系

```
┌──────────────┐   vsock/ttrpc (port 1024)  ┌──────────────┐
│   CubeShim   │ ◀─────────────────────────▶│   cube-agent  │
│  (Rust)      │   CreateContainer/Exec/... │   (PID 1)     │
└──────┬───────┘                            └──────┬───────┘
       │                                          │
       │                                          │ rustjail fork
       │                                          ▼
       │                                   ┌──────────────┐
       │                                   │    Container   │
       │                                   │  (OCI 进程)    │
       │                                   └──────────────┘
       │
       │ vsock (port 10240) — OpenTelemetry traces
       │ vsock (debug_console_vport) — PTY 调试 shell
       │ vsock (log_vport) — 日志转发
```

**关键文件位置**:
- 入口: `agent/src/main.rs:283` (main 函数)
- RPC 启动: `agent/src/rpc.rs:1816` (`start` 函数)
- 配置解析: `agent/src/config.rs:202` (`from_cmdline`)

---

## 3. 处理流程

### 3.1 Guest 启动

来源: `agent/src/main.rs:141-281`

```
KVM vCPU start            cube-agent (PID 1)
  │                              │
  │ vmlinux → kernel init        │
  │                              │
  │ /sbin/init → cube-agent      │
  │ ────────────────────────────▶│
  │                              │
  │                              │ ① main.rs:158-189 (init_mode)
  │                              │   - general_mount (proc/sys/dev/pts)
  │                              │   - 运行 /etc/rc.local
  │                              │   - cgroups_mount + hostname 设置
  │                              │
  │                              │ ② config.rs:202-307
  │                              │   - 解析 /proc/cmdline
  │                              │   - agent.server_addr=vsock://-1:1024
  │                              │
  │                              │ ③ rpc.rs:1816-1889 (start)
  │                              │   - 创建 AgentService + HealthService
  │                              │   - 启动 ttrpc server (vsock:1024)
  │                              │
  │                              │ ④ 通知宿主 (I/O port 0x680)
  │                              │   - x86_64: ioport 0x680 write 0x8
  │                              │   - aarch64: SYS_CTRL_MMIO 0x0903_0000
  │                              │   - NotifyEvent::VsockServerReady
  │                              │
```

### 3.2 容器创建

来源: `agent/src/rpc.rs:160-280` (`do_create_container`)

```
CubeShim                      cube-agent                rustjail
  │                              │                        │
  │ CreateContainer(OCI spec)    │                        │
  │ ────────────────────────────▶│                        │
  │                              │                        │
  │                              │ ① rpc.rs:135-148       │
  │                              │   - is_allowed! 宏     │
  │                              │   - 端点白名单检查      │
  │                              │                        │
  │                              │ ② rpc.rs:203-225       │
  │                              │   - add_devices (PCI)  │
  │                              │   - add_storages       │
  │                              │   - 更新命名空间        │
  │                              │   - append_guest_hooks │
  │                              │                        │
  │                              │ ③ rpc.rs:239-253       │
  │                              │   - setup_bundle        │
  │                              │   - overlay mount       │
  │                              │   - 写 config.json     │
  │                              │                        │
  │                              │ ④ rustjail/container   │
  │                              │   - LinuxContainer::new│
  │                              │   - fork() 子进程      │
  │                              │   - set namespace      │
  │                              │   - mount rootfs       │
  │                              │   - drop_privileges    │
  │                              │   - seccomp (可选)     │
  │                              │   - exec.fifo 等待     │
  │                              │                        │
  │ StartContainer               │                        │
  │ ────────────────────────────▶│                        │
  │                              │ ⑤ rpc.rs:283-311       │
  │                              │   - ctr.exec()         │
  │                              │   - 写 exec.fifo       │
  │                              │ ───────────────────▶   │
  │                              │   - 子进程继续执行     │
  │                              │   - exec OCI entrypoint│
  │                              │                        │
```

**关键文件位置**:
- 入口: `agent/src/main.rs:283-320`
- RPC 实现: `agent/src/rpc.rs:690-1674`
- 容器 fork: `agent/rustjail/src/container.rs`
- 进程创建: `agent/rustjail/src/process.rs`
- 权能降级: `agent/rustjail/src/capabilities.rs`
- Seccomp: `agent/rustjail/src/seccomp.rs`
- 配置常量: `agent/src/config.rs:31` (`VSOCK_PORT = 1024`)

### 3.3 I/O 转发流程

```
CubeShim                      cube-agent                容器进程
  │                              │                        │
  │ WriteStdin(data)             │                        │
  │ ────────────────────────────▶│                        │
  │                              │ rpc.rs:610-637         │
  │                              │ do_write_stream:        │
  │                              │   - term_master?       │
  │                              │     → PTY write        │
  │                              │   - parent_stdin?      │
  │                              │     → pipe write       │
  │                              │ ───────────────────▶   │
  │                              │                        │
  │ ReadStdout(len)              │                        │
  │ ◀────────────────────────────│                        │
  │                              │ rpc.rs:640-686         │
  │                              │ do_read_stream:         │
  │                              │   - term_master?       │
  │                              │     → PTY read         │
  │                              │   - parent_stdout      │
  │                              │     → pipe read        │
  │                              │   - select! 监听       │
  │                              │     term_exit_notifier │
  │                              │                        │
```

### 3.4 信号升级流程

来源: `agent/src/rpc.rs:431-508` (`do_signal_process`)

```
Send SIGTERM → 容器 init 进程未处理 SIGTERM?
  │
  ├── 检查 /proc/{pid}/status SigBlk/SigIgn/SigCgt
  │     (is_signal_handled 函数, rpc.rs:1959-2005)
  │
  ├── 未安装 handler → SIGTERM 升级为 SIGKILL
  │
  └── exec_id 为空 → 冻结 cgroup → 向所有进程发信号 → 解冻
```

---

## 4. 路由与端点 (ttRPC AgentService)

来源: `agent/libs/protocols/protos/agent.proto:21-74`

### AgentService RPC

| RPC | 用途 | 请求参数 | 实现方法 (rpc.rs) |
|-----|------|---------|------------------|
| CreateSandbox | 创建 sandbox 命名空间/网络/存储 | CreateSandboxRequest | `create_sandbox` |
| DestroySandbox | 销毁 sandbox 并关机 | DestroySandboxRequest | `destroy_sandbox` |
| CreateContainer | 创建容器 (OCI spec) | CreateContainerRequest | `do_create_container` |
| StartContainer | 启动容器 (写 exec.fifo) | StartContainerRequest | `do_start_container` |
| RemoveContainer | 删除容器 (含超时) | RemoveContainerRequest | `do_remove_container` |
| ExecProcess | 在容器内执行新进程 | ExecProcessRequest | `do_exec_process` |
| SignalProcess | 发送信号 (含 SIGTERM→SIGKILL 升级) | SignalProcessRequest | `do_signal_process` |
| WaitProcess | 等待进程退出 | WaitProcessRequest | `do_wait_process` |
| UpdateContainer | 更新容器资源 | UpdateContainerRequest | `update_container` |
| StatsContainer | 容器统计 (cgroup + 网络) | StatsContainerRequest | `stats_container` |
| PauseContainer | 暂停容器 (cgroup freezer) | PauseContainerRequest | `pause_container` |
| ResumeContainer | 恢复容器 | ResumeContainerRequest | `resume_container` |
| WriteStdin | 写入 stdin | WriteStreamRequest | `do_write_stream` |
| ReadStdout | 读取 stdout | ReadStreamRequest | `do_read_stream(true)` |
| ReadStderr | 读取 stderr | ReadStreamRequest | `do_read_stream(false)` |
| CloseStdin | 关闭 stdin | CloseStdinRequest | `close_stdin` |
| TtyWinResize | 调整终端大小 | TtyWinResizeRequest | `tty_win_resize` |
| UpdateInterface | 更新网络接口 | UpdateInterfaceRequest | `update_interface` |
| UpdateRoutes | 更新路由表 | UpdateRoutesRequest | `update_routes` |
| ListInterfaces | 列出网络接口 | ListInterfacesRequest | `list_interfaces` |
| ListRoutes | 列出路由表 | ListRoutesRequest | `list_routes` |
| AddARPNeighbors | 添加 ARP 邻居 | AddARPNeighborsRequest | `add_arp_neighbors` |
| GetIPTables | 获取 iptables 规则 | GetIPTablesRequest | `get_ip_tables` |
| SetIPTables | 设置 iptables 规则 | SetIPTablesRequest | `set_ip_tables` |
| OnlineCPUMem | CPU/内存热插拔 | OnlineCPUMemRequest | `online_cpu_mem` |
| ReseedRandomDev | 注入熵 | ReseedRandomDevRequest | `reseed_random_dev` |
| GetGuestDetails | 获取 Guest 详情 | GuestDetailsRequest | `get_guest_details` |
| MemHotplugByProbe | 内存热插拔 probe | MemHotplugByProbeRequest | `mem_hotplug_by_probe` |
| SetGuestDateTime | 设置时钟 | SetGuestDateTimeRequest | `set_guest_date_time` |
| CopyFile | 复制文件到 Guest | CopyFileRequest | `copy_file` |
| GetOOMEvent | 获取 OOM 事件 | GetOOMEventRequest | `get_oom_event` |
| GetMetrics | 获取 Prometheus 指标 | GetMetricsRequest | `get_metrics` |
| AddSwap | 添加 Swap 设备 | AddSwapRequest | `add_swap` |
| GetVolumeStats | 卷统计 | VolumeStatsRequest | `get_volume_stats` |
| ResizeVolume | 调整卷大小 | ResizeVolumeRequest | `resize_volume` |

### HealthService RPC

| RPC | 用途 |
|-----|------|
| Check | 健康检查 (返回 SERVING) |
| Version | 版本查询 (agent + grpc version) |

---

## 5. 安全机制

### 5.1 端点白名单

来源: `agent/src/config.rs:55-64` + `agent/src/rpc.rs:135-148`

```rust
// config.rs
pub struct AgentEndpoints {
    pub allowed: HashSet<String>, // 允许的 RPC 方法名
    pub all_allowed: bool,        // 无配置文件时 true
}

// rpc.rs: 每个 RPC 调用检查
macro_rules! is_allowed {
    ($req:ident) => {
        if !AGENT_CONFIG
            .read()
            .await
            .is_allowed_endpoint($req.descriptor_dyn().name())
        {
            return Err(ttrpc_error!(
                ttrpc::Code::UNIMPLEMENTED,
                format!("{} is blocked", $req.descriptor_dyn().name()),
            ));
        }
    };
}
```

**关键点**:
1. 通过 TOML 配置文件中的 `[endpoints].allowed` 列表控制
2. 未提供配置文件时 `all_allowed = true` (默认全部放行)
3. 白名单返回 `UNIMPLEMENTED` 而非 `PERMISSION_DENIED`,避免泄露端点存在性
4. 每 RPC 调用都经过 `is_allowed!` 宏检查

示例配置 (`agent/samples/configuration-all-endpoints.toml`):
```toml
dev_mode = false
server_addr = "vsock://3:1024"

[endpoints]
allowed = ["CreateContainerRequest", "StartContainerRequest", "ExecProcessRequest",
           "SignalProcessRequest", "WaitProcessRequest", "WriteStreamRequest",
           "ReadStreamRequest"]
```

### 5.2 安全特性一览

| # | 特性 | 位置 | 说明 |
|---|------|------|------|
| S1 | **端点白名单** | `config.rs:55-64` + `rpc.rs:135-148` | 限制可调用的 RPC 方法 |
| S2 | **Capabilities 降级** | `rustjail/src/capabilities.rs:48-82` | 按 OCI spec 的 bounding/effective/permitted/inheritable/ambient 降权 |
| S3 | **Seccomp** (可选) | `rustjail/src/seccomp.rs:71-130` | OCI seccomp BPF 过滤 (feature gate: `--features seccomp`) |
| S4 | **RNG reseed** | `src/random.rs:25-51` | 从宿主机注入熵,防熵耗尽 |
| S5 | **CopyFile 路径校验** | `rpc.rs:2032-2084` | 仅限 `/run/cube-containers` 前缀 |
| S6 | **信号升级** | `rpc.rs:431-508` | SIGTERM → SIGKILL (进程未处理时) |
| S7 | **PID/网络/IPC/UTS ns** | `rustjail/src/container.rs` | 命名空间隔离 |
| S8 | **OOM 监控** | `sandbox.rs` + `rustjail/cgroups/notifier.rs` | cgroup 内存压力通知 |
| S9 | **vsock 隔离** | 协议层 | 虚拟机隔离,宿主机不可直接访问 |
| S10 | **SIGCHLD reaper** | `signal.rs:21-81` | PID 1 回收僵尸进程 |
| S11 | **subreaper** | `signal.rs:90` | `set_subreaper(true)` 确保子进程不变成孤儿 |
| S12 | **cgroup freezer** | `rpc.rs:511-522` | 信号发送时冻结/解冻 cgroup |
| S13 | **hostname 设置** | `main.rs:433-441` | 从 `/etc/hostname` 读取,防止注入 |
| S14 | **overlay 只读 rootfs** | `rpc.rs:2179-2207` | 无 writable layer 时 rootfs 只读 |

### 5.3 CopyFile 路径校验

来源: `agent/src/rpc.rs:2032-2084`

```rust
fn do_copy_file(req: &CopyFileRequest) -> Result<()> {
    let path = PathBuf::from(req.path.as_str());

    if !path.starts_with(CONTAINER_BASE) {  // CONTAINER_BASE = "/run/cube-containers"
        return Err(anyhow!(nix::Error::EINVAL));
    }
    // ... write to temp file, verify size, chown, rename
}
```

### 5.4 Seccomp (Feature Gated)

来源: `agent/rustjail/src/seccomp.rs` + `agent/Cargo.toml:91`

- `seccomp` feature 默认在 Makefile 中启用 (`SECCOMP := yes`)
- 实际是否生效依赖两个条件:
  1. 编译时开启 `--features seccomp` (Cargo.toml 的 feature gate)
  2. 运行时宿主机/内核支持 libseccomp
- `rpc.rs:1777-1783` (`have_seccomp()`): 返回 `cfg!(feature = "seccomp")`
- `init_seccomp` 从 OCI spec 解析 seccomp 配置: defaultAction + syscall rules + architectures + flags

### 5.5 Capabilities 降级

来源: `agent/rustjail/src/capabilities.rs:48-82`

```rust
pub fn drop_privileges(cfd_log: RawFd, caps: &LinuxCapabilities) -> Result<()> {
    let all = get_all_caps();

    // bounding: 删除不在 OCI spec bounding 列表中的权能
    for c in all.difference(&to_capshashset(cfd_log, caps.bounding.as_ref())) {
        caps::drop(None, CapSet::Bounding, *c)?;
    }

    // effective / permitted / inheritable / ambient 按 spec 设置
    caps::set(None, CapSet::Effective, &to_capshashset(cfd_log, caps.effective.as_ref()))?;
    // ...
}
```

---

## 6. 配置项

### 6.1 配置结构

来源: `agent/src/config.rs:67-81`

```rust
pub struct AgentConfig {
    pub debug_console: bool,
    pub dev_mode: bool,
    pub log_level: slog::Level,
    pub hotplug_timeout: Duration,
    pub debug_console_vport: i32,
    pub log_vport: i32,
    pub container_pipe_size: i32,
    pub server_addr: String,
    pub unified_cgroup_hierarchy: bool,
    pub tracing: bool,
    pub endpoints: AgentEndpoints,
    pub supports_seccomp: bool,
}
```

### 6.2 配置来源

优先级: **配置文件 > 内核 cmdline > 环境变量 > 默认值**

1. `agent.config_file` cmdline 参数指定 TOML 配置文件路径 (`config.rs:221-224`)
2. `--config` CLI flag 指定配置文件 (`config.rs:204-210`)
3. `/proc/cmdline` 参数 (KVM 启动时传递) (`config.rs:214-285`)
4. 环境变量 (`config.rs:287-302`):
   - `KATA_AGENT_SERVER_ADDR`
   - `KATA_AGENT_LOG_LEVEL`
   - `KATA_AGENT_TRACING`
5. 默认值 (`config.rs:146-161`): `vsock://-1:1024`, `log_level=Info`, `hotplug_timeout=3s`

### 6.3 内核 cmdline 参数

| 参数 | 格式 | 默认值 | 说明 |
|------|------|--------|------|
| `agent.server_addr` | `vsock://<cid>:<port>` | `vsock://-1:1024` | ttrpc 监听地址 |
| `agent.log` | `debug\|info\|warn\|error` | `info` | 日志级别 |
| `agent.hotplug_timeout` | `正整数` (秒) | `3` | 热插拔超时 |
| `agent.debug_console` | flag | `false` | 启用调试控制台 |
| `agent.debug_console_vport` | `正整数` | - | 调试控制台 vsock 端口 |
| `agent.log_vport` | `正整数` | - | 日志转发 vsock 端口 |
| `agent.container_pipe_size` | `非负整数` | `0` (auto) | 容器管道大小 (字节) |
| `agent.trace` | `true\|false` | `false` | OpenTelemetry 追踪 |
| `agent.devmode` | flag | `false` | 开发模式 |
| `agent.unified_cgroup_hierarchy` | `true\|false` | `false` | 统一 cgroup 层级 |
| `agent.config_file` | `/path/to/config.toml` | - | 配置文件路径 |

### 6.4 TOML 配置示例

来源: `agent/samples/configuration-all-endpoints.toml`

```toml
# 最小配置 (全部端点放行)
dev_mode = false
server_addr = "vsock://3:1024"

[endpoints]
allowed = ["CreateContainerRequest", "StartContainerRequest",
           "ExecProcessRequest", "SignalProcessRequest",
           "WaitProcessRequest"]

# 完整配置示例
dev_mode = true
server_addr = "vsock://8:2048"
log_level = "debug"
hotplug_timeout = "10s"
tracing = true

[endpoints]
allowed = [
    "CreateContainerRequest",
    "StartContainerRequest",
    "ExecProcessRequest",
    "SignalProcessRequest",
    "WaitProcessRequest",
    "WriteStreamRequest",
    "ReadStreamRequest",
    "CloseStdinRequest",
    "TtyWinResizeRequest",
    "OnlineCPUMemRequest",
    "ReseedRandomDevRequest",
    "CopyFileRequest"
]
```

---

## 7. 关键文件清单

| 模块 | 路径 | 关键内容 |
|------|------|----------|
| **入口** | `agent/src/main.rs:283` | main + tokio runtime + init |
| **配置** | `agent/src/config.rs:68` | AgentConfig + cmdline/toml 解析 |
| **RPC 实现** | `agent/src/rpc.rs:690` | AgentService trait 实现 |
| **RPC 启动** | `agent/src/rpc.rs:1816` | ttrpc server 创建 + vsock bind |
| **端点白名单** | `agent/src/rpc.rs:135` | `is_allowed!` 宏 |
| **Sandbox** | `agent/src/sandbox.rs:37` | Sandbox 结构体 |
| **设备管理** | `agent/src/device.rs` | 设备发现 + PCI 等待 |
| **存储挂载** | `agent/src/mount.rs` | virtiofs/9p/block 挂载 |
| **RNG** | `agent/src/random.rs:25` | reseed_rng |
| **控制台** | `agent/src/console.rs:50` | debug_console_handler |
| **信号** | `agent/src/signal.rs:83` | setup_signal_handler |
| **网络** | `agent/src/netlink.rs` | rtnetlink 接口 |
| **DNS** | `agent/src/network.rs` | DNS 配置 |
| **PCI** | `agent/src/pci.rs` | PCI 路径解析 |
| **uevent** | `agent/src/uevent.rs` | 设备热插拔监控 |
| **指标** | `agent/src/metrics.rs` | Prometheus 指标 |
| **追踪** | `agent/src/tracer.rs` | OpenTelemetry |
| **时钟** | `agent/src/time.rs` | 时钟同步 |
| **容器** | `agent/rustjail/src/container.rs` | fork + namespace + cgroup |
| **进程** | `agent/rustjail/src/process.rs:70` | Process 结构体 |
| **权能** | `agent/rustjail/src/capabilities.rs:48` | drop_privileges |
| **Seccomp** | `agent/rustjail/src/seccomp.rs:71` | init_seccomp (feature gate) |
| **Rootfs** | `agent/cube/src/rootfs.rs` | overlay/EROFS/pmem |
| **协议** | `agent/libs/protocols/protos/agent.proto` | AgentService 定义 |
| **配置示例** | `agent/samples/configuration-all-endpoints.toml` | 端点白名单示例 |
| **vsock 导出** | `agent/vsock-exporter/src/lib.rs` | OTel vsock 导出 |

---

## 8. 安全注意事项

### 8.1 已知风险

| # | 风险 | 位置 | 等级 |
|---|------|------|------|
| **R1** | **seccomp 默认关闭** | `rustjail/src/seccomp.rs` 有实现但 feature 编译时可关闭 | 🟠 中 |
| **R2** | **默认端点全放行** | `config.rs:304` 无配置文件时 `all_allowed=true` | 🟠 中 |
| **R3** | **默认容器以 root 运行** | 需 OCI spec 显式指定非 root 用户 | 🟡 中 |
| **R4** | **CopyFile 仅路径前缀检查** | `rpc.rs:2035` 仅检查 `/run/cube-containers` 前缀 | 🟢 低 |
| **R5** | **dev_mode 默认 false 但无强制保护** | `config.rs` dev_mode 仅影响部分调试行为 | 🟢 低 |
| **R6** | **iptables 调用使用硬编码路径** | `rpc.rs:101-104` `/sbin/iptables-save` 等硬编码 | 🟢 低 |

### 8.2 端点白名单配置建议

来源: `agent/src/config.rs:303-307`

```rust
// 未提供配置时,所有端点可调用
config.endpoints.all_allowed = true;
```

**建议**: 始终提供配置文件,仅开放必要的 RPC:
```toml
[endpoints]
allowed = ["CreateContainerRequest", "StartContainerRequest", "ExecProcessRequest",
           "SignalProcessRequest", "WaitProcessRequest", "WriteStreamRequest",
           "ReadStreamRequest", "CloseStdinRequest", "TtyWinResizeRequest"]
```

### 8.3 Seccomp 编译配置

来源: `agent/Makefile:34-37` + `agent/Cargo.toml:91`

```makefile
SECCOMP := yes
ifeq ($(SECCOMP),yes)
    override EXTRA_RUSTFEATURES += seccomp
endif
```

- `SECCOMP=yes` (默认): 编译 seccomp 支持,需 libseccomp-dev
- `SECCOMP=no`: 无 seccomp,减少依赖但无 BPF 保护
- `standard-oci-runtime` 特性独立于 seccomp (`STANDARD_OCI_RUNTIME := no`)

### 8.4 容器安全默认值

- **PID 1 隔离**: 每个容器独立 PID namespace (除非 `sandbox_pidns=true`)
- **Capabilities**: 按 OCI spec 严格降级,仅保留 bounding 集内的权能
- **Rootfs**: overlay 方式,无 writable layer 时 rootfs 只读
- **OOM 监控**: 每个容器启动后注册 cgroup 内存压力通知

---

## 9. 与 SVG 边界模型的关系

来源: `mydoc/security-boundaries/` 目录

| SVG 边界 | cube-agent 中的对应 |
|----------|------------------|
| **L5 (Guest 域)** | 整个 cube-agent 进程 (PID 1 init) |
| **L5a (容器域)** | rustjail OCI 容器隔离 (namespace + cgroup) |
| **L4 (host 内核域)** | vsock 通信通道 (VM 隔离) |
| **L7 (可观测性域)** | Prometheus 指标 + OpenTelemetry tracing |

---

## 10. 总结

1. **PID 1 init**: 替代 systemd,专为 sandbox 优化,极简启动路径
2. **Kata 派生**: 继承 Kata Containers agent 的设计,定制 Cube 扩展 (设备模型/rootfs)
3. **vsock 通信**: 安全隔离的 host-guest 通道,无需网络栈
4. **端点白名单**: 最小权限 RPC 控制,默认全放行需配置收紧
5. **rustjail**: 纯 Rust OCI 运行时,无 CGo 依赖,fork+namespace 隔离
6. **静态链接**: musl 构建,无运行时依赖,二进制体积约 15MB
7. **双可观测性**: Prometheus 指标 + OpenTelemetry tracing (vsock 导出)
8. **安全深度防御**: 端点白名单 + capabilities 降级 + seccomp (可选) + namespace + cgroup
9. **信号升级**: SIGTERM → SIGKILL 自动升级,避免容器忽略终止信号
10. **热插拔**: CPU/内存/设备运行时动态添加,无需重启 VM
