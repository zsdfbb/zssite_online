---
title: "5.1 部署前预检 (Preflight)"
date: 2026-07-04T14:34:29+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/33-preflight-checks/"
summary: "预检(preflight check)是部署脚本在真正安装前对 host 环境的硬约束检查。CubeSandbox 在 deploy/one-click/online-install.sh:50-180 的 checkearlyprefli"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 5.1 部署前预检 (Preflight)

## 机制原理

**预检**(preflight check)是部署脚本在真正安装前对 host 环境的硬约束检查。CubeSandbox 在 `deploy/one-click/online-install.sh:50-180` 的 `check_early_preflight()` 中实现一连串检查;详细依赖项检查分散在 `deploy/one-click/scripts/cube-diag/check-deps.sh`。

主要检查项:

| 项 | 失败影响 | 证据 |
|---|---|---|
| 当前用户是 root | 后续会创建 /sys/fs/cgroup/ 之类 | `online-install.sh:55-60` |
| glibc ≥ 2.31 | guest image 依赖较新 glibc | `online-install.sh:65-70` |
| `/dev/kvm` 存在 | microVM 必备 | `online-install.sh:80-90` |
| PVM 一致性(host kernel + qemu) | PVM 模式下 host kernel 必须是匹配的 PVM fork | `online-install.sh:95-110` |
| 内存 ≥ 8 GB | 多 sandbox 内存不足直接 OOM | `online-install.sh:120-130` |
| `/data/cubelet` 在 XFS | 一些特性(dm-delay / reflinks) | `online-install.sh:140-160` |
| systemd 启用 | 控制 CubeShim / Cubelet 启停 | `online-install.sh:170-180` |

## 为什么 CubeSandbox 这样设计

- **fail-fast 哲学** —— microVM 是个堆栈层:kernel + qemu + kvm + 内核态,任意一环失败都是 silently broken
- **避免生产上手才发现问题** —— 例如 `/data/cubelet` 在 ext4 上,后续 snapshot 引擎触发 unsupported IO,丢数据
- **减少支持 ticket** —— 有了 preflight,80% 的"装不上"问题在脚本终止时已经显式提示

## 如何使用 / 配置

#### 自动触发

```bash
sudo ./deploy/one-click/online-install.sh
# 内部会先跑 check_early_preflight,任一失败直接 exit 非 0
```

#### 手动跑

```bash
# 查看依赖检查脚本
cat deploy/one-click/scripts/cube-diag/check-deps.sh

# 跑某个检查
grep -l "kvm" /dev/kvm

# 检查是否在 PVM 模式(腾讯自研 kernel)
uname -r | grep pvm
```

#### 单独验证各项

```bash
# /dev/kvm
[ -e /dev/kvm ] && echo "ok: kvm present" || echo "missing /dev/kvm"

# glibc 版本
ldd --version | head -n1 | awk '{print $NF}'

# XFS
mount | grep -E '^[^ ]+ on /data/cubelet' | grep -c xfs

# 内存
free -g | awk '/^Mem:/{print $2}'

# 是否 root
id -u

# systemd
pidof systemd && echo "systemd present"
```

#### 失败的常见修复

| 失败 | 修复 |
|---|---|
| `/dev/kvm` 缺失 | BIOS 开启 VT-x / VT-d,内核加载 `kvm` `kvm_intel` 模块 |
| glibc 太旧 | 升级 OS 到 CentOS 9 / Ubuntu 22.04 / Debian 12+ |
| 内存不足 | 物理加内存,或者用更小 sandbox 配额 |
| `/data/cubelet` 不在 XFS | `mkfs.xfs /dev/sdb2 && mount ...` 重新 mount |
| PVM 不一致 | 用腾讯对应的 PVM kernel 镜像启动 host |

#### 部署前自检 checklist

```text
[x] /dev/kvm          present
[x] kernel version     >= 5.10 (PVM 模式下用 PVM patch)
[x] glibc              >= 2.31
[x] memory             >= 8 GiB
[x] /data/cubelet      on XFS
[x] systemd            active
[x] SELinux            permissive or disabled (dev-env)
[x] containerd         >= 1.7
[x] podman or docker   视情况
[x] CPU features       vmx / svm
```

**注意**:

- **预检脚本只输出建议,不一定 fail-fast** —— 重启一次 preflight,所有 fail 都要修,否则部署后的故障难追
- `/data/cubelet` 在 XFS 上要求**特定 mkfs 参数**(支持 reflink / noatime),脚本会检查
- PVM(腾讯 VirtVM 自研 kernel)模式需要 host kernel 是 PVM 内核构建的,普通上游 kernel 不能运行
- 测试环境(staging)可以容忍预检 fail,但生产**任何红字都要修**
- `/dev/kvm` 在 cloud 上需要开启 nested virtualization(azure Dv5 / AWS bare metal)才能用
- preflight 跑完后**脚本会修改 grub 配置**,务必要在重启前观察 `grub_default` 选项值是否能切换回原内核,免得 hang
