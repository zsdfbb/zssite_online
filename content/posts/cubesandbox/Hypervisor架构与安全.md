---
title: "Hypervisor (cube-hypervisor) 架构、处理流程与安全配置"
date: 2026-07-08T00:00:00+08:00
draft: false
tags: ["cubesandbox"]
categories: ["cubesandbox"]
comments: true
showToc: true
weight: 100
---


> 调研时间: 2026/07/08
> 调研范围: `/home/zs/Develop/TraceCubeSandbox/hypervisor/` 全量 Rust 源码
> 目的: 系统性梳理 cube-hypervisor (KVM VMM) 的架构、处理流程与安全配置
>
> 每节都带文件位置证据,可以直接引用。

---

## 1. 概述

cube-hypervisor 是 CubeSandbox 的 **KVM 虚拟机监视器 (VMM)**,基于开源 Cloud Hypervisor 定制,负责任何 MicroVM 的创建、启动、暂停、恢复、快照等生命周期管理。

| 属性 | 值 |
|------|-----|
| **语言** | Rust 2021 edition (MSRV 1.77.0) |
| **版本** | 28.0.0 |
| **基础项目** | Cloud Hypervisor (开源 VMM) |
| **Hypervisor** | KVM (x86-64 + AArch64) + 可选 MSHV |
| **CLI** | clap 参数解析 |
| **日志** | `/data/log/CubeVmm/vmm.log` |
| **管理 API** | Unix socket HTTP REST API |
| **程序化 API** | VmmInstance (mpsc 通道) |
| **工作空间** | 26 个 crate |

**核心职责**:
- KVM VM 生命周期 (create/boot/pause/resume/shutdown/delete)
- vCPU/内存/设备分配与管理
- virtio 设备模拟 (block/net/console/rng/balloon/vsock/fs)
- 设备热插拔 (CPU/内存/PCI)
- 快照与恢复 (内存 + 设备状态)
- 实时迁移 (live migration)
- 安全隔离 (seccomp + umask + TDX/SGX)

---

## 2. 架构

### 2.1 顶层目录结构

```
hypervisor/
├── Cargo.toml                    # Workspace 根, 26 个成员
├── src/
│   ├── main.rs                   # CLI 入口 + seccomp + VMM 启动
│   ├── lib.rs                    # VmmInstance 导出
│   ├── common.rs                 # 日志配置 + core dump 默认值
│   └── vmm_config.rs             # VmmConfig 结构
├── vmm/                          # 核心 VMM crate
│   └── src/
│       ├── lib.rs                # Vmm 主循环 (epoll)
│       ├── vm.rs                 # Vm 结构体 (生命周期)
│       ├── vm_config.rs          # VmConfig + 子配置
│       ├── cpu.rs                # vCPU 管理 + seccomp
│       ├── memory_manager.rs     # 内存管理 (hugepages/NUMA)
│       ├── device_manager.rs     # 设备管理 + virtio 初始化
│       ├── migration.rs          # 实时迁移 + 快照
│       ├── api/mod.rs            # ApiRequest/ApiResponse 定义
│       ├── api/service.rs        # VMM_SERVICE 全局单例
│       ├── api/http/mod.rs       # HTTP REST API 服务器
│       ├── api/http/http_endpoint.rs # HTTP 端点处理器
│       ├── seccomp_filters.rs    # Seccomp BPF 定义
│       ├── config.rs             # CLI 配置解析
│       ├── coredump.rs           # Guest coredump 支持
│       ├── acpi.rs               # ACPI 表生成
│       ├── interrupt.rs          # 中断管理
│       ├── serial_manager.rs     # 串口管理
│       ├── pci_segment.rs        # PCI 段管理
│       ├── pagemap_anon.rs       # 匿名页映射
│       ├── soft_dirty.rs         # 软脏页跟踪
│       ├── clone3.rs             # clone3 系统调用封装
│       └── gdb.rs                # GDB 调试支持
├── hypervisor/                   # Hypervisor 抽象层
│   ├── src/lib.rs                # HypervisorType enum + new() 工厂
│   ├── src/hypervisor.rs         # Hypervisor trait
│   ├── src/vm.rs                 # Vm/VmOps trait
│   ├── src/cpu.rs                # Vcpu trait + VmExit
│   ├── src/device.rs             # Device trait
│   ├── src/arch/                 # 架构相关 (x86_64 + AArch64)
│   └── src/kvm/                  # KVM 实现 (kvm ioctl 封装)
├── virtio-devices/               # virtio 设备模拟 (block/net/console/rng/fs/vsock)
├── devices/                      # 传统设备 (serial/RTC/i8042/SysCtrl)
├── pci/                          # PCI 总线 + 配置空间
├── net_util/                     # 网络工具 (TAP/MACVTAP)
├── block_util/                   # 块设备工具 (QCOW/VHDX/raw)
├── qcow/ / vhdx/                 # 磁盘格式支持
├── vm-migration/                 # 快照/迁移协议
├── vm-virtio/                    # virtio 队列/传输层
├── vhost_user_block/             # vhost-user 块后端
├── vhost_user_net/               # vhost-user 网络后端
├── virtiofsd/                    # virtio-fs 共享文件系统
├── vfio_user/                    # VFIO user 设备
├── tpm/                          # TPM 设备
├── vm-device/                    # 设备总线抽象
├── vm-allocator/                 # 地址空间分配器
├── acpi_tables/                  # ACPI 表生成
└── docs/                         # 30+ Markdown 文档
```

