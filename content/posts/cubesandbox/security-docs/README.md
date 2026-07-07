---
title: "CubeSandbox 安全机制详解"
date: 2026-07-04T14:21:03+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/"
summary: "基于《CubeSandbox 安全机制清单.md》(2026/07/03 调研)"
cover:
  image: ""
  alt: ""
  caption: ""
---
# CubeSandbox 安全机制详解

> 基于《CubeSandbox 安全机制清单.md》(2026/07/03 调研)
> 每篇一篇,介绍一个安全机制的:原理 / 为什么 CubeSandbox 使用 / 如何使用
> 按四层防御链 + 部署层组织

---

## 阅读路径

### 1. 虚拟化层 (Virtualization)

| # | 机制 | 文档 |
|---|------|------|
| 1.1 | cloud-hypervisor (KVM microVM) 作主 VMM | [01-cloud-hypervisor-kvm.md]({{< relref "./01-cloud-hypervisor-kvm.md" >}}) |
| 1.2 | 独立 Guest Kernel(消除共享内核风险) | [02-isolated-guest-kernel.md]({{< relref "./02-isolated-guest-kernel.md" >}}) |
| 1.3 | vCPU / 内存隔离 | [03-vcpu-memory-isolation.md]({{< relref "./03-vcpu-memory-isolation.md" >}}) |
| 1.4 | virtio 设备隔离 | [04-virtio-device-isolation.md]({{< relref "./04-virtio-device-isolation.md" >}}) |
| 1.5 | 设备透传 (IOMMU / VFIO) | [05-iommu-vfio-passthrough.md]({{< relref "./05-iommu-vfio-passthrough.md" >}}) |
| 1.6 | 启动流程安全配置 | [06-boot-security-config.md]({{< relref "./06-boot-security-config.md" >}}) |
| 1.7 | virtio Rate Limiter (TokenBucket) | [07-virtio-rate-limiter.md]({{< relref "./07-virtio-rate-limiter.md" >}}) |

### 2. 内核层 (Kernel)

| # | 机制 | 文档 |
|---|------|------|
| 2.1 | seccomp / seccomp-bpf(三层部署) | [08-seccomp-three-layers.md]({{< relref "./08-seccomp-three-layers.md" >}}) |
| 2.2 | Linux Capabilities(细粒度) | [09-linux-capabilities.md]({{< relref "./09-linux-capabilities.md" >}}) |
| 2.3 | cgroups(资源限制) | [10-cgroups-resource-limiting.md]({{< relref "./10-cgroups-resource-limiting.md" >}}) |
| 2.4 | Namespaces(PID/Net/Mount/UTS/IPC/User/Cgroup) | [11-namespaces.md]({{< relref "./11-namespaces.md" >}}) |
| 2.5 | host 启动参数硬化 (grub cmdline) | [12-kernel-boot-hardening.md]({{< relref "./12-kernel-boot-hardening.md" >}}) |
| 2.6 | 审计 (Audit) | [13-audit.md]({{< relref "./13-audit.md" >}}) |
| 2.7 | 未启用的内核机制(AppArmor/SELinux/Landlock/Yama) | [14-disabled-kernel-mechanisms.md]({{< relref "./14-disabled-kernel-mechanisms.md" >}}) |

### 3. 运行时层 (Runtime)

