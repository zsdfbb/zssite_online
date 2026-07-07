---
title: "2.6 审计 (Audit)"
date: 2026-07-04T14:27:06+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/13-audit/"
summary: "Linux Audit(CONFIGAUDIT=y)是一个内核子系统,记录系统调用、文件访问、安全决策等关键事件到 audit log(/var/log/audit/audit.log 或 netlink 转发)。CubeSandbox 内"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 2.6 审计 (Audit)

## 机制原理

**Linux Audit**(`CONFIG_AUDIT=y`)是一个内核子系统,记录系统调用、文件访问、安全决策等关键事件到 audit log(`/var/log/audit/audit.log` 或 netlink 转发)。CubeSandbox 内:

- **guest kernel 编译** 启用 audit:`configs/kernel-oc9.config` 中:
  ```text
  CONFIG_AUDIT=y
  CONFIG_HAVE_ARCH_AUDITSYSCALL=y
  CONFIG_AUDITSYSCALL=y
  CONFIG_SECURITY_DMESG_RESTRICT=y
  CONFIG_SECURITY=y
  CONFIG_SECURITYFS=y
  ```
- **guest kernel cmdline 显式 `audit=0`** —— `CubeShim/shim/src/hypervisor/config.rs:64` 默认关掉 audit(高开销,日志庞大)
- **host kernel 需要 `audit=1`** 才支持 seccomp log 模式:见 `hypervisor/docs/seccomp.md:40-50` 注释明确

```text
// 关键注释
// the kernel running on the host machine must have the `audit` parameter enabled.
// If this is not the case, update kernel boot options by appending `audit=1`
```

`CONFIG_SECURITY_DMESG_RESTRICT=y` 让非 root 用户看不到内核 ring buffer,减少内核信息泄漏。

## 为什么 CubeSandbox 这么配置

- **guest 内 audit 默认关闭** —— 性能折中。guest 内即使开启 audit,日志也写到 guest 内文件系统,sandbox 销毁即消失,价值有限
- **host 内 audit 默认开启** —— seccomp log 模式必须依赖它。要"看见"违规 syscall,必经 auditd
- **seccomp + audit 是组合拳** —— 没有 audit 时 `--seccomp log` 等于无效
- **`SECURITY_DMESG_RESTRICT`** —— sandbox 内非特权用户拿不到 dmesg,削弱 side-channel 信息

## 如何使用 / 配置

#### 确保 host 上 auditd 启动

```bash
systemctl enable auditd
systemctl start auditd

# 查 audit 配置
auditctl -l
# 应该看到 -b 8192 这种 backlog 设置
```

#### 启用 seccomp log 模式

```bash
# 1. host grub 加 audit=1
vi /etc/default/grub
# GRUB_CMDLINE_LINUX="... audit=1 ..."
grub2-mkconfig -o /boot/grub2/grub.cfg

# 2. 重启后,启动 hypervisor
./cube-hypervisor --seccomp log

# 3. 跑一晚上流量,然后看
ausearch -m SECCOMP -ts today
# 应该看到 SECCOMP: auid=N uid=N ... exe=... syscall=... compat=... ip=...
```

#### guest 内 audit(可选,调试 sandbox 安全时启用)

```yaml
metadata:
  annotations:
    cube.vm.kernel.cmdline.append: "audit=1"   # 覆盖默认 audit=0
```

guest 内 audit log 一般写到 virtio-fs 共享目录的 `/var/log/audit/audit.log` 上:

```bash
# sandbox 内
auditctl -a exit,always -F exe=/usr/bin/suspicious-binary
ausearch -ts today
```

#### 事件分析

```bash
# 列出今日所有 syscall 违规
ausearch -m SECCOMP -ts today --format text

# 解析为可读报告
aureport --summary
aureport --auid --summary
aureport --syscall
```

**注意**:

- **host 的 audit 不能仅靠 `--seccomp log` 满足** —— 没有 auditd 服务接收的话,netlink 包会丢
- guest 内 audit=1 后,如果 OCI 容器还配置了 seccomp `SECCOMP_FILTER_FLAG_LOG`,日志会**双写**(guest 内 audit + guest kernel ring buffer)
- 调试时务必用 `auditctl -D` 临时清空规则,避免 noise 干扰
- audit buffer(`-b` 参数)默认 8192,超高负载时建议提到 16384 或更高