### 2.2 模块分层

```
┌──────────────────────────────────────────────────────────┐
│  入口层                                                    │
│    • main.rs — CLI parsing + seccomp + VMM 线程启动       │
│    • lib.rs — VmmInstance 封装 (mpsc 通道)                │
├──────────────────────────────────────────────────────────┤
│  VMM 层 (核心控制循环)                                      │
│    • vmm/lib.rs — epoll 事件循环                            │
│    • vmm/vm.rs — VM 生命周期管理                           │
│    • vmm/api/ — ApiRequest + HTTP REST API + service       │
├──────────────────────────────────────────────────────────┤
│  资源管理层                                                │
│    • vmm/cpu.rs — vCPU 线程 + seccomp                     │
│    • vmm/memory_manager.rs — 内存分区 + 大页              │
│    • vmm/device_manager.rs — 总线 + 设备                   │
├──────────────────────────────────────────────────────────┤
│  虚拟设备层                                                │
│    • virtio-devices/ — block/net/console/rng/fs/vsock    │
│    • devices/ — 串口/RTC/键盘/SysCtrl                     │
│    • pci/ — PCI 总线 + 配置空间                            │
├──────────────────────────────────────────────────────────┤
│  Hypervisor 抽象层                                         │
│    • hypervisor/ — KVM/MSHV/Vcpu/Vm trait                │
│    • hypervisor/src/kvm/ — KVM ioctl 封装                 │
│    • hypervisor/src/arch/ — x86_64 + AArch64 架构支持     │
└──────────────────────────────────────────────────────────┘
```

### 2.3 交互关系

```
┌──────────────┐   VmmInstance (mpsc)   ┌──────────────────┐
│   CubeShim   │ ─────────────────────▶│  cube-hypervisor  │
│  (Rust)      │   ApiRequest           │  (KVM VMM)       │
│              │ ◀─────────────────────│                   │
│              │   ApiResponse          │                   │
└──────────────┘                        └────────┬─────────┘
                                                 │ KVM ioctl
                                                 ▼
                                          ┌──────────────┐
                                          │   Linux KVM   │
                                          │  (内核模块)    │
                                          └──────────────┘
```

---

## 3. 处理流程

### 3.1 VM 创建与启动