| # | 机制 | 文档 |
|---|------|------|
| 3.1 | rootless / 非特权运行 | [15-rootless-unprivileged.md]({{< relref "./15-rootless-unprivileged.md" >}}) |
| 3.2 | jailer / 进程隔离 | [16-jailer-process-isolation.md]({{< relref "./16-jailer-process-isolation.md" >}}) |
| 3.3 | 快照 / 克隆安全 | [17-snapshot-clone-safety.md]({{< relref "./17-snapshot-clone-safety.md" >}}) |
| 3.4.1 | 网络隔离 - 主机侧 eBPF (CubeVS) | [18-network-ebpf-cubevs.md]({{< relref "./18-network-ebpf-cubevs.md" >}}) |
| 3.4.2 | 网络隔离 - L7 透明代理 (CubeEgress) | [19-network-cubegress-tproxy.md]({{< relref "./19-network-cubegress-tproxy.md" >}}) |
| 3.4.3 | 网络隔离 - TAP 设备隔离 + sysctl | [20-network-tap-sysctl.md]({{< relref "./20-network-tap-sysctl.md" >}}) |
| 3.5 | 文件系统隔离 (overlayfs / virtiofs) | [21-filesystem-overlayfs-virtiofs.md]({{< relref "./21-filesystem-overlayfs-virtiofs.md" >}}) |
| 3.6 | OCI 容器兼容 (containerd-shim v2) | [22-oci-shim-v2.md]({{< relref "./22-oci-shim-v2.md" >}}) |
| 3.7 | vsock 主机-客机通信 | [23-vsock-host-guest.md]({{< relref "./23-vsock-host-guest.md" >}}) |
| 3.8 | no_reaper / no_sub_reaper | [24-no-reaper.md]({{< relref "./24-no-reaper.md" >}}) |

### 4. API 层 (API)

| # | 机制 | 文档 |
|---|------|------|
| 4.1 | 外部 auth callback | [25-auth-callback.md]({{< relref "./25-auth-callback.md" >}}) |
| 4.2 | per-API-key 速率限制 | [26-api-rate-limit.md]({{< relref "./26-api-rate-limit.md" >}}) |
| 4.3 | 共享 HTTP 客户端(连接池上限) | [27-shared-http-client.md]({{< relref "./27-shared-http-client.md" >}}) |
| 4.4 | /health 豁免 | [28-health-endpoint-exempt.md]({{< relref "./28-health-endpoint-exempt.md" >}}) |
| 4.5 | 多租户隔离 | [29-multi-tenant-isolation.md]({{< relref "./29-multi-tenant-isolation.md" >}}) |
| 4.6 | WebUI 数字助理(DB-backed session) | [30-webui-session.md]({{< relref "./30-webui-session.md" >}}) |
| 4.7 | 跨服务凭据 | [31-cross-service-credentials.md]({{< relref "./31-cross-service-credentials.md" >}}) |
| 4.8 | 端口与服务拓扑 | [32-port-service-topology.md]({{< relref "./32-port-service-topology.md" >}}) |

### 5. 部署与监控

| # | 机制 | 文档 |
|---|------|------|
| 5.1 | 部署前预检 (Preflight) | [33-preflight-checks.md]({{< relref "./33-preflight-checks.md" >}}) |
| 5.2 | 日志与诊断 | [34-logs-diagnostics.md]({{< relref "./34-logs-diagnostics.md" >}}) |
| 5.3 | CVE 修复链 | [35-cve-remediation.md]({{< relref "./35-cve-remediation.md" >}}) |

---

## 四层防御链回顾

```
┌─────────────────────────────────────────────────┐
│ Host API        │ 25–32(认证/限流/凭据/会话)    │  ← 接入层
├─────────────────────────────────────────────────┤
│ Host Runtime    │ 18–24(网络 + 文件 + 通道)     │  ← 网络 + 通道
├─────────────────────────────────────────────────┤
│ Host Kernel     │ 8–14(seccomp/cap/cgroup/NS)   │  ← 内核
├─────────────────────────────────────────────────┤
│ Virtualization  │ 1–7(KVM/virtio/IOMMU/RL)      │  ← 硬件级
└─────────────────────────────────────────────────┘
```

## 安全理念(自上而下)

1. **硬件级隔离为底**(KVM microVM + 独立 guest kernel)——把容器逃逸面从 1 收敛到 0
2. **内核机制强化**(seccomp 三层 + capability 五 set + cgroup v1+v2)——纵深防御
3. **运行时守护**(eBPF + TPROXY + vsock + TAP)——网络/通道级隔离
4. **API 边界**(callback auth + rate-limit + 多租户)——攻击面收敛

---

## 系列文档索引

## 概述

