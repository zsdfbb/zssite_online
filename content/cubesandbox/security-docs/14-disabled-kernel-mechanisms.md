---
title: "2.7 未启用的内核机制(重要!)"
date: 2026-07-04T14:27:44+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/14-disabled-kernel-mechanisms/"
summary: "不是所有\"听起来该用\"的安全机制 CubeSandbox 都启用了。这篇列了 4 个经过调研确认未使用 / 默认 permissive 的内核机制,以及 CubeSandbox 选/不选它的原因。"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 2.7 未启用的内核机制(重要!)

## 概述

不是所有"听起来该用"的安全机制 CubeSandbox 都启用了。这篇列了 4 个**经过调研确认未使用 / 默认 permissive** 的内核机制,以及 CubeSandbox 选/不选它的原因。

## 1. AppArmor

- **状态**: **未启用**
- **证据**: 全代码库无 apparmor policy 文件;CubeShim / agent 都没有 apparmor 路径
- **为什么不启用**:
  - cube 内有自己合规的 seccomp + capability 两层(2.1 / 2.2)
  - 引入 AppArmor 会让 pod 启动流程多一道 LSM hook,影响兼容性
  - LSM 只能选一个 major(CubeSandbox 倾向不绑 LSM,留 hook 给未来 SELinux)
- **何时考虑启用**:
  - 业务对 SELinux/AppArmor 二选一,且必须满足某种合规标准(等保 / FedRAMP)

## 2. SELinux

- **状态**: **dev-env 中显式 permissive**,生产依赖 host SELinux 默认 enforcing
- **证据**: `dev-env/internal/setup_selinux.sh:30-90`
  ```bash
  setenforce 0
  sed -i 's/SELINUX=enforcing/SELINUX=permissive/' /etc/selinux/config
  ```
- **为什么不默认 enforcing**:
  - dev-env 启动要 binding mount 外部 mysql / redis 容器,SELinux 在 docker-in-docker 套娃时会**拦掉很多常用 mount 路径**,与 dao 工具链不兼容
  - 仓库与外部 mysql 容器通信需要 setenforce 0 + 改 /etc/selinux/config
- **生产 host 怎么配**:
  ```bash
  setenforce 1
  sed -i 's/SELINUX=permissive/SELINUX=enforcing/' /etc/selinux/config
  ```
- **何时切换**:
  - 上生产前务必切 enforcing,但要提前跑 audit2allow 收集 warning,补全规则后再切

## 3. Landlock

- **状态**: **未使用**
- **证据**: `grep -rE 'landlock|Landlock'` 在 `CubeShim / agent / Cubelet / CubeAPI / CubeMaster` 全部无匹配
- **为什么不启用**:
  - Landlock 是比 seccomp 更晚引入(5.13+),目前对 fs 操作限制的能力覆盖不完整
  - 因为已经有 seccomp + capability + cgroup 三层,Landlock 的"应用层 sandboxing"价值被覆盖
  - 一旦 Landlock 升级到能在生产用,值得讨论是否加进 agent 的 fallback path
- **何时考虑启用**:
  - 业务需要"在保持 root 的同时只允许读某些目录"——这是 Landlock 擅长而 seccomp 难做的场景

## 4. Yama / ptrace_scope

- **状态**: **未在项目代码中显式配置**,依赖 host 内核 Yama 默认值(通常 `kernel.yama.ptrace_scope=1`)
- **证据**: grep 无 yama / ptrace_scope 引用
- **为什么不显式开**:
  - Yama 不像 SELinux 那样"platform 默认开启就生效";Linux 内核仅在 `CONFIG_SECURITY_YAMA=y` 时才有
  - 大多数发行版自带 `ptrace_scope=1`(只能 attach 到自己进程)
  - CubeSandbox 的"注入追踪"主要靠 vsock + ttrpc,不依赖 ptrace
- **何时手动设值**:
  - 如果你需要在开发机上 strace sandbox 内进程(调试 agent 行为),把 `/proc/sys/kernel/yama/ptrace_scope` 临时设 `0`
  - 生产 host 上保持 `1` 即可,无需调
  - 设置方式:
    ```bash
    echo 0 | sudo tee /proc/sys/kernel/yama/ptrace_scope   # 调试时
    echo 1 | sudo tee /proc/sys/kernel/yama/ptrace_scope   # 生产
    ```

## 5. unprivileged user namespace

- **状态**: **支持启用**(实现就绪,但默认不开)
- **证据**: `agent/rustjail/src/validator.rs:200-260`、`agent/rustjail/src/specconv.rs:11-13`
- **使用方式**: agent 实现 rootless(euid/cgroup)检测,要求 user namespace + uid/gid mapping;Cubelet 配置支持 `EnableUnprivilegedPorts` / `EnableUnprivilegedICMP`
- **何时启用**:
  - 在不希望给 sandbox 完全 root 的场景下,启用 unprivileged ports / ICMP 的 rootless 模式
  - 启用需要 host 内核 `CONFIG_USER_NS=y`(已经默认启用)

## 学习建议

在 Phase 2' (拆 SDK + 写 CubeSandboxClient) 不要因为"听说 AppArmor / Landlock 更好"就强行加进自己的 client。读源码确认 CubeSandbox 没碰后,**保持现状**就好——除非你的目标就是在生产环境替换或补全这些 LSM,否则没必要增加 compatibility surface。

对每一条 LSM 决策的**反向问题**也很重要:为什么这里不用它?答对后能更好理解 CubeSandbox 把"安全模型"寄托在(VM 硬件隔离 + seccomp + capability + cgroup + namespace)的核心约束上。