```
CubeShim                     cube-hypervisor                     KVM
  │                              │                              │
  │ VmmInstance::new()           │                              │
  │ ────────────────────────────▶│                              │
  │                              │                              │
  │                              │ ① src/lib.rs                  │
  │                              │   - 设置日志/seccomp          │
  │                              │   - 创建 api channel          │
  │                              │   - 启动 VMM 线程             │
  │                              │                              │
  │                              │ ② vmm/lib.rs                  │
  │                              │   - epoll 事件循环启动        │
  │                              │   - HTTP API 线程             │
  │                              │                              │
  │ ApiRequest::VmCreate         │                              │
  │ ────────────────────────────▶│                              │
  │                              │ ③ vmm/vm.rs                   │
  │                              │   - hypervisor.create_vm()   │
  │                              │   - KVM_CREATE_VM            │
  │                              │   - 创建内存区域              │
  │                              │ ──────────────────────────▶ │ KVM_CREATE_VM
  │                              │                              │
  │ ApiRequest::VmBoot           │                              │
  │ ────────────────────────────▶│                              │
  │                              │ ④ vmm/cpu.rs                  │
  │                              │   - KVM_CREATE_VCPU          │
  │                              │   - vCPU 线程启动 + seccomp  │
  │                              │                              │
  │                              │ ⑤ vmm/device_manager.rs      │
  │                              │   - virtio 设备创建           │
  │                              │   - vsock/virtio-net/...     │
  │                              │                              │
  │                              │ ⑥ PVH direct boot             │
  │                              │   - 加载内核 vmlinux          │
  │                              │   - 设置 cmdline             │
  │                              │   - KVM_RUN (vCPU 运行)      │
  │                              │                              │
  │ NotifyEvent::VsockServerReady│                              │
  │ ◀───────────────────────────│                              │
  │                              │                              │
```

**关键文件位置**:
- VmmInstance 创建: `hypervisor/src/lib.rs:86-257` (VmmInstance::new)
- VMM 循环: `hypervisor/vmm/src/lib.rs` (start_vmm_thread)
- VM 管理: `hypervisor/vmm/src/vm.rs` (Vm 结构体)
- vCPU 管理: `hypervisor/vmm/src/cpu.rs` (CpuManager)
- 设备管理: `hypervisor/vmm/src/device_manager.rs` (DeviceManager)
- Seccomp: `hypervisor/vmm/src/seccomp_filters.rs` (get_seccomp_filter)

### 3.2 快照创建

来源: `hypervisor/vmm/src/migration.rs`

```
CubeShim                     cube-hypervisor
  │                              │
  │ ApiRequest::VmSnapshot       │
  │ ────────────────────────────▶│
  │                              │
  │                              │ ① 暂停 VM (pause)            │
  │                              │ ② 保存 vCPU 状态              │
  │                              │ ③ 保存内存快照 (逐步复制)     │
  │                              │ ④ 保存设备状态                │
  │                              │    - state.json                │
  │                              │    - config.json               │
  │                              │    - memory 快照文件           │
  │                              │ ⑤ 恢复 VM (resume)            │
  │                              │                              │
  │ SnapshotInfo ◀──────────────│
  │                              │
```

**关键常量**:
```rust
// vmm/src/migration.rs:17-18
pub const SNAPSHOT_STATE_FILE: &str = "state.json";
pub const SNAPSHOT_CONFIG_FILE: &str = "config.json";
```

### 3.3 vCPU 线程安全

来源: `hypervisor/vmm/src/cpu.rs`

```
① CpuManager::create_vcpu()
   - KVM_CREATE_VCPU ioctl
   - 分配 vCPU ID + 事件 fd
   - 启动独立线程

② 每 vCPU 线程:
   - apply_seccomp_filter() — 应用最小 syscall 集
   - KVM_RUN 循环
   - VmExit 事件处理:
     • IoIn/IoOut → PIO 处理
     • MmioRead/MmioWrite → MMIO 处理
     • Hypercall → 超调用处理
     • Shutdown → VM 关闭

③ vCPU 热插拔:
   - 新增 vCPU → 创建新线程 + seccomp
   - 删除 vCPU → 停止线程 + 清理
```

---

## 4. 路由与端点

### 4.1 HTTP REST API (Unix socket)

来源: `hypervisor/vmm/src/api/http/http_endpoint.rs` + `vmm/src/api/http/mod.rs`

前缀: `/api/v1`

