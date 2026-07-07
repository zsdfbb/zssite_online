---
title: "1.2 独立 Guest Kernel(消除共享内核风险)"
date: 2026-07-04T14:21:30+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/02-isolated-guest-kernel/"
summary: "\"独立 Guest Kernel\" 指每个 sandbox 启动一台 microVM,VM 内运行的是专属的内核镜像(cube-guest-image-cpu.img),而不是像传统 Linux 容器那样 host kernel 通过 na"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 1.2 独立 Guest Kernel(消除共享内核风险)

## 机制原理

"独立 Guest Kernel" 指每个 sandbox 启动一台 microVM,VM 内运行的是**专属的内核镜像**(cube-guest-image-cpu.img),而不是像传统 Linux 容器那样 host kernel 通过 namespace/cgroup 模拟隔离。

对比:

| 隔离模型 | 是否共享内核 | 攻击面 |
|---|---|---|
| 传统 Docker 容器 | 共享 host kernel | 内核 namespace 漏洞 / capability 逃逸 / cgroup escape / io_uring 漏洞...一处失守,host 全军覆没 |
| microVM(CubeSandbox) | **每个 VM 一个独立 guest kernel** | guest 内核漏洞只影响自己;无法触碰 host 内核 |

关键的工程落地:

- Guest rootfs 挂在 `/dev/pmem0`(DAX virtio-pmem),这是 NVDIMM 的模拟设备
- 引导参数硬编码 `root=/dev/pmem0 rootflags=dax,errors=remount-ro ro`——`ro` 标志保证根分区只读,即使 guest 内 kernel 攻破,无法写入 rootfs
- Guest 内核和 host 内核是两个不同的内核——可以独立修补 CVE

## 为什么 CubeSandbox 使用它

- **Docker/共享内核模型的根痛点**——Kubernetes 历史上多次 CVE(io_uring CVE-2023-2598 / runc CVE-2019-5736 / CVE-2024-21626 等)都是"容器内逃逸到 host kernel"路径。共享内核意味着逃逸面是 1(必定存在逃逸路径)。microVM 让逃逸面在架构上变成 0——攻破 guest kernel 只能回到 guest 内部
- **多租户 LLM 代码沙箱的合规要求**——许多客户代码是 untrusted 的,放共享内核上跑几乎不会被安全团队接受
- **CubeShim 的相对简洁**——因为隔离由 KVM 提供,shim 内部不必做完整的容器化防御,只需正确配置 VMM

## 如何使用 / 配置

- 入口:`CubeShim/shim/src/hypervisor/config.rs:35-55`
  ```rust
  pub const IMAGE_PATH: &str = "/usr/local/services/cubetoolbox/cube-image/cube-guest-image-cpu.img";
  ```
- guest kernel cmdline 在 `VmConfig::default()` 强制加上:
  ```text
  root=/dev/pmem0 rootflags=dax,errors=remount-ro ro audit=0 mitigations=off panic=1 agent.debug_console
  ```
- 升级 guest kernel 时:
  1. 替换 `/usr/local/services/cubetoolbox/cube-image/` 下的 vmlinux + image
  2. 通过 annotation `cube.vm.kernel.path` 可临时覆盖路径,用于灰度验证
  3. 通过 `cube.vm.kernel.cmdline.append` 追加额外 cmdline(有冲突检查)

**注意**:

- 不要把 `ro` 标志去掉——许多 sandbox 漏洞缓解最终依赖"guest 内文件无法持久写"
- `mitigations=off` 是**性能折中**:guest 已经独立,host 上那么多 spectre/meltdown 缓解在 guest 内不必开
- guest kernel 编译在 `configs/kernel-oc9.config`——研究机制层时优先看这里