本目录按 [CubeSandbox 安全机制清单]({{< relref "../CubeSandbox安全机制清单.md" >}}) §1-§5 的子节展开,共 35 篇机制专题 + 1 篇本 README,提供:

- 每个机制的**完整文件位置证据** (`path/to/file:line`)
- 配置/启用方式 (默认 / CLI flag / env / annotation)
- 与 SVG 安全边界模型的对接关系

适合回答"机制 X 是怎么实现的?在哪行代码?如何配置?"。

## 章节分布 (与清单.md 对齐)

| 章节 | 篇数 | 范围 |
|------|------|------|
| **1. 虚拟化层** | 7 篇 (1.1-1.7) | cloud-hypervisor、独立 guest kernel、vCPU/内存、virtio、IOMMU、RateLimiter |
| **2. 内核层** | 7 篇 (2.1-2.7) | seccomp 三层、Linux Capabilities、cgroup、namespace、grub 硬化、audit、未启用机制 |
| **3. 运行时层** | 10 篇 (3.1-3.8 含子节) | rootless、jailer、快照、网络隔离 (eBPF + TPROXY + sysctl + TAP)、文件系统、OCI shim、vsock、no_reaper |
| **4. API 层** | 8 篇 (4.1-4.8) | auth callback、rate limit、HTTP 客户端、健康检查、多租户、WebUI session、凭据、端口 |
| **5. 部署与监控** | 3 篇 (5.1-5.3) | 预检、日志诊断、CVE 修复 |

## 35 篇专题清单

### 1. 虚拟化层 (1.1-1.7)

| # | 标题 | 文档 |
|---|------|------|
| 1.1 | cloud-hypervisor (KVM microVM) 作主 VMM | [01-cloud-hypervisor-kvm.md]({{< relref "01-cloud-hypervisor-kvm.md" >}}) |
| 1.2 | 独立 Guest Kernel (消除共享内核风险) | [02-isolated-guest-kernel.md]({{< relref "02-isolated-guest-kernel.md" >}}) |
| 1.3 | vCPU / 内存隔离 | [03-vcpu-memory-isolation.md]({{< relref "03-vcpu-memory-isolation.md" >}}) |
| 1.4 | virtio 设备隔离 | [04-virtio-device-isolation.md]({{< relref "04-virtio-device-isolation.md" >}}) |
| 1.5 | 设备透传 (IOMMU / VFIO) | [05-iommu-vfio-passthrough.md]({{< relref "05-iommu-vfio-passthrough.md" >}}) |
| 1.6 | 启动流程安全配置 | [06-boot-security-config.md]({{< relref "06-boot-security-config.md" >}}) |
| 1.7 | virtio Rate Limiter (TokenBucket) | [07-virtio-rate-limiter.md]({{< relref "07-virtio-rate-limiter.md" >}}) |

### 2. 内核层 (2.1-2.7)

| # | 标题 | 文档 |
|---|------|------|
| 2.1 | seccomp / seccomp-bpf (三层部署) | [08-seccomp-three-layers.md]({{< relref "08-seccomp-three-layers.md" >}}) |
| 2.2 | Linux Capabilities (细粒度) | [09-linux-capabilities.md]({{< relref "09-linux-capabilities.md" >}}) |
| 2.3 | cgroups (资源限制) | [10-cgroups-resource-limiting.md]({{< relref "10-cgroups-resource-limiting.md" >}}) |
| 2.4 | Namespaces (PID/Net/Mount/UTS/IPC/User/Cgroup) | [11-namespaces.md]({{< relref "11-namespaces.md" >}}) |
| 2.5 | host 启动参数硬化 (grub cmdline) | [12-kernel-boot-hardening.md]({{< relref "12-kernel-boot-hardening.md" >}}) |
| 2.6 | 审计 (Audit) | [13-audit.md]({{< relref "13-audit.md" >}}) |
| 2.7 | 未启用的内核机制 (重要!) | [14-disabled-kernel-mechanisms.md]({{< relref "14-disabled-kernel-mechanisms.md" >}}) |

### 3. 运行时层 (3.1-3.8)