| 方法 | 路径 | Handler | 用途 |
|------|------|---------|------|
| PUT | `/api/v1/vm.create` | `VmCreate` | 创建 VM |
| PUT | `/api/v1/vm.boot` | `VmActionHandler(Boot)` | 启动 VM |
| GET | `/api/v1/vm.info` | `VmInfo` | 查询 VM 信息 |
| PUT | `/api/v1/vm.pause` | `VmActionHandler(Pause)` | 暂停 |
| PUT | `/api/v1/vm.resume` | `VmActionHandler(Resume)` | 恢复 |
| PUT | `/api/v1/vm.shutdown` | `VmActionHandler(Shutdown)` | 关闭 |
| PUT | `/api/v1/vm.reboot` | `VmActionHandler(Reboot)` | 重启 |
| PUT | `/api/v1/vm.snapshot` | `VmActionHandler(Snapshot)` | 快照 |
| PUT | `/api/v1/vm.restore` | `VmActionHandler(Restore)` | 恢复 |
| PUT | `/api/v1/vm.migrate` | `VmActionHandler(SendMigration)` | 迁移 |
| PUT | `/api/v1/vm.receive-migration` | `VmActionHandler(ReceiveMigration)` | 接收迁移 |
| PUT | `/api/v1/vm.add-device` | `VmActionHandler(AddDevice)` | 热插拔设备 |
| PUT | `/api/v1/vm.add-disk` | `VmActionHandler(AddDisk)` | 热插拔磁盘 |
| PUT | `/api/v1/vm.add-net` | `VmActionHandler(AddNet)` | 热插拔网络 |
| PUT | `/api/v1/vm.add-fs` | `VmActionHandler(AddFs)` | 热插拔文件系统 |
| PUT | `/api/v1/vm.add-vsock` | `VmActionHandler(AddVsock)` | 热插拔 vsock |
| PUT | `/api/v1/vm.add-pmem` | `VmActionHandler(AddPmem)` | 热插拔持久内存 |
| PUT | `/api/v1/vm.add-user-device` | `VmActionHandler(AddUserDevice)` | VFIO 用户设备 |
| PUT | `/api/v1/vm.remove-device` | `VmActionHandler(RemoveDevice)` | 热移除设备 |
| PUT | `/api/v1/vm.resize` | `VmActionHandler(Resize)` | 调整 CPU/内存 |
| PUT | `/api/v1/vm.resize-zone` | `VmActionHandler(ResizeZone)` | 调整内存区 |
| PUT | `/api/v1/vm.power-button` | `VmActionHandler(PowerButton)` | 模拟电源按钮 |
| GET | `/api/v1/vmm.ping` | `VmmPing` | 探活 |
| PUT | `/api/v1/vmm.shutdown` | `VmmShutdown` | 关闭 VMM |

### 4.2 程序化 API (VmmInstance)

来源: `hypervisor/src/lib.rs:69-358`

```rust
// hypervisor/src/lib.rs
pub struct VmmInstance {
    vmm_thread: Option<JoinHandle<Result<(), VmmError>>>,
}

impl VmmInstance {
    pub fn new(config: VmmConfig) -> Result<Self, Error>;
    pub fn send_request(&self, request: ApiRequest) -> Result<ApiResponse, Error>;
    pub fn join(&mut self) -> Result<(), Error>;
    pub fn join_timeout(&mut self, timeout: Option<Duration>) -> Result<(), Error>;
}
```

### 4.3 VMM_SERVICE 全局单例

来源: `hypervisor/vmm/src/api/service.rs:14-90`

```rust
// vmm/src/api/service.rs:14-16
lazy_static! {
    pub static ref VMM_SERVICE: Mutex<VmmService> = Mutex::new(VmmService::new());
}
```

`VMM_SERVICE` 是一个全局静态单例,管理 VMM 线程与外部之间的 mpsc 通道。`send_request()` 发送 `ApiRequest` 后通过 `EventFd` 通知 VMM 线程,然后阻塞等待 `ApiResponse`。

---

## 5. 安全机制

### 5.1 Seccomp BPF 过滤

来源: `hypervisor/vmm/src/seccomp_filters.rs`

每个线程类型有独立的 seccomp 策略:

```rust
// vmm/src/seccomp_filters.rs:21-28
pub enum Thread {
    Api,             // HTTP API 线程
    SignalHandler,   // 信号处理线程
    Vcpu,            // vCPU 线程 (最小 syscall 集)
    Vmm,             // VMM 控制线程
    PtyForeground,   // PTY 前台线程
    All,             // 通用白名单
}
```

