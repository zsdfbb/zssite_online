---
title: "3.2 jailer / 进程隔离"
date: 2026-07-04T14:28:24+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/16-jailer-process-isolation/"
summary: "Jailer 模式起源于 AWS Firecracker:把 VMM 进程(chroot 进空目录 + 切到非 root 用户 + 给新 cgroup + 加 seccomp filter + drop all caps)。这样,即使 VM"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 3.2 jailer / 进程隔离

## 机制原理

**Jailer** 模式起源于 AWS Firecracker:把 VMM 进程(chroot 进空目录 + 切到非 root 用户 + 给新 cgroup + 加 seccomp filter + drop all caps)。这样,即使 VMM 被攻破,攻击者被困在 jail 内,无法触碰 host 系统资源。

CubeSandbox 的关键差异:**它没有 Firecracker-style jailer 子进程**。`grep -rE 'jailer|jail'` 在 `CubeShim` 全代码无匹配。

替代品是**KVM 微 VM + 多层 host 防护**的组合:

| Firecracker 用 jailer | CubeSandbox 等价物 |
|---|---|
| chroot 进空目录 | KVM microVM 已有独立 guest kernel,不必再 chroot |
| 切换到非 root 用户 | shim 进程本身以受限 uid 跑(可选) |
| cgroup 限制 | cgroup 限制 (2.3) |
| seccomp filter | seccomp filter (2.1.1, 2.1.2) |
| drop caps | capability drop (2.2) |
| 独立 guest kernel | 独立 guest kernel (1.2) |

也就是说 CubeSandbox **不需要** jailer,因为 sandbox 已经身处独立 microVM 中了。在 microVM 之外再做一层 host jail,边际安全收益很低,但 debug 复杂度暴涨。

## 为什么 CubeSandbox 不集成 jailer

- **隔离已经在更细的 KVM 层完成** —— guest kernel 是独立的,microVM 是 Firecracker-style 的
- **Jailer 的副作用**:Firecracker jailer 必须设置 `--id` 与 `--exec-file`,运维稍重;CNCF/agent 框架不必为运维便利牺牲安全性
- **seccomp 已经覆盖** —— 三层 seccomp (2.1) 已把"host 进程能调哪些 syscall"列出严格白名单,jailer 的"切 uid/chroot"作用被组合替代
- **可观测性更好** —— 没有 jailer 的层级关系,debug 路径扁平

## 如何使用 / 配置

#### 现状:无显式配置

CubeSandbox 不暴露 `jailer` flag。如果你确实需要模仿 Firecracker 的 jail 行为,可以**自定义**如下几项:

```bash
# 1) 把 shim 进程用 systemd 切到非 root 的专用用户
cat > /etc/systemd/system/cube-shim.service <<EOF
[Service]
ExecStart=/usr/local/services/cubetoolbox/cube-shim/bin/containerd-shim-cube-rs
User=cube-shim
Group=cube-shim
NoNewPrivileges=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictNamespaces=true
RestrictRealtime=true
RestrictSUIDSGID=true
SystemCallArchitectures=native
SystemCallFilter=@system-service
SystemCallErrorNumber=EPERM
EOF
```

#### 自检:当前 host 上 cube-shim 进程是否降权

```bash
ps -o pid,user,group,namespaces -p $(pidof containerd-shim-cube-rs)

# 期望:
#     PID USER       GROUP      NSpid
#   12345 cube-shim  cube-shim  12345   (如果有 user namespace)
```

#### podman / docker 替身场景

CubeSandbox Podman 都可借鉴这个思路,但目前 shim 实际上仍是 root(因为它要管 KVM / cgroup)。如果一定要降权,可以:

```bash
sudo -u cube-shim ./cube-shim --config /etc/cube-shim/conf.yaml
# 配合 systemd 启 + SupplementaryGroups 喂 /dev/kvm 权限
```

**注意**:

- 不要轻易尝试把 shim 切到非 root,因为它要访问 `/dev/kvm`、`/dev/net/tun`、cgroup hierarchy、containerd socket,**几乎都需要 root**
- 如果业务仅想要"层层防御"的话,在 hypervisor 层加 `Jailer-like` 包装反而让 SDLC 失稳
- 当企业合规要求"全部进程非 root"时,**与 KVM 用 sudo 配的 `CAP_VIRT_X` 特殊能力**(实测内核有 patch)是一种有效路径
- 调试 host 行为时关闭 seccomp / cgroup 是常见做法,**生产环境务必保持开启**
