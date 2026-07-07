---
title: "1.1 cloud-hypervisor (KVM microVM) 作主 VMM"
date: 2026-07-04T14:21:19+08:00
draft: false
tags: ["cubesandbox", "security", "T3"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/01-cloud-hypervisor-kvm/"
summary: "cloud-hypervisor 是 RustVMM 基金会下的一个 KVM hypervisor,针对 microVM 场景(单租户、启动快、内存极小、安全)做了专门优化。腾讯在 CubeSandbox 中将其内部命名为 cube-hyp"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 1.1 cloud-hypervisor (KVM microVM) 作主 VMM

## 机制原理

**cloud-hypervisor** 是 RustVMM 基金会下的一个 KVM hypervisor,针对 microVM 场景(单租户、启动快、内存极小、安全)做了专门优化。腾讯在 CubeSandbox 中将其内部命名为 **cube-hypervisor**,源码 vendored 在仓库 `hypervisor/` 子目录下,而不是像 Firecracker / QEMU 那样作为外部依赖。

它的核心原理是:

1. **直接通过 `/dev/kvm` ioctl 操作 KVM**——不经过 QEMU 这一层,系统调用路径短,攻击面小
2. **极简设备模型**——只支持 virtio-net / virtio-block / virtio-fs / virtio-console / vsock 等"必要"virtio 设备,不模拟完整 PC 设备(无 i440fx、PS/2、IDE)
3. **进程单线程模型为主、专用 virtio 线程**——一个 virtio 设备一个 worker thread,便于按线程施加不同的 seccomp 规则
4. **Rust 实现 + seccomp 保护**——`hypervisor/src/lib.rs` 顶部就 `use seccompiler::SeccompAction;`,启动后立即套上 seccomp(`SeccompAction::KillProcess`),默认动作即拒绝所有未声明 syscall

## 为什么 CubeSandbox 使用它

- **不引入 QEMU**——QEMU 的全设备模型 + 复杂的 user-mode 转换是过去多个 guest escape CVE 的来源,Firecracker 之所以安全部分原因就是砍掉了这些
- **Rust 内存安全**——hypervisor 主程序在 Rust 里,大量 buffer 处理无 use-after-free / double-free 风险(虽然 syscall fd 仍需谨慎)
- **与 KVM 直接对话**——硬件级隔离,即使 VMM 进程被攻破,攻击者也只触达虚拟 vCPU,无法直接读写 host 物理内存
- **对齐"microVM 是新型容器"理念**——启动微秒级,适合 sandbox 按需创建-销毁节奏

## 如何使用 / 配置

| 项 | 位置 | 说明 |
|---|---|------|
| 部署前 | `/dev/kvm` 必须存在 | `deploy/one-click/install.sh` 与 `online-install.sh` 会主动检查,缺失直接 fail-fast 退出 |
| seccomp 行为 | `hypervisor/src/vmm_config.rs` | `seccomp: SeccompAction::KillProcess` 默认 |
| seccomp 调节 | `hypervisor/src/main.rs` | `--seccomp true|false|log`(默认 `true`,`log` 模式需要 host `audit=1`) |
| 各线程规则 | `hypervisor/vmm/src/seccomp_filters.rs` | 按 `Api`/`SignalHandler`/`Vcpu`/`Vmm`/`PtyForeground` 五类聚合 |

启动一个 sandbox(由 CubeMaster 编排 → CubeShim 拉起 hypervisor)的命令大致是:

```bash
./cube-hypervisor \
  --api-socket /tmp/cube-vm-0.sock \
  --kernel /usr/local/services/cubetoolbox/cube-image/vmlinux.bin \
  --disk /usr/local/services/cubetoolbox/cube-image/cube-guest-image-cpu.img \
  --cpus boot=2 \
  --memory size=512M \
  --seccomp true \
  --log-file /tmp/cube-vm-0.log
```

**要点提示**:

- 不要绕过 `/dev/kvm` 直接 /usr/bin/qemu-system-x86_64;CubeShim 假定 hypervisor 都是自己 vendored 的 cube-hypervisor
- 升级 hypervisor 时必须同步升级 `seccomp_filters.rs` 中的 syscall 白名单——新版本 libc 加了 syscall 而白名单未更新,会直接破坏 sandbox 启动
- 不要在生产中用 `--seccomp false`,即便只为了 debug 也要三思