| 线程类型 | syscall 集范围 | 风险等级 |
|----------|---------------|---------|
| `Vcpu` | 最小 — 仅 KVM_RUN 相关 | 🟢 最小 |
| `Vmm` | 适中 — ioctl + mmap + eventfd | 🟢 低 |
| `Api` | 适中 — socket + read/write | 🟢 低 |
| `All` | 通用白名单 | 🟡 中 |
| `SignalHandler` | 最小 — 仅信号处理 | 🟢 最小 |

**seccomp 模式** (CLI `--seccomp`):
| 模式 | 行为 |
|------|------|
| `true` (默认 KillProcess) | 违规 → 进程杀死 |
| `trap` | 违规 → SIGSYS |
| `log` | 违规 → 仅记录日志 |
| `false` (Allow) | 不启用 seccomp |

### 5.2 安全特性

| # | 特性 | 位置 | 说明 |
|---|------|------|------|
| S1 | Seccomp BPF | vmm/src/seccomp_filters.rs | 每线程独立策略,默认 KillProcess |
| S2 | umask(0o077) | src/main.rs:748 | 文件权限加固 (owner-only) |
| S3 | SIGSYS 处理 | src/main.rs:646-652 | seccomp 违规信号捕获 |
| S4 | TDX 支持 | vm_config.rs:102-104, hypervisor/kvm/ | Intel 机密计算 (feature = "tdx") |
| S5 | SGX EPC | vm_config.rs:549, vm_config.rs:642 | Enclave 页面缓存配置 |
| S6 | IOMMU/VFIO | device_manager.rs | DMA 隔离 (PCI IOMMU 段) |
| S7 | Core dump 控制 | src/main.rs:541-565 | 过滤 + limit 2GB |
| S8 | 大页支持 | memory_manager.rs | hugepages 可选,无强制隔离 |
| S9 | 信号处理 | src/lib.rs:216-226 | SIGINT/SIGTERM 屏蔽 + 专用 handler |

### 5.3 Core Dump 配置

来源: `hypervisor/src/common.rs:20-27` + `hypervisor/src/main.rs:541-565`

```rust
// common.rs:20-27
pub fn default_coredump_filter() -> String {
    "0x33".to_string()  // 默认过滤 mask
}
pub fn default_coredump_limit() -> u64 {
    2 * 1024 * 1024 * 1024  // 2GB limit
}
```

**实际应用** (main.rs:560-565):
- 写入 `/proc/self/coredump_filter` (默认 `0x33`)
- `setrlimit(Resource::CORE, 2GB)` 限制大小

### 5.4 内存保护

来源: `hypervisor/vmm/src/memory_manager.rs`

| 特性 | 说明 |
|------|------|
| 只读保护 | KVM 内存区域可设只读标志 |
| 大页 (hugepages) | 可选,通过 MemoryZoneConfig.hugepages 配置 |
| NUMA 亲和性 | MemoryZoneConfig.host_numa_node |
| 共享内存 | MemoryZoneConfig.shared 标志 |
| 软脏页跟踪 | soft_dirty.rs 用于迁移增量复制 |
| 内存气球 | virtio-balloon 动态调整 |

### 5.5 umask 加固

来源: `hypervisor/src/main.rs:748`

```rust
let _ = unsafe { libc::umask(0o077) };
```

所有新创建的文件仅 owner 可读写,组和其他用户无权限。

---

## 6. 配置项

### 6.1 VMM 配置 (VmmConfig)

来源: `hypervisor/src/vmm_config.rs:11-41`

```rust
pub struct VmmConfig {
    pub core_dump: Option<CoreDumpConfig>,
    pub event_monitor: Option<EventMonitorConfig>,
    pub gdb: Option<GdbConfig>,
    pub log_file: String,                    // "/data/log/CubeVmm/vmm.log"
    pub log_json_file: Option<String>,       // "/data/log/CubeVmm/vmm.json"
    pub log_level: LevelFilter,              // "info"
    pub log_stderr: bool,
    pub sandbox_id: String,                  // "cube-hypervisor"
    pub seccomp: SeccompAction,              // KillProcess
    pub event_notifier: Option<EventNotifyConfig>,
    pub http_path: Option<String>,
}
```

