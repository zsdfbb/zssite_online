---
title: "T3 KVM CORE BOUNDARY — Host ↔ Guest"
date: 2026-07-06T23:19:20+08:00
draft: false
tags: ["cubesandbox", "security", "T3", "boundary"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/boundaries/t3-kvm-core/"
summary: "一句话定位: **整个 CubeSandbox 安全模型的基石** —— 宿主进程与 Guest OS kernel 之间的硬件级隔离面。"
cover:
  image: ""
  alt: ""
  caption: ""
---
# T3 KVM CORE BOUNDARY — Host ↔ Guest

> 一句话定位: **整个 CubeSandbox 安全模型的基石** —— 宿主进程与 Guest OS kernel 之间的硬件级隔离面。
> 边界类型: 真边界 (信任跃迁) ★核心 —— SVG 中唯一用红色粗虚线绘制的边界,代表"信任跃迁"。
> SVG 位置: 横向贯穿 x=50→1350, y=635 (中央) ,红色粗虚线,标"T3 KVM CORE BOUNDARY"。

## 1. 边界概述

```
   ┌──────────── Host (Privileged-but-minimal) ─────────────┐
   │   L3 host 进程域                                       │
   │   ┌──────────────────────────────────────────────┐    │
   │   │  Cubelet + CubeShim + cube-hypervisor        │    │
   │   │  - seccomp ×3 层 (VMM/shim/agent)            │    │
   │   │  - capability drop                           │    │
   │   │  - no_reaper / no_sub_reaper                 │    │
   │   └──────────────────────────────────────────────┘    │
   │   L4 host 内核域                                       │
   │   - KVM 硬件虚拟化                                     │
   │   - IOMMU / VFIO                                       │
   │   - cgroup v1+v2 / namespace                           │
   │   - grub hardening (module.sig_enforce=1)              │
   └─────────────────╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳──────┘
                     ╳  T3 KVM CORE  (硬件隔离 + virtio)   ╳
                     ╳  - virtio 设备 (id + queue)         ╳
                     ╳  - virtio-pmem (只读 rootfs)       ╳
                     ╳  - virtiofs tag cubeShared         ╳
                     ╳  - vsock (host↔guest 通道)         ╳
   ┌─────────────────╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳╳──────┐
   │   L5 guest OS 域 (UNTRUSTED)                          │
   │   - 独立 guest kernel (非共享)                         │
   │   - cube-agent (OCI 容器运行时)                        │
   │   - guest agent seccomp + capability 5-set             │
   │   - overlayfs / virtiofs                              │
   └────────────────────────────────────────────────────────┘
```

**信任跃迁语义**: T3 是**唯一**的硬件级隔离面,而不是共享内核 namespace。C3 SVG 用红色粗虚线表达这一点——任何穿过 T3 的数据流都必然经过 virtio 总线/vsock/IOMMU 的检查。**这是 CubeSandbox 与传统容器沙箱 (Docker/Kata-classic) 的根本分水岭**。

## 2. 涉及的纵深防御层

| 层 | 名称 | 是否参与 | 在本边界的作用 |
|----|------|---------|--------------|
| L1 | WebUI 域 | ❌ | T3 不经过 WebUI |
| L2 | 控制面域 | ❌ | T3 由沙箱 spec 启动,不直接经 L2 |
| L3 | host 进程域 | ✅★ | cloud-hypervisor seccomp (5 线程) 、CubeShim runtime seccomp、no_reaper、cap drop、vsock 通信 |
| L4 | host 内核域 | ✅★ | KVM、IOMMU、grub hardening、cgroup、namespace、virtio device 隔离 |
| L5 | guest OS 域 | ✅★ | 独立 guest kernel、guest agent seccomp + capability 5-set、shim 过滤 namespace |
| L6 | 存储域 | ✅ | virtio-pmem 只读 rootfs、virtiofs tag `cubeShared`、CubeCoW |
| L7 | 可观测性域 | ✅ | vsock-exporter :10240 (host 主动拉 metrics)、audit |

## 3. 机制清单

### 3.1 L3 (host 进程域) ★

#### 机制: cloud-hypervisor VMM seccomp (5 线程类型)

- **文件位置**: `hypervisor/vmm/src/seccomp_filters.rs:13-895`
- **作用**: 按线程类型分别设置规则,共 5 类:`Api` / `SignalHandler` / `Vcpu` / `Vmm` / `PtyForeground`,聚合 `virtio_device_thread_rules` + `create_runtime_seccomp_rules`。**默认** `SeccompAction::KillProcess`
- **配置/启用**: 通过 `--seccomp` CLI flag (`true | false | log`);`log` 模式需 host `audit=1`
- **与本边界的关联**: T3 的第一道 L3 防御——guest 突破 virtio 后,先撞上 VMM 进程的 seccomp,默认 kill

#### 机制: CubeShim 运行时 seccomp (注入额外 syscall)

- **文件位置**: `CubeShim/shim/src/hypervisor/cube_hypervisor.rs:70-78` `set_runtime_seccomp_rules` 调用
- **作用**: 在 VMM seccomp 基础上,追加 `SYS_mkdir`、`SYS_getsockopt`、`SYS_setsockopt`、`SYS_faccessat2` 等 syscall
- **配置/启用**: 硬编码 (CubeShim 在启动 VMM 前注入)
- **与本边界的关联**: 让 CubeShim 仍能调用这些 syscall 做 snapshot、fs setup,但 guest 不能

#### 机制: CubeShim snapshot 路径上的 seccomp 注入

- **文件位置**: `CubeShim/shim/src/snapshot/mod.rs` (本系列新增,原清单未列)
- **作用**: snapshot 创建/恢复路径同样调用 `set_runtime_seccomp_rules` 注入 `mkdir, getsockopt, setsockopt`
- **配置/启用**: 硬编码
- **与本边界的关联**: T3 上 snapshot 操作的安全约束

#### 机制: no_reaper / no_sub_reaper

- **文件位置**: `CubeShim/shim/src/main.rs:17-22`
- **作用**: shim 进程配置 `no_reaper: true, no_sub_reaper: true` —— 避免沙箱内进程被 host 进程回收或回收 host 进程
- **配置/启用**: 硬编码
- **与本边界的关联**: 即使 guest 进程 PID 命名空间被破坏,也不会逃逸到 host 进程树

#### 机制: capability drop (host 进程)

- **文件位置**: `hypervisor/vmm/src/main.rs` 启动流程 (本系列新增,原清单未列)
- **作用**: VMM 启动后立即调用 `prctl(PR_CAPBSET_DROP, ...)` 删除不需要的 capability,最小权限
- **配置/启用**: 硬编码
- **与本边界的关联**: T3 上 VMM 进程的最小权限

#### 机制: virtio RateLimiter (TokenBucket)

- **文件位置**: `CubeShim/shim/src/hypervisor/config.rs:280-300` (`RateLimiterConfig { bandwidth, ops }`)
- **作用**: virtio 设备 IO 带宽和 OPS 通过 TokenBucket 限流
- **配置/启用**: 在 net / disk annotation 中提供 `qos: { bw_size, bw_one_time_burst, bw_refill_time, ops_size, ... }`
- **与本边界的关联**: T3 中 guest → host 的设备 IO 限速,防止 DoS

### 3.2 L4 (host 内核域) ★

#### 机制: KVM 硬件虚拟化

- **文件位置**: `hypervisor/src/lib.rs`、`hypervisor/src/vmm_config.rs:13-22`
- **作用**: 通过 `/dev/kvm` 直接访问硬件虚拟化;**不使用** QEMU / Firecracker,改用 cloud-hypervisor (vendored `hypervisor/`)
- **配置/启用**: 部署时 `deploy/one-click/install.sh` 检查 `/dev/kvm` 是否存在并 fail-fast
- **与本边界的关联**: T3 的物理基础——guest 通过 KVM 进出 CPU/内存,与 host 完全隔离

#### 机制: IOMMU / VFIO 设备透传

- **文件位置**: `CubeShim/shim/src/sandbox/device.rs:5-7` (`ANNO_VFIO_DISK`, `ANNO_VFIO_NET`) ;`CubeShim/shim/src/sandbox/config.rs:50-55` (`vfio_nets`, `vfio_disks`, `vfio_disk_path_map`)
- **作用**: 支持 VFIO 透传 net / disk 设备,每个设备可独立启用 IOMMU
- **配置/启用**: 通过 `cube.vfio.net` / `cube.vfio.disk` annotation;VMM 配置中 `iommu: bool` 控制单设备 IOMMU
- **与本边界的关联**: T3 的设备隔离——DMA 通过 IOMMU,防止 guest 通过设备直接攻击 host 内存

#### 机制: host grub cmdline 硬化

- **文件位置**: `deploy/pvm/grub/host_grub_config.sh:18-22`
- **作用**: `module.sig_enforce=1` (拒绝未签名内核模块) + `clearcpuid=...` (清除 CPU 漏洞 feature 位) + `pti=off mitigations=on` (关 PTI 开其他缓解) + `kvm.nx_huge_pages=never`
- **配置/启用**: 安装脚本自动合并到现有 grub cmdline
- **与本边界的关联**: T3 的 host 内核安全基线

#### 机制: cgroup v1+v2 双兼容

- **文件位置**: `configs/kernel-oc9.config:1-50` (`CONFIG_CGROUPS=y`、`CONFIG_CGROUP_PIDS=y` 等) ;`deploy/pvm/grub/host_grub_config.sh` (`systemd.unified_cgroup_hierarchy=1`)
- **作用**: cgroup v1 路径用于 CPU、内存、device、pids 限制;`systemd.unified_cgroup_hierarchy=1` 启用 cgroup v2 兼容
- **配置/启用**: 通过 OCI spec `resources.memory.limit` / `cpu` 等
- **与本边界的关联**: T3 中 host 进程的资源隔离

#### 机制: namespace 隔离 (host 侧)

- **文件位置**: `agent/src/namespace.rs:7-50`、`agent/rustjail/src/validator.rs:23-130`
- **作用**: agent 显式 unshare IPC/UTS/PID namespace;**shim 层强制清除 CGROUP/NET/PID namespace** (避免与 host 命名空间冲突,因为这些由 VMM 接管)
- **配置/启用**: OCI spec `linux.namespaces[]` 配置;shim 层固定过滤 cgroup/net/pid
- **与本边界的关联**: T3 上 guest 内的 namespace,确保即便共享内核也能有 IPC/UTS/PID 隔离

#### 机制: virtio 设备独立 id + queue

- **文件位置**: `CubeShim/shim/src/hypervisor/config.rs:46-50` (`VIRTIO_FS_TAG = "cubeShared"`、`VIRTIO_FS_ID = "cube-fs"`) ;`CubeShim/shim/src/hypervisor/config.rs:337-348` (`FsConfig { num_queues: 1, queue_size: 1024 }`)
- **作用**: 每个 virtio 设备 (net, disk, fs) 拥有独立 `id` + 独立 virtio queue,`num_queues=1, queue_size=1024`,Tag 名 `cubeShared`
- **配置/启用**: 通过 `cube.fs` / `cube.virtiofs` annotation
- **与本边界的关联**: T3 上多设备互不干扰,guest 不能通过设备 id 冲突干扰 host

### 3.3 L5 (guest OS 域) ★

#### 机制: 独立 Guest Kernel

- **文件位置**: `README.md:46-50`、`CubeShim/shim/src/hypervisor/config.rs:35-55` (`IMAGE_PATH = "/usr/local/services/cubetoolbox/cube-image/cube-guest-image-cpu.img"`)
- **作用**: 每个沙箱运行独立 Guest OS kernel (非共享内核) —— 消除容器逃逸风险
- **配置/启用**: 引导参数固定 `root=/dev/pmem0 rootflags=dax,errors=remount-ro ro`,只读根,ext4
- **与本边界的关联**: T3 的根本隔离——**核心安全理念**: guest 突破 virtio 后仍需突破独立内核

#### 机制: vCPU / 内存隔离

- **文件位置**: `CubeShim/shim/src/sandbox/config.rs:18` (`ANNO_VM_RES = "cube.vmmres"`) ;`CubeShim/shim/src/sandbox/config.rs:230-240` (`VmResource { cpu, memory, preserve_memory, snap_memory }`)
- **作用**: vCPU 数量、内存大小、CPU 拓扑由 annotation `cube.vmmres` 指定;**不支持热添加**
- **配置/启用**: 沙箱创建 API 提交 `cube.vmmres` annotation
- **与本边界的关联**: T3 的资源隔离

#### 机制: guest agent seccomp

- **文件位置**: `agent/rustjail/src/seccomp.rs:1-100`、`CubeShim/shim/src/container/mod.rs:530-540`
- **作用**: guest 内 `cube-agent` 通过 libseccomp 实现 OCI `LinuxSeccomp` spec 解析,支持 `default_action` / `syscall rules` / `architectures` / `flags (TSYNC, LOG, SPEC_ALLOW)`
- **配置/启用**: OCI spec 中提供 `linux.seccomp` 字段
- **与本边界的关联**: T3 的第三层 seccomp——即便 guest kernel 被攻破,容器进程仍受 seccomp 约束

#### 机制: Linux Capabilities (5-set, OCI 解析)

- **文件位置**: `agent/rustjail/src/capabilities.rs:1-100` (`to_capshashset`、`drop_privileges`)
- **作用**: rustjail 完整实现 **Bounding / Effective / Permitted / Inheritable / Ambient** 5 个 set 的 OCI 解析和 drop。`drop_privileges` 限制进程只能获得 spec 中声明的 cap
- **配置/启用**: OCI spec `linux.capabilities.bounding/effective/permitted/inheritable/ambient`
- **与本边界的关联**: T3 中容器内进程的最小权限

#### 机制: shim namespace 过滤 (跳过 cgroup/net/pid)

- **文件位置**: `CubeShim/shim/src/container/mod.rs:285-302`
- **作用**: shim 启动 OCI 容器时,显式 unshare IPC/UTS/PID namespace;**强制清除 CGROUP/NET/PID namespace** (因为这些由 VMM 接管)
- **配置/启用**: 硬编码
- **与本边界的关联**: T3 上 namespace 隔离的一致性——避免 container namespace 与 VMM namespace 冲突

#### 机制: 启动参数固定 (RO root, mitigations=off, /dev/urandom)

- **文件位置**: `CubeShim/shim/src/hypervisor/config.rs:60-83` (`VmConfig::default()`)
- **作用**: 强制 `root=/dev/pmem0`、`ro`、`audit=0`、`mitigations=off`、`panic=1`、`agent.debug_console`;`rng.src = /dev/urandom`
- **配置/启用**: 通过 annotation `cube.vm.kernel.path` 覆盖 kernel,`cube.vm.kernel.cmdline.append` 追加 cmdline 并做冲突检查
- **与本边界的关联**: T3 中 guest kernel 的启动参数不可被 guest 改写 (只能由 T2 运维修改)

### 3.4 L6 (存储域)

#### 机制: virtio-pmem 只读 rootfs

- **文件位置**: `CubeShim/shim/src/hypervisor/config.rs:35-55`
- **作用**: 沙箱 rootfs 通过 DAX virtio-pmem (`pmem0`) 暴露,**只读**
- **配置/启用**: guest kernel cmdline 固定 `root=/dev/pmem0 ro`
- **与本边界的关联**: T3 上 guest 不能改写自己的 rootfs,即使逃逸到 host 也无法通过改 rootfs 持久化

#### 机制: virtiofs tag `cubeShared`

- **文件位置**: `CubeShim/shim/src/hypervisor/config.rs:337-348` (`FsConfig { id: "cube-fs", tag: "cubeShared", num_queues: 1, queue_size: 1024, ... }`) ;`CubeShim/shim/src/sandbox/config.rs:35-37` (`SHARE_CACHE_ALWAYS=1, SHARE_CACHE_NEVER=2`)
- **作用**: 容器内 rootfs 通过 virtiofs (tag `cubeShared`) 共享,支持 overlay (lower_dir 来源于多个 virtiofs lower)
- **配置/启用**: 容器 spec 的 `mounts` 字段 + cube.agent 的 `cube.rootfs.info` annotation
- **与本边界的关联**: T3 上多 sandbox 共享同一 rootfs 底层,内存高效

#### 机制: OCI 容器兼容 (containerd-shim v2)

- **文件位置**: `CubeShim/shim/Cargo.toml:17-25` (依赖 `containerd-shim=0.9.0`、`containerd-shim-protos=0.9.0`、`ttrpc=0.5.8`) ;`CubeShim/shim/README.md:90-100` (`runtime_type = "io.containerd.cube.v2"`)
- **作用**: CubeShim 实现 containerd-shim v2 API,以 `io.containerd.cube.v2` 注册到 containerd;支持 OCI spec、CRI 安全上下文(`privileged` / `readonly` / `NoNewPrivs` / `capabilities` / `seccomp` / `apparmor`)
- **配置/启用**: `runtime_type` 注册到 containerd
- **与本边界的关联**: T3 的 OCI 兼容层——外部 K8s / containerd 工具链可无缝对接

#### 机制: snapshot/clone 安全 (CUBE_SYS_PATH 强制)

- **文件位置**: `CubeShim/shim/src/snapshot/mod.rs:36-65` (`pub const CUBE_SYS_PATH: &str = "/usr/local/services/cubetoolbox/"`、`FilePtr::Drop`)
- **作用**: CubeShim 强制清理 `CUBE_SYS_PATH` 之外的 snapshot 路径,避免覆盖系统目录;snapshot 类型分 Full / Diff
- **配置/启用**: 通过 annotation `cube.snapshot.disable`、`cube.appsnapshot.create`、`cube.appsnapshot.restore`、`cube.vm.snapshot.base.path`、`cube.vm.snapshot.memory_vol_url`
- **与本边界的关联**: T3 上 snapshot 落盘路径约束

### 3.5 L7 (可观测性域)

#### 机制: vsock 主机-客机通信 (host→guest 主动拉)

- **文件位置**: `CubeShim/shim/src/hypervisor/config.rs:355-360` (`add_vsock(id)` → `Utils::gen_vsock_config(&id)`)
- **作用**: 主机 shim 与 guest cube-agent 通过 **vsock** (CID 主机侧、Guest CID=2) 通信,不走 host 网络
- **配置/启用**: 自动
- **与本边界的关联**: T3 的 host→guest 通道,作为 L7 vsock-exporter 的载体

#### 机制: vsock-exporter :10240 (host 主动拉 metrics)

- **文件位置**: `agent/src/vsock_exporter.rs` (本系列新增,原清单未列) —— guest 内启动 exporter
- **作用**: host 主动通过 vsock 拉取 guest metrics,使用 bincode 序列化
- **配置/启用**: 自动
- **与本边界的关联**: T3 上的 L7 落点;host 不需要从 guest 推数据,主动权在 host

#### 机制: Audit 编译 (但 guest 默认 audit=0)

- **文件位置**: `configs/kernel-oc9.config` (`CONFIG_AUDIT=y`、`CONFIG_AUDITSYSCALL=y`) ;`CubeShim/shim/src/hypervisor/config.rs:64` (`audit=0` 关闭)
- **作用**: guest kernel 启用 `CONFIG_AUDIT=y` 但 cmdline 显式 `audit=0` 关闭 (性能折中)
- **配置/启用**: 双向 —— host 通过 grub 开启;guest 默认关闭以减少开销
- **与本边界的关联**: T3 上 guest 内 audit 子系统编译但默认未运行

## 4. 关键交互

- **数据流入自**:
  - **T2 (Operator Trust)**: 运维替换 cube-shim / cube-hypervisor binary;修改 kernel cmdline;调整 `allowed_host_mount_prefixes`
  - **T1 (CubeAPI ingress)**: 用户提交沙箱 spec,经 gRPC 落入 CubeMaster,再创建 microVM
- **数据流出到**:
  - **T4 (Egress)**: guest 内 sandbox 的出网请求 → 经 host 网络栈 → 走 T4
  - **T5 (CubeProxy inbound)**: 通过暴露端口,公网请求可访问 sandbox
  - **L7 vsock-exporter**: host 主动拉取 metrics
- **同信任域 L 层依赖**: L3 (VMM/shim 进程沙箱) → L4 (内核隔离) → L5 (guest kernel) → L6 (virtio 设备) → L7 (vsock-exporter)

## 5. 设计权衡

1. **为什么 T3 用独立 guest kernel 而不是共享内核 namespace**: 这是 CubeSandbox 与传统容器沙箱的根本分水岭。共享内核 namespace 攻击面 = host 内核 syscall 全部,guest 一旦突破就是 host root;独立 guest kernel 攻击面 = guest 内核的子集 + virtio 设备,且 guest 内核是专用构建的最小内核。**这是用启动延迟换安全性** (微秒级启动实测可用)。
2. **为什么 seccomp 分三层 (VMM / shim / agent)**: 不同进程的攻击面不同——VMM 直接接触 virtio 设备、最危险,需要最严格过滤;shim 与 containerd 通信、需要额外 syscall、过滤稍宽;agent 在 guest 内、过滤 OCI spec。三层独立失效,任何一层被绕过不影响其他层。
3. **为什么 no_reaper/no_sub_reaper 硬编码**: 这是个**安全>灵活**的决定——sub_reaper 会让 shim 接管孤儿进程,但也意味着攻击者可让 shim 回收 host 的孤儿进程,扩大攻击面。直接禁用是更保守的选择。
4. **为什么 guest kernel audit 编译但默认关闭**: audit 会带来 5-15% 性能开销,而 L7 已经有 vsock-exporter 提供主动 metrics;两者功能重叠,优先性能。如果需要 audit,可通过 T2 修改 cmdline 开启 `audit=1`。
5. **为什么 namespace 过滤在 shim 而不是 agent**: shim 是 host 信任域的最后一道关卡,由它决定哪些 namespace 进入 guest。如果让 agent 决定,guest kernel 一旦被攻破,namespace 设置可被篡改。这是"host 决定一切不可信域配置"原则的体现。
6. **为什么 virtio-pmem rootfs 只读**: guest 改写 rootfs 对自己毫无意义 (重启恢复),但对攻击者有意义 (持久化 + 影响其他 sandbox)。只读 + 重启恢复,把持久化攻击的收益降为零。
7. **为什么 `virtiofs tag = cubeShared` 固定**: 防止多 sandbox 间互相通过 virtio tag 访问对方的共享卷。固定 tag + sandbox ID 在 path 中隔离,这是"命名空间命名空间"的纵深防御。