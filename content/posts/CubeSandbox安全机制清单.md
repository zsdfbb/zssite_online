---
title: "CubeSandbox 安全机制清单"
date: 2026-07-06T23:21:04+08:00
draft: false
tags: ["cubesandbox", "security"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/checklist/"
summary: "调研时间: 2026/07/03"
cover:
  image: ""
  alt: ""
  caption: ""
---
# CubeSandbox 安全机制清单

> 调研时间: 2026/07/03
> 调研范围: `/home/zs/Develop/AI_inference/CubeSandbox` 全量源码 + 配置 + 文档
> 目的: 系统性梳理 CubeSandbox(腾讯开源 microVM 沙箱)使用的所有安全机制,供 `Agent Sandbox学习计划` Phase 1 / 2 / 2' 参考
> 每条机制都带文件位置证据,可以直接引用

---

## 系列文档:按安全边界

> 本清单按"机制主题"组织(虚拟化/内核/运行时/API/部署),便于查"机制 X 是什么"。与之**正交**的另一个视角是按"安全边界"组织,便于查"边界 Y 上有哪些机制"——后者已抽到独立系列,本节为索引入口。
>
> 系列定位、与 SVG 的对应关系、阅读路径详见 [`security-boundaries/README.md`]({{< relref "README.md" >}})。

| 真边界 | 名称 | 一句话 | 文档 |
|--------|------|--------|------|
| **T1** | CubeAPI ingress | 外部 → CubeAPI 访问入口(认证 / 速率限制) | [T1-cubeapi-ingress.md]({{< relref "T1-cubeapi-ingress.md" >}}) |
| **T2** | Operator Trust | 运维 → 配置 / 镜像 / 二进制 / host-mount 路径 | [T2-operator-trust.md]({{< relref "T2-operator-trust.md" >}}) |
| **T3** | KVM CORE BOUNDARY ★ | Host ↔ Guest (独立 kernel + 3 层 seccomp + virtio) | [T3-kvm-core-boundary.md]({{< relref "T3-kvm-core-boundary.md" >}}) |
| **T4** | Egress | Guest → 互联网 (eBPF + TPROXY + L7 MITM) | [T4-egress.md]({{< relref "T4-egress.md" >}}) |
| **T5** | CubeProxy inbound | 互联网 → Sandbox (`*.cube.app` + token + 暴露端口) | [T5-cubeproxy-inbound.md]({{< relref "T5-cubeproxy-inbound.md" >}}) |

> 系列文档反映 **2026/07 修订** 后的安全边界模型,与下方 SVG 一一对应;每篇按"L1-L7 中哪些参与 + 每个机制的文件位置 / 作用 / 配置"统一模板展开。本清单的机制章节 (§1-§5) 仍是机制定义的权威来源,系列文档是边界视角的入口索引。

---

## 系统安全边界与安全机制位置总览

![系统安全边界总览](/images/系统安全边界总览.svg)

> 上图使用 Claude Official 风格重绘，暖色调背景，**5 个真边界 + 7 层纵深防御 + 3 个横切关注点** 以虚线框分层堆叠；蓝/青/米/灰四色语义着色区分组件类型；实线/虚线/蓝色实线三类箭头区分数据流、配置关系和穿层调用。

### 安全边界总结（2026/07 修订）

> 把原来的 7 层重命名为「5 个真边界 (T1–T5) + 7 层纵深防御 (L1–L7) + 3 个横切关注点」。原 Layer 3/4/6 是同信任域内的加固,不是真边界;原 Layer 1 被拆为 T1 (外部→CubeAPI) 和 L1 (WebUI session);原缺失的 CubeProxy 入站 / operator 信任 / 存储 CoW / 可观测性 egress 补齐为 T2 / T5 + L6 / L7。详细评估见 `mydoc/security-docs/00-boundary-model-review-2026-07.md`（待补）。

#### 真边界（信任跃迁）

| # | 边界 | 位置 | 核心安全机制 | 文档编号 |
|---|------|------|-------------|----------|
| **T1** | 外部 → CubeAPI | ingress | auth callback、rate limit、bearer token、回调透传 `X-Request-Path` | 4.1 / 4.2 |
| **T2** | 运维 → 配置 / 镜像 ⚠️ | operator trust | 当前缺失:建议 config 签名 + 镜像签名 + template chain-of-trust + hotswap 受限字段 | (新增) |
| **T3** | Host ↔ Guest（**KVM 核心**） | KVM boundary | KVM microVM、独立 guest kernel、virtio 设备隔离、IOMMU/VFIO | 1.1 / 1.2 / 1.3 / 1.4 / 1.5 / 1.6 |
| **T4** | Guest → 互联网 | egress | 独立 TAP、eBPF L3/L4 (CubeVS)、TPROXY L7 MITM (CubeEgress)、credential inject | 3.4.1 / 3.4.2 / 3.4.4 |
| **T5** | 互联网 → Sandbox | CubeProxy inbound | `*.cube.app` 通配 DNS、`traffic_access_token` 校验、nginx shared cache、Redis 元数据 | (新增) |

#### 纵深防御层（同信任域内加固）

| # | 层级 | 域 | 核心安全机制 | 文档编号 |
|---|------|----|-------------|----------|
| **L1** | WebUI 域 | WebUI 前端 + CubeAPI `/auth/*` | DB session、24h TTL、客户端路由守卫 ⚠️ 服务端 middleware 缺失 | 4.4 / 4.6 |
| **L2** | 控制面域 | CubeMaster + Redis + MySQL | 多租户 containerd namespace、Redis TTL 安全、hotswap reload ⚠️ race | 4.5 / 4.7 |
| **L3** | host 进程域 | Cubelet + CubeShim + cube-hypervisor | seccomp ×3 层、no_reaper、vsock、rate limiter、capability drop | 2.1.1 / 2.1.2 / 3.6 / 3.7 / 3.8 |
| **L4** | host 内核域 | Host OS Kernel | grub 硬化、cgroup、namespace 过滤、eBPF (CubeVS)、TAP sysctl | 2.3 / 2.4 / 2.5 / 2.6 / 3.4.1 / 3.4.3 |
| **L5** | guest OS 域 | Guest Kernel + cube-agent + OCI 容器 | guest agent seccomp、capability 5-set、overlayfs / virtiofs | 2.1.3 / 2.2 / 2.4 / 3.5 |
| **L6** | 存储域 ⚠️ | CoW 池 + virtiofs lower_dir + host-mount allowlist | virtio-pmem rootfs、virtiofs tag、`allowed_host_mount_prefixes`、CubeCoW FICLONE | 3.5 / 1.6 / hostdir_mount |
| **L7** | 可观测性域 ⚠️ | vsock-exporter + logs + audit | 端口 10240 主动拉、bincode 序列化、Audit ⚠️ 无 quota / 无 redaction | (新增) |

#### 横切关注点（Cross-cutting）

| 关注点 | 描述 | 涉及层级 |
|--------|------|----------|
| **多租户隔离** | 跨所有层的横切维度:API key → containerd ns → guest kernel → TAP → Redis key → mount prefix 必须 tenant-scoped | T1 / T3 / T4 / T5 + L1 / L2 / L6 |
| **guest-to-guest 经 host** | 同一 host 进程 (Cubelet/shim/cube-hypervisor) 服务多 sandbox,A 逃逸到 host 进程后可读 B 的元数据 / 卷 / 快照 | T3 完整性依赖 L2 / L3 |
| **fail-closed 默认** | `auth_callback_url=None` 默认全放行;`allowed_host_mount_prefixes` 默认 `/data/shared/` 通常不存在;WebUI 默认 admin/admin;MySQL `cube_pass` / Redis `ceuhvu123` 默认密码 | T1 / T2 / L1 / L6 启动校验 |

### 设计理念

- **纵深防御**:5 个真边界 + 7 层纵深防御各自独立失效。攻击者需要突破多层才能造成实质损害
- **真边界全在 host 信任域**:T1/T2/T3/T4/T5 五个跃迁点都位于 host 侧,因为 **guest = untrusted** 是本项目的根本前提
- **三层 seccomp** 覆盖主机→VMM→Guest 完整链路 (L3 host 进程 + L4 host 内核 + L5 guest agent)
- **网络双保险**:L4 eBPF (L3/L4 线速) + T4 TPROXY (L7 透明代理)
- **KVM 为核心**:T3 用独立 guest kernel 替代共享内核 namespace,容器逃逸面从 1 收敛到 0
- **eBPF 必须放 host 内核**:策略执行点必须在不可信域之外,否则 guest root 可 `bpftool prog detach` 卸载/替换策略

### 已知实现缺陷（按优先级）

> 以下缺陷需要在文档之外单独修复。完整清单见 `mydoc/security-docs/` 待办或 GitHub Issues。

| 优先级 | 编号 | 缺陷 | 位置 |
|--------|------|------|------|
| 🔴 Critical | C1 | `cube.master.*` annotation 全量透传到 shim,调用者可换内核 / 追加 cmdline | `CubeMaster/pkg/service/sandbox/util.go:680-686` |
| 🔴 Critical | C2 | `validateHostPath` 纯字符串校验,无 inode / symlink 强制,TOCTOU | `CubeMaster/pkg/service/sandbox/hostdir_mount.go:114-124` |
| 🟠 High | C3 | AgentHub `rsync -a --delete` 跟随 symlink | `CubeAPI/src/handlers/agenthub.rs:858-899` |
| 🟠 High | C4 | auth callback 默认关闭 → 所有请求放行 | `CubeAPI/src/middleware/auth.rs:53` |
| 🟠 High | C5 | WebUI admin/admin + 客户端守卫 (无服务端 middleware) | `CubeAPI/src/handlers/auth.rs:6-9,98` |
| 🟠 High | C6 | 默认凭据 cube_pass / ceuhvu123 写在仓库里 | `CubeMaster/conf.yaml:48,60` |
| 🟠 High | C7 | `AllowPublicTraffic` 旧数据默认 public,无迁移脚本 | `CubeProxy/lua/sandbox_backend.lua:164-171` |
| 🟡 Medium | C8-C12 | `cfg` 重载 race、IsAbs 用 raw input、mount 数量无上限、大小写不敏感 FS 可逃逸、path 不存在无检查 | `CubeMaster/pkg/base/config/config.go:1186-1193` 等 |

---

## 目录

- [1. 虚拟化层 (Virtualization)](#1-虚拟化层-virtualization)
- [2. 内核层 (Kernel)](#2-内核层-kernel)
- [3. 运行时层 (Runtime)](#3-运行时层-runtime)
- [4. API 层 (API)](#4-api-层-api)
- [5. 部署与监控](#5-部署与监控)
- [6. 总结:四层防御链](#6-总结四层防御链)

---

## 1. 虚拟化层 (Virtualization)

### 1.1 cloud-hypervisor (KVM microVM) 作主 VMM

- **机制**: 整个沙箱使用 **cloud-hypervisor**(Tencent 内部名为 cube-hypervisor,vendored 在 `hypervisor/` 目录)作为 VMM,以 KVM MicroVM 形式运行,**不使用** QEMU / Firecracker
- **依据**:
  - `hypervisor/src/lib.rs` — `use seccompiler::SeccompAction;` 表明 hypervisor 进程使用 seccomp 保护
  - `hypervisor/src/vmm_config.rs:13-22` — `pub seccomp: SeccompAction, seccomp: SeccompAction::KillProcess,`(默认动作)
  - `README.md:1-50` — "built on RustVMM and KVM",微秒级启动
  - `docs/architecture/overview.md` — "CubeHypervisor ... manages KVM MicroVMs"
- **配置/启用**: 通过 `/dev/kvm` 直接访问硬件虚拟化;部署时由 `deploy/one-click/install.sh` 与 `online-install.sh` 检查 `/dev/kvm` 是否存在并 fail-fast 退出

### 1.2 独立 Guest Kernel(消除共享内核风险)

- **机制**: 每个沙箱运行独立 Guest OS kernel(非共享内核),消除容器逃逸风险
- **依据**:
  - `README.md:46-50` — "True kernel-level isolation: No more unsafe Docker shared-kernel (Namespace) hacks. Each Agent runs with its own dedicated Guest OS kernel"
  - `CubeShim/shim/src/hypervisor/config.rs:35-55` — `IMAGE_PATH = "/usr/local/services/cubetoolbox/cube-image/cube-guest-image-cpu.img"`,Guest rootfs 挂在 `/dev/pmem0`
- **配置/启用**: 引导参数固定为 `root=/dev/pmem0 rootflags=dax,errors=remount-ro ro`,只读根,ext4

### 1.3 vCPU / 内存隔离

- **机制**: vCPU 数量、内存大小、CPU 拓扑由 annotation `cube.vmmres` 指定;不支持热添加(由 shim 限制)
- **依据**:
  - `CubeShim/shim/src/sandbox/config.rs:18` — `pub const ANNO_VM_RES: &str = "cube.vmmres";`
  - `CubeShim/shim/src/sandbox/config.rs:230-240` — `VmResource { cpu: u32, memory: u64, preserve_memory: u64, snap_memory: u64 }`
  - `CubeShim/shim/src/hypervisor/config.rs:108-130` — `vcpus.max_vcpus = self.vcpus as u8;` + `CpuTopology { threads_per_core: 1, cores_per_die: self.vcpus as u8, ... }`
  - 内存: `vc.memory.size = self.memory_size * MI_B;`,粒度 MB
- **配置/启用**: 沙箱创建 API 提交 `cube.vmmres` annotation

### 1.4 virtio 设备隔离

- **机制**: 每个设备 (net, disk, fs) 拥有独立 `id` + 独立 virtio queue,`num_queues=1, queue_size=1024`,Tag 名 `cubeShared`
- **依据**:
  - `CubeShim/shim/src/hypervisor/config.rs:46-50` — `pub const VIRTIO_FS_TAG: &str = "cubeShared"; pub const VIRTIO_FS_ID: &str = "cube-fs";`
  - `CubeShim/shim/src/hypervisor/config.rs:337-348` — `FsConfig { id: VIRTIO_FS_ID, tag: VIRTIO_FS_TAG, num_queues: 1, queue_size: 1024, ... }`
  - `CubeShim/shim/src/hypervisor/config.rs:280-300` — net 设备的 `id` 命名空间 `format!("{}-{}", utils::NET_DEVICE_ID_PRE, nets.len())`
- **配置/启用**: 通过 `cube.fs` / `cube.virtiofs` annotation 配置

### 1.5 设备透传 (IOMMU / VFIO)

- **机制**: 支持 VFIO 透传 net / disk 设备,每个设备可独立启用 IOMMU
- **依据**:
  - `CubeShim/shim/src/sandbox/device.rs:5-7` — `pub const ANNO_VFIO_DISK: &str = "cube.vfio.disk"; pub const ANNO_VFIO_NET: &str = "cube.vfio.net";`
  - `CubeShim/shim/src/sandbox/config.rs:50-55` — `pub vfio_nets: Vec<Device>, pub vfio_disks: Vec<DeviceDisk>, pub vfio_disk_path_map: HashMap<String, u32>,`
  - `CubeShim/shim/docs/shimapi/rollback-snapshot.md` — 设备配置中 `iommu: bool` 字段默认 `false`
- **配置/启用**: 通过 `cube.vfio.net` / `cube.vfio.disk` annotation 启用;VMM 配置中 `iommu: bool` 控制单设备 IOMMU

### 1.6 启动流程安全配置

- **机制**: 默认使用 read-only root,禁用 mitigations(性能 vs 安全的折中),使用 `/dev/urandom` 作为熵源
- **依据**:
  - `CubeShim/shim/src/hypervisor/config.rs:60-83` — `VmConfig::default()` 强制 `root=/dev/pmem0`、`ro`、`audit=0`、`mitigations=off`、`panic=1`、`agent.debug_console`
  - `CubeShim/shim/src/hypervisor/config.rs:84-88` — `rng: RngConfig { src: PathBuf::from("/dev/urandom"), iommu: false }`
- **配置/启用**: 通过 annotation `cube.vm.kernel.path` 覆盖 kernel,`cube.vm.kernel.cmdline.append` 追加额外 cmdline,并做冲突检查

### 1.7 virtio Rate Limiter (TokenBucket)

- **机制**: virtio 设备 IO 带宽和 OPS 通过 TokenBucket 进行限流
- **依据**:
  - `CubeShim/shim/src/hypervisor/config.rs:280-300` — `RateLimiterConfig { bandwidth: TokenBucketConfig, ops: TokenBucketConfig }`
  - `CubeShim/shim/src/sandbox/config.rs:201-210` — `Fs` 与 `VirtioFs` 结构中均带 `rate_limiter_config`
- **配置/启用**: 在 net / disk annotation 中提供 `qos: { bw_size, bw_one_time_burst, bw_refill_time, ops_size, ... }`

---

## 2. 内核层 (Kernel)

### 2.1 seccomp / seccomp-bpf (三层部署)

CubeSandbox 的 seccomp 在三个层面都做了部署,这是它纵深防御的核心。

#### 2.1.1 VMM 进程 (host) — cloud-hypervisor

- **机制**: 按线程类型分别设置规则,共 5 类:`Api` / `SignalHandler` / `Vcpu` / `Vmm` / `PtyForeground`,聚合 `virtio_device_thread_rules` + `create_runtime_seccomp_rules`。**默认** `SeccompAction::KillProcess`
- **依据**:
  - `hypervisor/vmm/src/seccomp_filters.rs:13-15` — `use seccompiler::{... SeccompFilter, SeccompRule};`
  - `hypervisor/vmm/src/seccomp_filters.rs:840-895` — `thread_rules()` 聚合各线程规则
  - `hypervisor/src/vmm_config.rs:13-22` — `pub seccomp: SeccompAction, seccomp: SeccompAction::KillProcess`
  - `hypervisor/src/main.rs` — `--seccomp` CLI flag,支持 `true | false | log`
  - `hypervisor/docs/seccomp.md` — 项目级 seccomp 文档
- **配置/启用**: 默认开启。可通过 `--seccomp false` 关闭,`--seccomp log` 记录违规 syscall(需 host `audit=1`)

#### 2.1.2 CubeShim 运行时 (host)

- **机制**: CubeShim 通过 `set_runtime_seccomp_rules` 注入额外 syscalls 到 seccomp 白名单
- **依据**:
  - `CubeShim/shim/src/hypervisor/cube_hypervisor.rs:70-78` — `cube_hypervisor::set_runtime_seccomp_rules(vec![(libc::SYS_mkdir, vec![]), (libc::SYS_getsockopt, vec![]), (libc::SYS_setsockopt, vec![]), (libc::SYS_faccessat2, vec![])]);`
  - `CubeShim/shim/src/snapshot/mod.rs` — 同样调用 `set_runtime_seccomp_rules` 注入 `mkdir, getsockopt, setsockopt`
  - `hypervisor/vmm/src/seccomp_filters.rs:870-880` — `create_runtime_seccomp_rules` / `set_runtime_seccomp_rules` API

#### 2.1.3 Guest Agent (guest 内)

- **机制**: Guest 内 `cube-agent` 通过 libseccomp 实现 OCI `LinuxSeccomp` spec 解析,支持 `default_action` / `syscall rules` / `architectures` / `flags (TSYNC, LOG, SPEC_ALLOW)`
- **依据**:
  - `agent/rustjail/src/seccomp.rs:1-100` — `init_seccomp(scmp: &LinuxSeccomp) -> Result<()>`,`get_filter_attr_from_flag`(支持 `SECCOMP_FILTER_FLAG_TSYNC|LOG|SPEC_ALLOW`),`set_ctl_nnp(false)`
  - `CubeShim/shim/src/container/mod.rs:530-540` — 注释明确 "the VM's seccomp policy and triggers SIGSYS"

### 2.2 Linux Capabilities (细粒度)

- **机制**: rustjail 完整实现 Linux capabilities(**Bounding / Effective / Permitted / Inheritable / Ambient**)5 个 set 的 OCI 解析和 drop。`drop_privileges` 限制进程只能获得 spec 中声明的 cap
- **依据**:
  - `agent/rustjail/src/capabilities.rs:1-100` — `to_capshashset`、`drop_privileges(cfd_log, caps: &LinuxCapabilities)` 操作 5 个 cap set
  - `Cubelet/pkg/container/capability/capability.go` — 主机侧基于 `cap.Current()` + 容器 spec 编译 cap 列表
- **配置/启用**: OCI spec `linux.capabilities.bounding/effective/permitted/inheritable/ambient`

### 2.3 cgroups (资源限制)

- **机制**: cgroup v1 路径(`CONFIG_CGROUP_PIDS`、`CONFIG_CGROUP_SCHED`、`CONFIG_CGROUP_DEVICE`、`CONFIG_CGROUP_CPUACCT` 等)用于 CPU、内存、device、pids 限制。`systemd.unified_cgroup_hierarchy=1` 启用 cgroup v2 兼容
- **依据**:
  - `configs/kernel-oc9.config:1-50` — `CONFIG_CGROUPS=y`、`CONFIG_CGROUP_PIDS=y`、`CONFIG_CGROUP_SCHED=y`、`CONFIG_CGROUP_DEVICE=y`、`CONFIG_CGROUP_CPUACCT=y`、`CONFIG_CGROUPFS=y`
  - `deploy/pvm/grub/host_grub_config.sh:18-21` — host grub 配置:`systemd.unified_cgroup_hierarchy=1`
  - `agent/rustjail/src/cgroups/` — `mod.rs`、`notifier.rs`、`systemd.rs`、`mock.rs`
  - `Cubelet/plugins/cube/internals/cgroup/cgroup.go` — `SetCubeboxCgroupLimit`
  - `Cubelet/pkg/container/cgroup/cgroup.go` — `WithMemoryLimit`(从 OCI spec 编译)
  - `Cubelet/services/cubebox/cube_container_create.go:107-118` — `cgroupp.SetCubeboxCgroupLimit(ctx, cgInfo.CgroupID, ...)` 显式设置 host cgroup 限制
- **配置/启用**: 沙箱创建时通过 OCI spec 提交 `resources.memory.limit` / `cpu` 等

### 2.4 Namespaces (PID / Net / Mount / UTS / IPC / User / Cgroup)

- **机制**: Guest 内部 containerd-shim 启动 OCI 容器时,Agent 显式 unshare IPC/UTS/PID namespace;**shim 层强制清除 CGROUP/NET/PID namespace**(避免与 host 命名空间冲突,因为这些由 VMM 接管)
- **关键代码**:
  ```rust
  // CubeShim/shim/src/container/mod.rs:285-302
  for ns in spec.get_linux().get_namespaces() {
      if ns.field_type == common::NS_CGROUP
          || ns.field_type == common::NS_NET
          || ns.field_type == common::NS_PID
      {
          continue;   // host-managed by VMM, not container
      }
      nss.push(n.clone());
  }
  spec.mut_linux().set_namespaces(nss.into());
  ```
- **依据**:
  - `agent/src/namespace.rs:7-50` — `pub const NSTYPEIPC: &str = "ipc"; pub const NSTYPEUTS: &str = "uts"; pub const NSTYPEPID: &str = "pid";` + `nix::sched::unshare(CloneFlags)`
  - `agent/rustjail/src/validator.rs:23-130` — `contain_namespace`、`usernamespace`、`cgroupnamespace`、`sysctl` 验证
  - `configs/kernel-oc9.config:1-50` — `CONFIG_NAMESPACES=y`、`CONFIG_USER_NS=y`
- **配置/启用**: 通过 OCI spec `linux.namespaces[]` 配置;shim 层固定过滤 cgroup/net/pid

### 2.5 host 启动参数硬化 (grub cmdline)

完整 `GRUB_CMDLINE_LINUX_APPEND`(`deploy/pvm/grub/host_grub_config.sh:18-22`):

```
module.sig_enforce=1 \
clearcpuid=27,28,54,57,104,107,118,120,122,131,152,158,193,196,198,... \
pti=off no5lvl mitigations=on spec_store_bypass_disable=prctl retbleed=off \
kvm.nx_huge_pages=never
```

- **作用说明**:
  - `module.sig_enforce=1` — 拒绝未签名内核模块
  - `clearcpuid=` — 清除大量 CPU 漏洞相关 feature 位(安全 + 性能平衡)
  - `pti=off mitigations=on` — 关闭页表隔离(性能),保留其他 CPU 漏洞缓解
  - `kvm.nx_huge_pages=never` — 关闭 KVM 大页 NX 缓解
- **配置/启用**: 通过 grub 配置应用,安装脚本自动合并到现有 cmdline

### 2.6 审计 (Audit)

- **机制**: guest kernel 启用 `CONFIG_AUDIT=y`、`CONFIG_AUDITSYSCALL=y`、`CONFIG_SECURITY_DMESG_RESTRICT=y`。但启动 cmdline 显式 `audit=0` 关闭 audit(性能折中)
- **依据**:
  - `configs/kernel-oc9.config` — `CONFIG_AUDIT=y`、`CONFIG_HAVE_ARCH_AUDITSYSCALL=y`、`CONFIG_AUDITSYSCALL=y`、`CONFIG_SECURITY_DMESG_RESTRICT=y`、`CONFIG_SECURITY=y`、`CONFIG_SECURITYFS=y`
  - `CubeShim/shim/src/hypervisor/config.rs:64` — `audit=0`(cmdline 关闭)
  - `hypervisor/docs/seccomp.md:40-50` — 注释 "the kernel running on the host machine must have the `audit` parameter enabled. If this is not the case, update kernel boot options by appending `audit=1`" — 即 seccomp log 模式需要 host audit=1
- **配置/启用**: 双向 — host 端通过 grub 开启;guest 端默认关闭以减少开销

### 2.7 未启用的内核机制 (重要!)

| 机制 | 状态 | 证据 |
|------|------|------|
| **AppArmor** | **未启用**(代码中无 apparmor policy 文件) | 全代码库无 apparmor policy |
| **SELinux** | **dev-env 中被显式关闭**(permissive)以兼容容器绑定挂载;生产部署靠 host SELinux 默认 enforcing 保护外部 mysql 容器 | `dev-env/internal/setup_selinux.sh:30-90` — `setenforce 0` + 改写 `/etc/selinux/config` 为 `permissive` |
| **Landlock** | **未使用**。整个代码库无 `landlock` 引用 | `grep -rE "Landlock|landlock"` 在 `CubeShim`、`agent`、`Cubelet`、`CubeAPI`、`CubeMaster` 全无匹配 |
| **Yama / ptrace** | 未在项目代码中显式配置;**依赖 host 内核 Yama 默认值**(通常 `kernel.yama.ptrace_scope=1`) | 代码无 yama / ptrace_scope 引用 |
| **unprivileged user namespace** | **支持启用**。agent 实现 rootless (euid/cgroup) 检测,要求 user namespace + uid/gid mapping;Cubelet 配置中支持 `EnableUnprivilegedPorts` / `EnableUnprivilegedICMP` | `agent/rustjail/src/validator.rs:200-260`、`agent/rustjail/src/specconv.rs:11-13` |

---

## 3. 运行时层 (Runtime)

### 3.1 rootless / 非特权运行

- **机制**: 支持 rootless 模板导出(`umoci --rootless`),并保留 rootfull 路径(`umoci` 非 `--rootless`);agent 内部 CreateOpts 含 `rootless_euid` / `rootless_cgroup` 字段
- **依据**:
  - `docs/changelog/v0.4.0.md` — "Daemonless export path (#492, #506): When skopeo and umoci are available on the CubeMaster node, template images are pulled via `skopeo copy` into a local OCI layout and unpacked with `umoci unpack --rootless`"
  - `CubeMaster/pkg/templatecenter/image/export.go` — 注释 "Only pass --rootless when we are NOT running as root. --rootless makes..."
  - `CubeMaster/pkg/templatecenter/image/disk.go` — 注释 "Mirrors the umoci (no --rootless) fix in export.go"
- **配置/启用**: 自动检测运行环境(以 root 启动则非 rootless)

### 3.2 jailer / 进程隔离

- **机制**: cloud-hypervisor **未集成 AWS Firecracker-style jailer 子进程**;改用 KVM 微 VM 隔离,shim 进程通过 **seccomp + capability + cgroup** 实现主机侧隔离
- **依据**: 全代码库无 `jailer` / `jail` 引用(已 grep)
- **隔离组合**:
  - hypervisor seccomp(2.1.1)
  - cube-hypervisor runtime seccomp(2.1.2)
  - 进程级 cgroup(2.3)
  - capability(2.2)
  - 独立 guest kernel(1.2)

### 3.3 快照/克隆安全

- **机制**: CubeShim 强制清理 `CUBE_SYS_PATH` 之外的 snapshot 路径(`/usr/local/services/cubetoolbox/`),避免覆盖系统目录;snapshot 类型分 Full / Diff;rate_limiter 在 snapshot 期间可用
- **依据**:
  - `CubeShim/shim/src/snapshot/mod.rs:36-65` — `pub const CUBE_SYS_PATH: &str = "/usr/local/services/cubetoolbox/"`,`FilePtr::Drop` 清理临时目录
  - `CubeShim/shim/src/snapshot/mod.rs:100-200` — 包含 `force: bool`、`app_snapshot: bool`、`snapshot_type: SnapshotType`、`memory_vol_url: Option<String>`
  - `CubeShim/shim/src/hypervisor/cube_hypervisor.rs:115-135` — `snapshot_vm(path, snapshot_type)` 通过 `ApiRequest::VmSnapshot` 调用 cloud-hypervisor
  - `CubeShim/shim/src/snapshot/mod.rs:230-260` — `set_runtime_seccomp_rules` + `Fs { backendfs_config: shared_dir = FS_SHARE_DIR, rate_limiter_config: None }`
  - `docs/blog/posts/2026-06-03-cubesandbox-v0.3.0-snapshot.md` — CubeCoW Copy-on-Write 引擎,提供 event-level snapshot / clone / rollback
- **配置/启用**: 通过 annotation `cube.snapshot.disable`、`cube.appsnapshot.create`、`cube.appsnapshot.restore`、`cube.vm.snapshot.base.path`、`cube.vm.snapshot.memory_vol_url`

### 3.4 网络隔离 (eBPF + TPROXY 双层)

#### 3.4.1 主机侧 eBPF (CubeVS) — L3/L4 强制隔离

- **机制**: **3 个 eBPF 程序**(`from_cube` / `from_world` / `from_envoy`)在 TC 钩子点实现 **ARP proxy、SNAT、policy check、session tracking**,全部在内核态执行
- **依据**:
  - `docs/architecture/network.md:30-100` — 三程序架构表
  - `CubeNet/cubevs/miscs.go:60-80` — `rlimit.RemoveMemlock()` + 常量重写
  - `docs/architecture/network.md:200-260` — 内置 always-deny CIDR: `10.0.0.0/8, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16`
  - 沙箱内部 IP 固定 `169.254.68.6`,网关 `169.254.68.5`
  - 主机端口空间分区:`10000-19999`(本机临时端口)、`20000-29999`(CubeProxy 入站)、`30000-65535`(SNAT 出站)
- **配置/启用**: 启动时加载 BPF 对象到 `/sys/fs/bpf/`,每个沙箱分配专属 TAP

#### 3.4.2 L7 透明代理 (CubeEgress)

- **机制**: **OpenResty + TPROXY** 实现 **HTTPS MITM**,逐请求匹配 L7 rules(`allow` / `deny` / `inject`)
- **路径**: TAP → cube-dev → mangle/PREROUTING → TPROXY → OpenResty(8080/8443) → 上游
- **依据**:
  - `CubeEgress/scripts/cube-proxy-iptables-init.sh:60-90` — `iptables -t mangle -A "${CHAIN}" -i cube-dev -p tcp --dport 80/443 -j TPROXY --on-ip 192.168.0.1`
  - `CubeEgress/nginx.conf` — OpenResty 配置,`ssl_certificate_by_lua` 动态签发 leaf cert
  - `docs/guide/security-proxy.md:30-50` — "CubeEgress runs as a host-network container and binds two TPROXY listeners"
  - `docs/guide/egress-network-policy.md:160-200` — 详细规则字段表(`match.scheme/sni/host/method/path`、`action.allow/audit/inject`)
- **配置/启用**: 沙箱创建时 `network.rules = [...]` L7 rule 列表

#### 3.4.3 防火墙级 sysctl

- **机制**: `rp_filter=0`、`route_localnet=1`、`accept_local=1` 支持 TPROXY 重定向到 lo
- **依据**: `CubeEgress/scripts/cube-proxy-iptables-init.sh:60-80` — `apply_sysctls` 函数,设置 `net.ipv4.conf.all.rp_filter=0`、`net.ipv4.conf.cube-dev.accept_local=1`

#### 3.4.4 TAP 设备隔离

- **机制**: 每个沙箱独立 TAP(`z` 前缀),固定 link-local IP `169.254.68.6/32` 网关 `169.254.68.5`,无广播域,无 L2 共享
- **依据**:
  - `Cubelet/network/plugin_tap.go:43-50` — `const tapNamePrefix = "z"; const cubeDev = "cube-dev"; const eth0 = "eth0";`
  - `docs/blog/posts/2026-06-23-cubesandbox-network-deep-dive.md:90-100` — "TAPs are naturally unreachable from each other — communication is only possible through paths controlled by eBPF programs"
- **配置/启用**: network-agent 维护 500+ TAP 池,沙箱启动时分配

### 3.5 文件系统隔离 (overlayfs / virtiofs)

- **机制**: 沙箱 rootfs 通过 DAX virtio-pmem (`pmem0`) 暴露;容器内 rootfs 通过 **virtiofs**(tag `cubeShared`)共享,支持 **overlay**(lower_dir 来源于多个 virtiofs lower)
- **依据**:
  - `CubeShim/shim/src/container/rootfs.rs:7-25` — `OverlayInfo { virtiofs_lower_dir: Vec<String> }`、`MountInfo { virtiofs_id, virtiofs_source, container_dest, r#type, options }`、`EroImage { path, lower_dir }`
  - `CubeShim/shim/src/container/rootfs.rs:34-60` — `fix_virtiofs()` 拼接 guest 内部路径 `/run/cube-containers/shared/containers`
  - `agent/src/mount.rs:30-50` — mount flag 表(含 `MS_NOSUID`、`MS_NODEV`、`MS_NOEXEC`)
  - `CubeShim/shim/src/hypervisor/config.rs:340-348` — `FsConfig` 的 `num_queues=1, queue_size=1024`
  - `CubeShim/shim/src/sandbox/config.rs:35-37` — `SHARE_CACHE_ALWAYS=1, SHARE_CACHE_NEVER=2` 控制 virtiofs cache 共享策略
- **配置/启用**: 容器 spec 的 `mounts` 字段 + cube.agent 的 `cube.rootfs.info` annotation

### 3.6 OCI 容器兼容 (containerd-shim v2)

- **机制**: CubeShim 实现 containerd-shim v2 API,以 `io.containerd.cube.v2` 注册到 containerd;支持 OCI spec、CRI 安全上下文(`privileged` / `readonly` / `NoNewPrivs` / `capabilities` / `seccomp` / `apparmor`);通过 **ttrpc/vsock** 与 guest 内 cube-agent 通信
- **依据**:
  - `CubeShim/shim/Cargo.toml:17-25` — 依赖 `containerd-shim=0.9.0`、`containerd-shim-protos=0.9.0`、`ttrpc=0.5.8`
  - `CubeShim/shim/README.md:90-100` — `runtime_type = "io.containerd.cube.v2"`
  - `Cubelet/services/cubebox/local.go` — `defaultShimPath = "/usr/local/services/cubetoolbox/cube-shim/bin/containerd-shim-cube-rs"`
  - `CubeMaster/conf.yaml:32-37` — 默认 OCI spec 含 `"security_context":{"privileged":true,"readonly_rootfs":false,"no_new_privs":false}`(在 OCI spec 透传给 agent, agent 内部最终实现 seccomp/cap drop)
  - `Cubelet/services/cubebox/cube_container_create_test.go` — 测试 `NoNewPrivs: true`

### 3.7 vsock 主机-客机通信

- **机制**: 主机 shim 与 guest cube-agent 通过 **vsock**(CID 主机侧、Guest CID=2)通信,不走 host 网络
- **依据**:
  - `CubeShim/shim/src/hypervisor/config.rs:355-360` — `add_vsock(id)` 调用 `Utils::gen_vsock_config(&id)`
  - `CubeShim/shim/README.md:1-20` — 架构图 "containerd → shim → cube-agent (ttrpc over vsock)"
- **配置/启用**: 自动

### 3.8 no_reaper / no_sub_reaper

- **机制**: shim 进程配置 `no_reaper: true, no_sub_reaper: true` — 避免沙箱内进程逃逸到 host 进程树
- **依据**: `CubeShim/shim/src/main.rs:17-22` — `let c = Config { no_reaper: true, no_setup_logger: true, no_sub_reaper: true, ..Default::default() };`
- **配置/启用**: 硬编码

---

## 4. API 层 (API)

### 4.1 外部 auth callback

- **机制**: 外部 `auth_callback_url` 模式,所有非 `/health` 请求须带 `Authorization: Bearer` 或 `X-API-Key`,转发原始 credential + `X-Request-Path` + `X-Request-Method` 到外部 callback;callback 返 200 即放行
- **依据**:
  - `CubeAPI/src/middleware/auth.rs:1-120` — 完整实现:`extract_credential()`(Bearer 优先)、`unified_auth()` 中间件(转发 `Authorization`/`X-API-Key`/`X-Request-Path`/`X-Request-Method`)
  - `CubeAPI/src/config/mod.rs:1-90` — `pub auth_callback_url: Option<String>,` 配置项
  - `CubeAPI/src/main.rs:1-90` — `--auth-callback-url` CLI flag + `AUTH_CALLBACK_URL` 环境变量
  - `docs/guide/authentication.md:1-100` — 完整使用文档,含 Python/FastAPI 示例
- **配置/启用**:
  - 启动: `./cube-api --auth-callback-url https://your-auth-service/verify` 或 `AUTH_CALLBACK_URL=...`
  - 默认未设置时**所有请求无认证放行**(需谨慎)
  - callback 验证 `X-Request-Path` + `X-Request-Method` 防止读权限提升到删/改

### 4.2 per-API-key 速率限制

- **机制**: token bucket per API key,默认 100 req/s,可通过 `RATE_LIMIT_PER_SEC` / `--rate-limit-per-sec` 调整
- **依据**:
  - `CubeAPI/src/state.rs:42-50` — `let quota = Quota::per_second(NonZeroU32::new(config.rate_limit_per_sec.max(1)).unwrap()); let rate_limiter = Arc::new(RateLimiter::keyed(quota));`
  - `CubeAPI/src/config/mod.rs:33-37` — `#[serde(default = "default_rate_limit")] pub rate_limit_per_sec: u32,` 默认 100
  - `CubeAPI/src/handlers/config.rs` — 通过 `/config` endpoint 暴露
- **配置/启用**: `RATE_LIMIT_PER_SEC` 环境变量 / `--rate-limit-per-sec` flag

### 4.3 共享 HTTP 客户端 (连接池上限)

- **机制**: `reqwest::Client` 配置 `pool_max_idle_per_host=100`
- **依据**: `CubeAPI/src/state.rs:50-55` — `http_client = reqwest::Client::builder().pool_max_idle_per_host(100)...`
- **配置/启用**: 硬编码

### 4.4 /health 豁免

- **机制**: `/health` 端点不被 auth callback 拦截
- **依据**: `docs/guide/authentication.md:8-12` — "every request (except /health) must carry..."
- **配置/启用**: 硬编码

### 4.5 多租户隔离

- **机制**: containerd `namespaces` 概念 + tenant label + Redis 命名空间
- **依据**:
  - `Cubelet/services/images/image_gc.go` — `nsCtx := namespaces.WithNamespace(ctx, ns)` 多处
  - `Cubelet/services/cubebox/events.go` — `ctx := namespaces.WithNamespace(context.Background(), e.Namespace)`
  - `Cubelet/services/cubebox/runc_container_op.go` — `ctx = namespaces.WithNamespace(ctx, sb.Namespace)`
  - `Cubelet/services/images/service.go:50` — `ns := namespaces.Default; ctx = namespaces.WithNamespace(ctx, ns);`

### 4.6 WebUI 数字助理 (DB-backed session)

- **机制**: WebUI 通过 username/password 登录(DB-backed),签发 opaque session token(`x-session-token` header),TTL 24h;`POST /auth/change-password` 修改密码
- **依据**:
  - `CubeAPI/src/handlers/auth.rs:1-100` — `login()`、`change_password()`、`session_token()`、`password_matches()`(使用 `crate::crypto::verify_password`)
  - `CubeAPI/src/handlers/auth.rs:18-20` — `const SESSION_HEADER: &str = "x-session-token"; const SESSION_TTL_SECS: i64 = 24 * 60 * 60;`
  - `CubeAPI/src/handlers/auth.rs:9-10` — 默认账户 `admin/admin` 首次迁移时播种(注释提到)
  - `CubeAPI/src/crypto.rs` — `verify_password` 实现

### 4.7 跨服务凭据

- **机制**: 部署时强制提醒用户重写默认 MySQL `cube_pass` / Redis `ceuhvu123`
- **依据**:
  - `deploy/one-click/install.sh:85-98` — `warn_default_external_credentials()` 在外部 MySQL/Redis 使用默认密码时输出 WARNING
  - `CubeMaster/conf.yaml` — 显式 MySQL `pwd: "cube_pass"` / Redis `password: "ceuhvu123"` 默认值,需在生产中修改
- **配置/启用**: `CUBE_EXTERNAL_MYSQL_PASSWORD` / `CUBE_EXTERNAL_REDIS_PASSWORD` 环境变量

### 4.8 端口与服务拓扑

- **机制**: CubeAPI 默认 3000 端口、CubeMaster 8089、MySQL 3306、Redis 6379
- **依据**:
  - `CubeAPI/src/config/mod.rs:30-32` — `pub bind: String, default: "0.0.0.0:3000"`
  - `CubeMaster/conf.yaml:2-5` — `http_port: 8089`

---

## 5. 部署与监控

### 5.1 部署前预检 (Preflight)

- **机制**: 在线安装脚本在下载前执行 root 检查、glibc ≥ 2.31 检查、`/dev/kvm` 存在检查、PVM 一致性检查、内存 ≥ 8GB 检查、`/data/cubelet` 在 XFS 文件系统上检查
- **依据**:
  - `deploy/one-click/online-install.sh:50-180` — `check_early_preflight()` 完整预检链
  - `deploy/one-click/scripts/cube-diag/check-deps.sh` — 详细依赖检查脚本

### 5.2 日志与诊断

- **机制**: cube-diag 收集 `/dev/kvm`、`/dev/pvm`、服务状态日志;日志输出到 `/data/log/<service>/`
- **依据**:
  - `deploy/one-click/scripts/cube-diag/collect-logs.sh` — 包含 `ls -la /dev/kvm /dev/pvm`
  - `CubeMaster/conf.yaml:9-14` — `log.path: "/data/log/CubeMaster-dev"`

### 5.3 CVE 修复链

- **机制**: 持续 bump 依赖修复 CVE,vmm-sys-util 0.12.1 (CVE-2023-50711) 等
- **依据**: `docs/changelog/v0.2.2.md` — "vmm-sys-util bumped to 0.12.1 (CVE-2023-50711, GHSA-875g-mfp6-g7f9)"

---

## 6. 总结:四层防御链

```
┌─────────────────────────────────────────────────┐
│ Host API        │ 4.1 callback / 4.2 rate-limit │  ← 接入层
├─────────────────────────────────────────────────┤
│ Host Runtime    │ 3.4 eBPF+TPROXY / 3.7 vsock   │  ← 网络 + 通道
│                 │ 3.8 no_reaper / 3.6 OCI shim   │
├─────────────────────────────────────────────────┤
│ Host Kernel     │ 2.1 seccomp×3 / 2.2 cap       │  ← 内核
│                 │ 2.3 cgroup / 2.4 namespace     │
│                 │ 2.5 cmdline 硬化 / 2.6 audit   │
├─────────────────────────────────────────────────┤
│ Virtualization  │ 1.1 KVM microVM / 1.2 独立kernel│  ← 硬件级
│                 │ 1.4 virtio / 1.5 IOMMU / 1.7 RL │
└─────────────────────────────────────────────────┘
```

### 核心安全理念

1. **用 KVM 硬件级隔离(独立 guest kernel)替代共享内核 namespace** → 容器逃逸面从 1 收敛到 0
2. **eBPF 内核态 policy 强制 + TPROXY 透明 L7 拦截** → 双层网络防护
3. **三层 seccomp**(host VMM / host shim / guest agent) → 纵深防御
4. **vsock 隔离 host-guest 通道** → 不走 host 网络
5. **virtio-pmem + virtiofs + overlay** → 灵活 rootfs 隔离

### 启用/未启用机制速查表

| 类别 | 启用机制 | 未使用/缺省机制 |
|------|---------|---------------|
| **虚拟化** | cloud-hypervisor (KVM microVM), 独立 guest kernel, vCPU/mem 隔离, virtio, VFIO/IOMMU, RateLimiter, read-only root | QEMU / Firecracker |
| **内核** | seccomp-bpf (hypervisor/shim/agent 三层), capability (5 sets), cgroup v1+v2, namespace (IPC/UTS/PID/User/Cgroup), 启动参数硬化 (module.sig_enforce, clearcpuid, pti=off) | AppArmor (未启用), Landlock (未使用), Yama (host default), SELinux (dev-env permissive) |
| **运行时** | rootless (umoci), virtio-pmem + virtiofs + overlay, eBPF 3 程序 (CubeVS), OpenResty TPROXY (CubeEgress), 独立 TAP, vsock 通信, no_reaper/no_sub_reaper | jailer (使用 KVM 替代) |
| **API** | 外部 callback auth, per-key 速率限制 (token bucket), 多租户 namespace, WebUI DB session, 默认凭据强制重写警告 | OAuth/JWT (未实现, 委托外部 callback) |

---

## 学习路线建议(对接 `Agent Sandbox学习计划.md`)

| Phase | 重点研读章节 |
|-------|-------------|
| **Phase 0** (钉死 host ↔ SDK 链路) | 3.4 vsock / 3.6 OCI shim / 3.7 ttrpc |
| **Phase 1** (读 hypervisor vmm + snapshot) | 1.1-1.7 全部 / 2.1.1 seccomp / 3.3 snapshot 安全 |
| **Phase 2'** (拆 SDK + 写 CubeSandboxClient) | 2.1.2 / 2.1.3 / 2.2 / 2.3 / 2.4 全部 |
| **Phase 3'** (OpenAI Agents SDK 集成) | 2.2 cap / 2.4 namespace / 3.6 OCI 安全上下文 |
| **Phase 4'** (三个 snapshot/clone demo) | 3.3 snapshot / 3.5 overlay / 1.5 IOMMU |
| **Phase 5'** (Writeup + 业界对标) | 整张表(对标 E2B/Modal/Blaxel 的"安全机制覆盖度") |
