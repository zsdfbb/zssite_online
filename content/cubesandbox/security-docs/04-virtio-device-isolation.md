---
title: "1.4 virtio 设备隔离"
date: 2026-07-04T14:21:54+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/04-virtio-device-isolation/"
summary: "virtio 是虚拟机内最常见的\"半虚拟化\"IO 设备协议,CubeSandbox 中所有 guest ↔ host 数据通路(网络、磁盘、文件系统)都走 virtio。每个 virtio 设备的隔离由 3 个层面共同保证:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 1.4 virtio 设备隔离

## 机制原理

virtio 是虚拟机内最常见的"半虚拟化"IO 设备协议,CubeSandbox 中所有 guest ↔ host 数据通路(网络、磁盘、文件系统)都走 virtio。每个 virtio 设备的隔离由 3 个层面共同保证:

1. **独立 queue**:每个设备配置 `num_queues=1, queue_size=1024`(默认)——一条独立队列就一个独立 ring buffer,避免跨设备的 mem copy 路径混淆
2. **独立 id 和 tag**:设备有 `id`(内部编号,如 `cube-fs`)和共享挂载的 `tag`(guest 看到的 label,如 `cubeShared`)。Guest 内只能 mount 这个 tag 看到的共享,无法触碰其它 sandbox 的 virtio 后端
3. **virtio backend in host process**:每条 virtio 设备在 shim 进程内有独立的 worker 处理 guest kick / DMA(由 cloud-hypervisor 调度),不会和别的设备共用线程上下文

代码上的命名约定:

```rust
pub const VIRTIO_FS_TAG: &str = "cubeShared";
pub const VIRTIO_FS_ID: &str = "cube-fs";
```

```rust
FsConfig {
  id: VIRTIO_FS_ID,
  tag: VIRTIO_FS_TAG,
  num_queues: 1,
  queue_size: 1024,
  ...
}
```

net 设备 id 是命名空间化的:`format!("{}-{}", utils::NET_DEVICE_ID_PRE, nets.len())`,保证多设备也不冲突。

## 为什么 CubeSandbox 使用它

- **把 IO 路径"半虚拟化"**——避免完整设备模拟(IDE/NVMe/USB)的庞大攻击面
- **每个设备独立 queue**——设备 A 的一次 ring flood 不会污染设备 B 的队列
- **Tag 名约定**(`cubeShared`)是 guest-side rootfs 的入口,也是 agent 启动 OCI 容器时识别 virtiofs 后端唯一寻址方式
- **和 KVM 一起构成微 VM 的最小可视 IO 面**

## 如何使用 / 配置

- **透传 virtio-fs** 由 annotation `cube.fs` / `cube.virtiofs` 控制:
  ```yaml
  metadata:
    annotations:
      cube.virtiofs: |
        {
          "tag": "cubeShared",
          "source": "/var/lib/cubelet/sandboxes/box-42/rootfs",
          "cache": "always"
        }
  ```
- `cache` 字段对应 `CubeShim/shim/src/sandbox/config.rs:35-37` 的 `SHARE_CACHE_ALWAYS=1, SHARE_CACHE_NEVER=2`
- **net** 设备的 `id` 是程序生成的,user 不可改,但能用 annotation `cube.net.tx_rate` / `cube.net.rx_rate` 控速(被 TokenBucket 处理)

**注意**:

- 不要尝试在 guest 内 mount 一个未申报 tag 的 virtiofs——会直接 ENOENT
- guest 内 modprobe virtio-pci 但**不** modprobe virtio_mmio——具体看 `vmlinux` 内编译了哪些模块
- 如果你暴露的是临时数据,**用 `cache: never`**,数据完整性 / 安全同步优于性能