| 字段 | 默认值 | 说明 |
|------|--------|------|
| log_file | `/data/log/CubeVmm/vmm.log` | 日志路径 |
| log_json_file | `/data/log/CubeVmm/vmm.json` | JSON 日志路径 |
| log_level | Info | 日志级别 |
| seccomp | KillProcess | seccomp 模式 |
| core_dump | filter `0x33`, limit 2GB | core dump 过滤 |
| sandbox_id | `cube-hypervisor` | 沙箱标识 |
| event_notifier | None | 事件通知通道 (Sender\<NotifyEvent\>) |
| http_path | None | HTTP API Unix socket 路径 |

### 6.2 VM 配置 (VmConfig)

来源: `hypervisor/vmm/src/vm_config.rs`

| 字段 | 类型 | 说明 |
|------|------|------|
| cpus | `CpusConfig` | vCPU 数量/拓扑/亲和性 |
| memory | `MemoryConfig` | 内存大小/大页/hotplug |
| platform | `PlatformConfig` | PCI 段数/IOMMU/serial/uuid/TDX |
| payload | `Option<PayloadConfig>` | 内核路径 + cmdline |
| disks | `Option<Vec<DiskConfig>>` | 块设备列表 |
| nets | `Option<Vec<NetConfig>>` | 网络设备列表 |
| vsock | `Option<VsockConfig>` | vsock 设备 |
| rng | `Option<RngConfig>` | 随机数生成器 |
| balloon | `Option<BalloonConfig>` | 内存气球 |
| fs | `Option<Vec<FsConfig>>` | virtio-fs 共享文件系统 |
| pmem | `Option<Vec<PmemConfig>>` | 持久内存 |
| serial | `Option<ConsoleConfig>` | 控制台配置 |
| console | `Option<ConsoleConfig>` | 控制台配置 |
| devices | `Option<Vec<DeviceConfig>>` | 额外设备 |
| user_devices | `Option<Vec<UserDeviceConfig>>` | VFIO 用户设备 |
| vdpa | `Option<Vec<VdpaConfig>>` | vDPA 设备 |
| sgx_epc | `Option<Vec<SgxEpcConfig>>` | SGX EPC 区域 |
| numa | `Option<Vec<NumaConfig>>` | NUMA 节点配置 |
| tpm | `Option<TpmConfig>` | TPM 设备 |

### 6.3 启动示例

```bash
# 最小启动 (CLI)
./cube-hypervisor --kernel vmlinux --cmdline "console=ttyS0" \
    --disk path=rootfs.img --cpus 1 --memory size=512M \
    --vsock cid=3

# 程序化 (通过 CubeShim)
let instance = VmmInstance::new(config)?;
instance.send_request(ApiRequest::VmCreate(vm_config))?;
instance.send_request(ApiRequest::VmBoot)?;
```

---

## 7. 关键文件清单

