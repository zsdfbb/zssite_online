---
title: "1.6 启动流程安全配置"
date: 2026-07-04T14:24:39+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/06-boot-security-config/"
summary: "Cloud-hypervisor 拉起一个 microVM 时,会把命令行参数(-append)注入到 guest kernel 的 cmdline。这些 cmdline 设置不是简单的版本号,而是一组对 guest kernel 行为的安"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 1.6 启动流程安全配置

## 机制原理

Cloud-hypervisor 拉起一个 microVM 时,会把命令行参数(`-append`)注入到 guest kernel 的 cmdline。这些 cmdline 设置不是简单的版本号,而是一组**对 guest kernel 行为的安全姿态选择**。

`VmConfig::default()` 在 `CubeShim/shim/src/hypervisor/config.rs:60-83` 强制套上以下默认:

```text
root=/dev/pmem0           # DAX virtio-pmem 作为 rootfs
rootflags=dax,errors=remount-ro ro   # 只读 + 严重 IO 错误降级为只读
audit=0                   # guest audit 关闭(性能折中)
mitigations=off           # spectre/meltdown 缓解关闭(guest 内独立)
panic=1                   # panic 1 秒后重启
agent.debug_console      # 启用 debug console
```

Rng 设备:

```rust
rng: RngConfig {
  src: PathBuf::from("/dev/urandom"),
  iommu: false            # 不必为熵源再加 IOMMU,性能换安全不合算
}
```

**可由用户覆盖的部分**:

- `cube.vm.kernel.path` —— 覆盖 vmlinux 路径(灰度验证新内核)
- `cube.vm.kernel.cmdline.append` —— 追加 cmdline(有冲突检查,不能重复 key)

## 为什么 CubeSandbox 这样配置

| 配置 | 选择 | 理由 |
|------|------|------|
| `ro` root | ✓ | guest 内被攻破后无法持久化写 rootfs |
| `audit=0` | ✓ | guest 内 sys_audit_call 路径关掉,减少 audit 内存与 CPU 开销;host 上仍可选 `audit=1` |
| `mitigations=off` | ✓ | guest 内独立 kernel,host 与外部已隔离;CPU 漏洞对 guest 内信息边界影响小,收益/成本不划算 |
| `panic=1` | ✓ | panic 后 1s 重启,防止内核卡死后无法 kubelet 监控感知 |
| `/dev/urandom` 熵源 | ✓ | 不必 host 内 virtio-rng 优化,但又保证可重复安全的随机 |
| `agent.debug_console` | ✓ | debug 时 kernel printk 直接到 console,故障定位不必串口 |

## 如何使用 / 配置

#### 覆盖 kernel 路径(灰度验证新内核)

```yaml
metadata:
  annotations:
    cube.vm.kernel.path: /tmp/vmlinux-5.15-debug.bin
```

#### 追加 cmdline(必须无冲突)

```yaml
metadata:
  annotations:
    cube.vm.kernel.cmdline.append: "quiet loglevel=3"
```

冲突检查逻辑(简化):

```rust
let base_keys: HashSet<&str> = vec!["root", "rootflags", "audit", "mitigations", "panic", ...].into_iter().collect();
let append_keys: HashSet<&str> = append.split_whitespace().map(|kv| kv.split('=').next()).collect();
if !append_keys.is_disjoint(&base_keys) {
    return Err("cannot override default cmdline key");
}
```

#### 启用 cube-rng

如果你想真的用上 host 硬件熵源:

```yaml
metadata:
  annotations:
    cube.rng.src: "/dev/hwrng"
    cube.rng.iommu: "true"
```

**警告**:

- 不要尝试覆盖 `root` / `rootflags` —— 这些是 CubeShim 与 guest-image 编译时耦合好的
- 不要覆盖 `audit=0` 而代之以 `audit=1`,会让 log 巨幅膨胀
- 调试时想看内核 panic 现场可以临时改成 `panic=0`