| # | 标题 | 文档 |
|---|------|------|
| 3.1 | rootless / 非特权运行 | [15-rootless-unprivileged.md]({{< relref "15-rootless-unprivileged.md" >}}) |
| 3.2 | jailer / 进程隔离 | [16-jailer-process-isolation.md]({{< relref "16-jailer-process-isolation.md" >}}) |
| 3.3 | 快照 / 克隆安全 | [17-snapshot-clone-safety.md]({{< relref "17-snapshot-clone-safety.md" >}}) |
| 3.4.1 | 网络隔离 - 主机侧 eBPF (CubeVS) | [18-network-ebpf-cubevs.md]({{< relref "18-network-ebpf-cubevs.md" >}}) |
| 3.4.2 | 网络隔离 - L7 透明代理 (CubeEgress) | [19-network-cubegress-tproxy.md]({{< relref "19-network-cubegress-tproxy.md" >}}) |
| 3.4.3 | 网络隔离 - TAP 设备隔离 + sysctl | [20-network-tap-sysctl.md]({{< relref "20-network-tap-sysctl.md" >}}) |
| 3.5 | 文件系统隔离 (overlayfs / virtiofs) | [21-filesystem-overlayfs-virtiofs.md]({{< relref "21-filesystem-overlayfs-virtiofs.md" >}}) |
| 3.6 | OCI 容器兼容 (containerd-shim v2) | [22-oci-shim-v2.md]({{< relref "22-oci-shim-v2.md" >}}) |
| 3.7 | vsock 主机-客机通信 | [23-vsock-host-guest.md]({{< relref "23-vsock-host-guest.md" >}}) |
| 3.8 | no_reaper / no_sub_reaper | [24-no-reaper.md]({{< relref "24-no-reaper.md" >}}) |

### 4. API 层 (4.1-4.8)

| # | 标题 | 文档 |
|---|------|------|
| 4.1 | 外部 auth callback | [25-auth-callback.md]({{< relref "25-auth-callback.md" >}}) |
| 4.2 | per-API-key 速率限制 | [26-api-rate-limit.md]({{< relref "26-api-rate-limit.md" >}}) |
| 4.3 | 共享 HTTP 客户端 (连接池上限) | [27-shared-http-client.md]({{< relref "27-shared-http-client.md" >}}) |
| 4.4 | /health 豁免 | [28-health-endpoint-exempt.md]({{< relref "28-health-endpoint-exempt.md" >}}) |
| 4.5 | 多租户隔离 | [29-multi-tenant-isolation.md]({{< relref "29-multi-tenant-isolation.md" >}}) |
| 4.6 | WebUI 数字助理 (DB-backed session) | [30-webui-session.md]({{< relref "30-webui-session.md" >}}) |
| 4.7 | 跨服务凭据 | [31-cross-service-credentials.md]({{< relref "31-cross-service-credentials.md" >}}) |
| 4.8 | 端口与服务拓扑 | [32-port-service-topology.md]({{< relref "32-port-service-topology.md" >}}) |

### 5. 部署与监控 (5.1-5.3)

| # | 标题 | 文档 |
|---|------|------|
| 5.1 | 部署前预检 (Preflight) | [33-preflight-checks.md]({{< relref "33-preflight-checks.md" >}}) |
| 5.2 | 日志与诊断 | [34-logs-diagnostics.md]({{< relref "34-logs-diagnostics.md" >}}) |
| 5.3 | CVE 修复链 | [35-cve-remediation.md]({{< relref "35-cve-remediation.md" >}}) |

## 阅读路径建议

- **顺序阅读**: 1.1 → 1.2 → ... → 5.3 (按主题深入)
- **跳读**: 想了解网络隔离 → 3.4.1 + 3.4.2 + 3.4.3 (eBPF + TPROXY + sysctl 三连击)
- **跳读**: 想了解认证 → 4.1 + 4.6 (外部 callback + WebUI session)
- **跳读**: 想了解纵深防御 → 2.1 (seccomp 三层) + 3.8 (no_reaper) + 1.5 (IOMMU)