| 模块 | 路径 | 关键内容 |
|------|------|----------|
| **入口** | `hypervisor/src/main.rs` | CLI + seccomp + coredump + VMM 线程 |
| **VmmInstance** | `hypervisor/src/lib.rs` | 程序化 API (new/send_request/join) |
| **VMM 循环** | `hypervisor/vmm/src/lib.rs` | epoll 事件循环 + start_vmm_thread |
| **VM 生命周期** | `hypervisor/vmm/src/vm.rs` | Vm 结构体 (create/boot/pause/resume/shutdown) |
| **配置** | `hypervisor/vmm/src/vm_config.rs` | VmConfig + 子配置 (CpusConfig/MemoryConfig/...) |
| **CLI 配置** | `hypervisor/vmm/src/config.rs` | CLI 参数 → VM 配置解析 |
| **VMM 配置** | `hypervisor/src/vmm_config.rs` | VmmConfig 结构 |
| **CPU 管理** | `hypervisor/vmm/src/cpu.rs` | vCPU 管理 + KVM_RUN 循环 |
| **内存管理** | `hypervisor/vmm/src/memory_manager.rs` | 内存区分配/大页/NUMA |
| **设备管理** | `hypervisor/vmm/src/device_manager.rs` | 设备初始化/IOMMU/virtio |
| **迁移/快照** | `hypervisor/vmm/src/migration.rs` | 快照 + 实时迁移 |
| **API 请求** | `hypervisor/vmm/src/api/mod.rs` | ApiRequest/ApiResponse 枚举 |
| **API 服务** | `hypervisor/vmm/src/api/service.rs` | VMM_SERVICE 全局单例 |
| **HTTP API** | `hypervisor/vmm/src/api/http/mod.rs` | HTTP REST API 服务器 |
| **HTTP 端点** | `hypervisor/vmm/src/api/http/http_endpoint.rs` | 端点处理器 |
| **Seccomp** | `hypervisor/vmm/src/seccomp_filters.rs` | BPF 过滤规则 (6 线程) |
| **Core dump** | `hypervisor/vmm/src/coredump.rs` | Guest dump 支持 |
| **Hypervisor trait** | `hypervisor/hypervisor/src/hypervisor.rs` | Hypervisor 抽象接口 |
| **Vm trait** | `hypervisor/hypervisor/src/vm.rs` | Vm/VmOps trait |
| **KVM 实现** | `hypervisor/hypervisor/src/kvm/` | KVM ioctl 封装 |
| **Virtio 设备** | `hypervisor/virtio-devices/` | block/net/console/rng/fs/vsock |
| **传统设备** | `hypervisor/devices/src/` | 串口/RTC/i8042/SysCtrl |
| **PCI** | `hypervisor/pci/` | PCI 总线 + 配置空间 |
| **日志配置** | `hypervisor/src/common.rs` | 日志路径/core dump 默认值 |
| **中断管理** | `hypervisor/vmm/src/interrupt.rs` | GSI/MSI 中断 |
| **ACPI** | `hypervisor/vmm/src/acpi.rs` | ACPI 表生成 |
| **串口管理** | `hypervisor/vmm/src/serial_manager.rs` | 串口多路复用 |

---

## 8. API 请求枚举

来源: `hypervisor/vmm/src/api/mod.rs`

```rust
pub enum ApiRequest {
    VmCreate(Box<VmConfig>),
    VmBoot,
    VmDelete,
    VmInfo,
    VmPause,
    VmResume,
    VmShutdown,
    VmReboot,
    VmSnapshot(SnapshotConfig),
    VmRestore(Option<RestoreConfig>),
    VmSendMigration(VmSendMigrationData),
    VmReceiveMigration(VmReceiveMigrationData),
    VmAddDevice(Box<DeviceConfig>),
    VmAddDisk(Box<DiskConfig>),
    VmAddFs(Box<FsConfig>),
    VmAddNet(Box<NetConfig>),
    VmAddPmem(Box<PmemConfig>),
    VmAddVsock(Box<VsockConfig>),
    VmAddUserDevice(Box<UserDeviceConfig>),
    VmAddVdpa(Box<VdpaConfig>),
    VmRemoveDevice(Box<PciBdf>),
    VmResize(VmResizeData),
    VmResizeZone(VmResizeZoneData),
    VmCounters,
    VmPowerButton,
    VmmPing,
    VmmShutdown,
    // ... (guest_debug 特性相关)
}
```

---

## 9. 安全注意事项

### 9.1 已知风险

| # | 风险 | 位置 | 等级 |
|---|------|------|------|
| R1 | seccomp 可配置为禁用 | main.rs:633 `--seccomp false` → Allow | 🟠 中 — 默认 KillProcess,但可关闭 |
| R2 | 大页内存未强制隔离 | memory_manager.rs | 🟡 低 — hugepages 是可选功能 |
| R3 | VFIO 设备透传风险 | device_manager.rs | 🟠 中 — IOMMU 隔离依赖硬件 |
| R4 | HTTP API 无认证 | vmm/src/api/http/ | 🟠 中 — Unix socket 权限依赖文件系统 |
| R5 | 快照文件保护依赖 umask | migration.rs | 🟡 低 — umask 0o077 保护文件权限 |

### 9.2 Seccomp 可配置风险

来源: `hypervisor/src/main.rs:631-643`

