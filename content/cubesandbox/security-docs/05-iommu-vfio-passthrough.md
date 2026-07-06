---
title: "1.5 设备透传 (IOMMU / VFIO)"
date: 2026-07-04T14:24:19+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/05-iommu-vfio-passthrough/"
summary: "设备透传 (device passthrough) 把宿主机上的物理 PCI 设备(nic / disk)直接挂给某个 VM,VM 内能看到这块设备的真实 PCI BAR 与寄存器,而无需 hypervisor 的全设备模拟。"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 1.5 设备透传 (IOMMU / VFIO)

## 机制原理

**设备透传** (device passthrough) 把宿主机上的物理 PCI 设备(nic / disk)直接挂给某个 VM,VM 内能看到这块设备的真实 PCI BAR 与寄存器,而无需 hypervisor 的全设备模拟。

CubeSandbox 的实现要点:

1. **VFIO 框架**——Linux 内核的 VFIO 子系统把 DMA 操作通过 IOMMU 重定向,host 物理内存的某个区域只能被指定设备读写
2. **IOMMU 单独可开关**——`iommu: bool` 在 VMM 设备配置中默认 `false`,需要时显式开
3. **两类透传设备**——net / disk 各自通过 annotation `cube.vfio.net` / `cube.vfio.disk` 启用
4. **资源隔离**——透传设备一旦被某 sandbox 占用,host 内其它进程无法触碰对应 IOMMU domain

硬件要求:host CPU 必须支持 IOMMU (Intel VT-d / AMD-Vi),且 kernel 配置里有 `CONFIG_VFIO_IOMMU_TYPE1=y`。在 BIOS 中启用 VT-d 是先决条件。

## 为什么 CubeSandbox 使用它

- **极致 IO 性能**——直接绕过 virtio 路径,几乎等同于物理设备。对 GPU passthrough、RDMA 卡、NVMe 加速这类场景是关键
- **横向安全隔离**——VM 即便攻破 guest kernel,也无法通过 IOMMU domain 之外的 DMA 访问 host 物理内存
- **避免 device-tree/ACPI 模拟复杂度**——virtio 的 guest 侧模拟器虽然纯软件,但有完整 virtio backend 代码,攻击面比 VFIO 大
- **企业特性**——GPU passthrough 是"agent 跑 ML 推理"的常见诉求,需要直通 NIC/NVMe

## 如何使用 / 配置

#### annotation 形式

```yaml
metadata:
  annotations:
    cube.vfio.net: |
      [
        {"device_pci": "0000:01:00.0", "iommu": true}
      ]
    cube.vfio.disk: |
      [
        {"device_pci": "0000:02:00.0", "path_in_guest": "/dev/vda"}
      ]
```

结构定义在 `CubeShim/shim/src/sandbox/device.rs:5-7`:

```rust
pub const ANNO_VFIO_DISK: &str = "cube.vfio.disk";
pub const ANNO_VFIO_NET: &str = "cube.vfio.net";
```

#### 在 host 上准备 VFIO

```bash
# 解绑原始驱动
echo 0000:01:00.0 > /sys/bus/pci/drivers/<vendor-driver>/unbind

# 绑定到 vfio-pci
echo 0000:01:00.0 > /sys/bus/pci/drivers/vfio-pci/bind

# 验证
lspci -v -s 0000:01:00.0   # 应能看到 "Kernel driver in use: vfio-pci"
```

#### 数据结构

```rust
// CubeShim/shim/src/sandbox/config.rs:50-55
pub vfio_nets: Vec<Device>,
pub vfio_disks: Vec<DeviceDisk>,
pub vfio_disk_path_map: HashMap<String, u32>,
```

**注意**:

- 启用 VFIO 必须 host kernel 引导参数开 `intel_iommu=on` / `amd_iommu=on`
- 一旦一个 PCI 设备分配给 sandbox,**该设备 host 内就消失了**,不要在生产 host 上跑重要服务时启用
- 默认 `iommu: false`——不是所有 sandbox 都该开,只在需要时按 device 打开
- 如果你做 GPU passthrough,务必配合 SR-IOV + cgroup 限制 vfio-pci 进程的总内存
