---
title: "2.3 cgroups (资源限制)"
date: 2026-07-04T14:26:05+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/10-cgroups-resource-limiting/"
summary: "cgroups(control groups)是 Linux 内核的资源计数与限制机制。把同一个 hierarchy 下的进程归到一个 cgroup,该 cgroup 的所有进程共享一套配额。典型可限制的资源:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 2.3 cgroups (资源限制)

## 机制原理

**cgroups**(control groups)是 Linux 内核的资源计数与限制机制。把同一个 hierarchy 下的进程归到一个 cgroup,该 cgroup 的所有进程共享一套配额。典型可限制的资源:

| 子系统 | 限制的 | 字段 |
|---|---|---|
| `cpu` / `cpuacct` | CPU 时间 / accounting | `cpu.shares`、`cpu.cfs_quota_us` |
| `memory` | 内存 + swap | `memory.limit_in_bytes` |
| `pids` | PID 数量 | `pids.max` |
| `devices` | device node 访问 | `devices.allow` / `deny` |
| `blkio` | block IO 权重 | `blkio.weight` |
| `freezer` | freeze / thaw | `freezer.state` |
| `net_cls` / `net_prio` | 网络 class | `net_cls.classid` |

Cgroup 有 v1(各子系统独立 hierarchy)与 v2(统一 hierarchy)两代。CubeSandbox:

- guest kernel: 同时支持 v1 与 v2(`configs/kernel-oc9.config` 中 `CONFIG_CGROUPS=y`、`CONFIG_CGROUP_PIDS=y`、`CONFIG_CGROUP_SCHED=y`、`CONFIG_CGROUP_DEVICE=y`、`CONFIG_CGROUP_CPUACCT=y`)
- host grub 引导启用 v2 兼容:`systemd.unified_cgroup_hierarchy=1`

host 侧落地在 `agent/rustjail/src/cgroups/`(`mod.rs`、`notifier.rs`、`systemd.rs`、`mock.rs`)以及 `Cubelet/plugins/cube/internals/cgroup/cgroup.go` 的 `SetCubeboxCgroupLimit`。

## 为什么 CubeSandbox 使用它

- **CPU/内存强约束** —— 比 KVM 的 vCPU / 内存隔离更接近 OS 层
- **counter-based DoS** —— `pids.max` 直接限制进程数,防止 forkbomb
- **oom triggers 区分优先级** —— cgroup 内 OOM 与 host OOM 隔离,sandbox 内 OOM 不应"挤"死 kubelet
- **device deny 是 DAC 之外的二次防线** —— 恶意进程即使跑成 0:0,`devices.deny` 仍能阻止它写 /dev/kvm 等敏感节点
- **与 KVM 隔离正交** —— KVM 防"内核逃逸",cgroup 防"内核内资源滥用",两层一起才完整

## 如何使用 / 配置

#### OCI spec(用户提交)

```json
{
  "linux": {
    "resources": {
      "memory": { "limit": 268435456, "reservation": 134217728 },   // 256 MiB
      "cpu":    { "shares": 512, "quota": 50000, "period": 100000 } // 0.5 vCPU
    }
  }
}
```

#### agent 端转换(简化流程)

```rust
// agent/rustjail/src/cgroups/mod.rs
let path = format!("/sys/fs/cgroup/memory/cube/box-{}", box_id);
fs::write(format!("{}/memory.limit_in_bytes", path), 268_435_456u64.to_string())?;
fs::write(format!("{}/memory.swappiness", path), "0")?;

// CPU
fs::write(format!("{}/cpu.shares", path), 512u64.to_string())?;
fs::write(format!("{}/cpu.cfs_quota_us", path), 50000u64.to_string())?;
fs::write(format!("{}/cpu.cfs_period_us", path), 100000u64.to_string())?;

// pids
fs::write(format!("/sys/fs/cgroup/pids/cube/box-{}/pids.max", box_id), 1024u64.to_string())?;
```

#### Cubelet 侧显式调用(也供 host 上 host-cgroup 限制)

```go
// Cubelet/services/cubebox/cube_container_create.go:107-118
cgroupp.SetCubeboxCgroupLimit(ctx, cgInfo.CgroupID,
    /* memory */ 268435456,
    /* cpuShares */ 512,
    /* pidsMax */ 1024,
)
```

**注意**:

- **不要让 cgroup 路径穿透** —— 在 `systemd.unified_cgroup_hierarchy=0` 的 host 上,agent 走 `/sys/fs/cgroup/cpu,cpuacct/<id>` 而不是各自分离,改 cgroup 路径规划会引发限制失效
- **`memory.swap` 必须设置为 0 或有限制**,否则 malware 可靠 swap 隐蔽占内存
- **`pids.max` 别设太大**(>1k 在 forkbomb 时已经足够打爆 host)
- `devices.allow` 只放过确实需要的设备,如 `c 1:3 /dev/null rwm`,其它一律保持 deny
