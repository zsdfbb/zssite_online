---
title: "1.3 vCPU / 内存隔离"
date: 2026-07-04T14:21:41+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/03-vcpu-memory-isolation/"
summary: "每个 sandbox 实例的 vCPU 数、内存大小、CPU 拓扑结构都通过 annotation cube.vmmres 显式声明,由 CubeShim 注入到 cloud-hypervisor 的 VM 配置里。三个核心要素:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 1.3 vCPU / 内存隔离

## 机制原理

每个 sandbox 实例的 vCPU 数、内存大小、CPU 拓扑结构都通过 annotation `cube.vmmres` 显式声明,由 CubeShim 注入到 cloud-hypervisor 的 VM 配置里。三个核心要素:

1. **vCPU 数量隔离**:`vcpus.max_vcpus = self.vcpus as u8`,最多 255 个(单字节),按声明值固定
2. **CPU 拓扑隔离**:`CpuTopology { threads_per_core: 1, cores_per_die: self.vcpus as u8, ... }`——只有一线程一核,SMT/HyperThreading 关闭,关闭跨核侧信道风险
3. **内存隔离**:`vc.memory.size = self.memory_size * MI_B`(粒度 MB),按声明分配并独立 hva 映射

**不支持热添加**(由 CubeShim 限制)——vCPU/内存一旦声明不可中途变更,防止恶意 guest 通过 hotplug 创建超出 quota 的资源。

## 为什么 CubeSandbox 使用它

- **DoS 抑制**——如果 vCPU/内存不显式限制,恶意 tenant 可写 busy-loop 独占 vCPU,或 mmaps 大量内存让 OOM killer 误杀 host 上其它重要进程
- **公平调度**——配额让 SAAS 平台"按需付费、按量分配"
- **侧信道缓解**——关闭 SMT(单线程单核)直接消除 L1/L2 cache cross-thread 探测
- **配合 seccomp/cap 形成资源 + 行为双轴约束**

## 如何使用 / 配置

调用方在创建 sandbox 时提交 annotation:

```yaml
metadata:
  annotations:
    cube.vmmres: '{"cpu": 2, "memory": 536870912}'   # 2 vCPU, 512 MiB
```

字段定义在 `CubeShim/shim/src/sandbox/config.rs:230-240`:

```rust
pub struct VmResource {
    pub cpu: u32,
    pub memory: u64,            // bytes
    pub preserve_memory: u64,
    pub snap_memory: u64,
}
```

CubeShim 把它转译成 `VmConfig`:

```rust
self.vcpus.max_vcpus = self.vcpus as u8;
vc.memory.size = self.memory_size * MI_B;
CpuTopology { threads_per_core: 1, cores_per_die: self.vcpus as u8, ... }
```

**最佳实践**:

- LLM 代码 sandbox 的推荐起步:`cpu=2, memory=512MiB`
- 跑很重推理(加载大模型)的子场景:`cpu=4, memory=4GiB`;但要警惕 OS image 自身 page cache 占用
- 不要把 `memory` 给到 64GiB+——会碎片化 host 大页

**典型坑**:

- `preserve_memory` / `snap_memory` 这两个字段和 snapshot 引擎相关,先保持 0,等熟悉 snapshot API 后再填
- vCPU 数量是必填,且必须大于 0