```rust
let seccomp_action = if let Some(seccomp_value) = cmd_arguments.get_one::<String>("seccomp") {
    match seccomp_value as &str {
        "true" => SeccompAction::Trap,
        "false" => SeccompAction::Allow,     // ⚠️ 可完全禁用 seccomp
        "log" => SeccompAction::Log,
        "process" => SeccompAction::KillProcess,
        // ...
    }
};
```

虽然默认值是 `KillProcess`,但 `--seccomp false` 可完全禁用 seccomp 过滤。生产部署**不应**传递此参数。

### 9.3 API 安全

- HTTP API 通过 Unix socket 暴露 (默认 `--api-socket path`)
- 无内置认证机制 — 安全依赖 socket 文件权限
- CubeShim 通过 `VmmInstance` (mpsc 通道) 调用,绕过 HTTP 层

### 9.4 信息泄露面

- seccomp `log` 模式可能通过日志泄露 syscall 模式
- 快照文件包含完整 guest 内存状态,需文件权限保护
- GDB 调试功能 (feature = "guest_debug") 可能暴露 guest 寄存器状态

---

## 10. 与 SVG 边界模型的关系

| SVG 边界 | cube-hypervisor 中的对应 |
|----------|------------------|
| L3 (host 进程域) | cube-hypervisor 进程本身 (seccomp + umask) |
| L4 (host 内核域) | KVM ioctl + eBPF TC |
| L5 (Guest 域) | KVM 隔离 + virtio 设备模拟 |
| L6 (硬件域) | IOMMU/VFIO + TDX/SGX 机密计算 |

---

## 11. 特殊特性

### 11.1 日志系统

来源: `hypervisor/src/common.rs:36-257`

cube-hypervisor 使用自定义 `Logger` 实现,支持:
- 异步日志 (defer_logger_thread): 初始化阶段缓冲日志,vCPU 启动后切换异步线程
- 日志轮转: 通过 `LOG_CTRL_REOPEN` 目标触发文件重开
- 格式: `{sandbox_id} --- {ms} --- {level} --- <{thread}> {file}:{line} -- {msg}`

### 11.2 Hypervisor 抽象层

来源: `hypervisor/hypervisor/src/lib.rs:71-85`

```rust
pub fn new() -> std::result::Result<Arc<dyn Hypervisor>, HypervisorError> {
    #[cfg(feature = "kvm")]
    if kvm::KvmHypervisor::is_available()? {
        return kvm::KvmHypervisor::new();
    }
    #[cfg(feature = "mshv")]
    if mshv::MshvHypervisor::is_available()? {
        return mshv::MshvHypervisor::new();
    }
    Err(HypervisorError::HypervisorCreate(anyhow!("no supported hypervisor")))
}
```

支持多种 Hypervisor 后端:
- **KVM** (Linux 默认, feature = "kvm")
- **MSHV** (Microsoft Hypervisor, feature = "mshv", x86_64 only)
- **KVM-PVM** (KVM para-virtualized mode)

### 11.3 TDX / SGX 支持

- **TDX** (Intel Trust Domain Extensions): 通过 `#[cfg(feature = "tdx")]` 条件编译,在 `PlatformConfig` 中配置 `tdx: bool`,使用 `KVM_MEMORY_ENCRYPT_OP` ioctl
- **SGX EPC** (Intel Software Guard Extensions Enclave Page Cache): 通过 `SgxEpcConfig` 结构配置 EPC 区域大小

---

## 12. 总结

1. **Cloud Hypervisor 分支**: 继承 Cloud Hypervisor 生态 (Rust, KVM 原生, virtio 设备栈)
2. **KVM 原生性能**: 直接 KVM ioctl,无额外抽象层
3. **每线程 seccomp**: 6 种线程各有独立 syscall 白名单
4. **快照/迁移**: 内存 + 设备状态完整序列化 (state.json + config.json)
5. **TDX/SGX**: 支持机密计算场景
6. **双 API**: HTTP REST (Unix socket) + 程序化 VmmInstance (mpsc)
7. **Hypervisor 抽象**: 支持 KVM/MSHV 多后端切换
8. **umask 加固**: 所有文件默认仅 owner 访问
9. **core dump 控制**: filter + limit 防止敏感信息泄露
