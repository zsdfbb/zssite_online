---
title: "2.5 host 启动参数硬化 (grub cmdline)"
date: 2026-07-04T14:26:45+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/12-kernel-boot-hardening/"
summary: "Host kernel 引导参数(grub cmdline)是内核启动时的关键开关。CubeSandbox 在 deploy/pvm/grub/hostgrubconfig.sh:18-22 中合并下列项:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 2.5 host 启动参数硬化 (grub cmdline)

## 机制原理

**Host kernel 引导参数**(grub cmdline)是内核启动时的关键开关。CubeSandbox 在 `deploy/pvm/grub/host_grub_config.sh:18-22` 中合并下列项:

```text
module.sig_enforce=1 \
clearcpuid=27,28,54,57,104,107,118,120,122,131,152,158,193,196,198,... \
pti=off no5lvl mitigations=on spec_store_bypass_disable=prctl retbleed=off \
kvm.nx_huge_pages=never
```

逐项解释:

| 参数 | 作用 | 风险/收益 |
|---|---|---|
| `module.sig_enforce=1` | 拒绝任何未签名内核模块 | 防 rootkit 直接加载 `.ko`,但禁了 dkms 自定义模块 |
| `clearcpuid=27,28,...,198,...` | 关闭指定 CPU feature 位(包括 spectre/variant 类的预测执行 hint) | 见下详解 |
| `pti=off` | **关闭** Kernel Page Table Isolation | 性能提升 5-15%,但仍暴露 spectre v2 |
| `no5lvl` | 不启用 5-level page table | 小内存机器 (host < 4TB RAM) 反正也用不上 |
| `mitigations=on` | 启用除上面 pt=off 之外的其它 CPU 漏洞缓解 | 关闭 pt 的情况下仍保留 retpoline、SSBD 等 |
| `spec_store_bypass_disable=prctl` | spectre v4 缓解默认关闭,需要进程显式 prctl 开启 | 性能优先,但 sandbox 内程序可用 prctl 自我打开 |
| `retbleed=off` | 关闭 retbleed 缓解 | retbleed 利用率极低,关掉换性能 |
| `kvm.nx_huge_pages=never` | KVM 大页禁用 NX-execute-on-data bit | 防 MCE/EDAC 误报;性能微损 |

**关于 `clearcpuid` 一长串数字**:腾讯自己做过实验,把已知 CVE-2017-5754 / CVE-2018-3639 等所涉及 CPU feature 位(speculative execution / IBRS / STIBP / RDCL_NO / IBPB...)选出来关掉。这是"付出 ~1% 性能,挡住一批低危 CPU 漏洞"的平衡选法。

## 为什么 CubeSandbox 这样配

- **不是无脑全开**(那会损失 30%+ 性能)
- **不是无脑全关**(生产 host 上别人也会用 VM / VMX)
- **平衡策略**:guest 内有独立 kernel 缓解该有都有,host 上把低利用率漏洞的 feature 关掉,直接削减 KVM 转换开销
- **节省 host CPU 周期**——`pti=off` 在大型云上对 throughput 影响极大

## 如何使用 / 配置

#### 自动应用(推荐)

直接跑 `deploy/pvm/grub/host_grub_config.sh`:

```bash
cd deploy/pvm/grub
./host_grub_config.sh
# 会解析 grub 配置 + 加上硬化的 cmdline + grub2-mkconfig
```

#### 手动改(调试时)

```bash
# 1. 备份
cp /etc/default/grub /etc/default/grub.bak

# 2. 在 GRUB_CMDLINE_LINUX 末尾追加
GRUB_CMDLINE_LINUX="... existing stuff ... module.sig_enforce=1 clearcpuid=... pti=off mitigations=on spec_store_bypass_disable=prctl retbleed=off kvm.nx_huge_pages=never"

# 3. 重新生成 grub 菜单
grub2-mkconfig -o /boot/grub2/grub.cfg

# 4. 重启
reboot
```

#### 验证生效

```bash
cat /proc/cmdline | tr ' ' '\n' | grep -E 'pti|mitigations|sig_enforce|clearcpuid'

# CPU feature 检查
cat /proc/cpuinfo | grep -E 'stibp|ibrs|ssbd|rdcl_no'
# 这些 flag 不应出现,即为关闭
```

**警告**:

- 修改 grub 重启失败 → 用串口物理控制台调试——所以**永远保留一个能用的旧 grub entry**(`GRUB_TIMEOUT=10` 起步)
- `module.sig_enforce=1` 会让 host 上所有未签名模块(典型如 nvidia 自家的闭源 GPU 驱动)直接拒绝加载。需要 GPU passthrough 时,要么自己签名,要么临时重启回退
- `clearcpuid=` 的数字列表会随新 CPU 模型而扩大,**升级 kernel 时检查一次这个列表**
- `kvm.nx_huge_pages=never` 不能与某些 Intel CPU 型号(Xeon v2-era)+ large page 大页混用,提前验证
