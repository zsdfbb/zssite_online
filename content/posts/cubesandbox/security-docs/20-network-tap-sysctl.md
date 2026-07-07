---
title: "3.4.3 网络隔离 - TAP 设备隔离 + sysctl"
date: 2026-07-04T14:29:52+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/20-network-tap-sysctl/"
summary: "这层包含两个相辅相成的组件:TAP 设备的命名/地址惯例 与 支持 TPROXY 的 sysctl。"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 3.4.3 网络隔离 - TAP 设备隔离 + sysctl

## 机制原理

这层包含两个相辅相成的组件:**TAP 设备的命名/地址惯例** 与 **支持 TPROXY 的 sysctl**。

#### TAP 设备隔离

每个 sandbox 分配一个 host 上的 **TAP 网络接口**(linux TUN/TAP 设备),TAP 命名约定:

```go
// Cubelet/network/plugin_tap.go:43-50
const tapNamePrefix = "z";
const cubeDev = "cube-dev";
const eth0 = "eth0";
```

| 项 | 约定 |
|---|---|
| TAP 名称前缀 | `z` (例如 `z1234`) |
| Internal bridge | `cube-dev` |
| Guest 内网络接口 | `eth0` |
| Internal IP | `169.254.68.6/32`(link-local) |
| Gateway | `169.254.68.5` |

无广播域,无 L2 共享——TAP 之间**自然不可达**,任何通信必须经 eBPF TC 钩子(详见 18 号文章)。

#### 防火墙级 sysctl

`CubeEgress/scripts/cube-proxy-iptables-init.sh:60-80` 显式:

```bash
net.ipv4.conf.all.rp_filter=0       # 关闭逆向路径过滤
net.ipv4.conf.cube-dev.accept_local=1   # lo 上接受本地 netns 来源
net.ipv4.conf.all.route_localnet=1   # 允许 127.0.0.0/8 路由
```

这些 sysctl 是 **TPROXY 工作前提**——TPROXY 把包重定向到 lo 上的 OpenResty,**正常策略下 reverse path filter 会丢这种"假"包**。

## 为什么 CubeSandbox 这样设计

- **TAP 命名 `z` 前缀**:避开 systemd predictable interface 与 docker bridge 的命名冲突,也不必每次重生成 udev 规则
- **`/32` + 单 gateway**:sandbox 之间没有 L2 互通可能,broadcast 攻击零面
- **always-deny CIDR** + **TPROXY MITM** 双层组合:**L3/L4 由 eBPF 拦,L7 由 OpenResty 拦**
- **定制 sysctl** 让 MITM 真正能 happen,而不丢包

## 如何使用 / 配置

#### host 启动时 apply sysctl

```bash
cat > /etc/sysctl.d/99-cube-net.conf <<EOF
net.ipv4.conf.all.rp_filter=0
net.ipv4.conf.default.rp_filter=0
net.ipv4.conf.cube-dev.accept_local=1
net.ipv4.conf.all.route_localnet=1
net.ipv4.ip_forward=1
net.core.rmem_max=16777216
net.core.wmem_max=16777216
EOF

sysctl --system
```

#### 看 TAP

```bash
ip -br link show | grep ^z
# z1234: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500

# 具体某一个 TAP 的统计
ip -s link show z1234
#   RX: bytes  packets  errors  dropped overrun mcast
#  12345678  12345    0       0       0       0
```

#### 看 TAP pool

```bash
# CubeMaster 会维护 500+ 预创建的 TAP 池
ls /sys/class/net | grep ^z | wc -l
# 期望 ≥ sandbox 数量 × 1.5
```

#### 观察 cube-dev bridge

```bash
brctl show cube-dev
bridge name    bridge id            STP enabled    interfaces
cube-dev       8000.525400abcdef    no             z1234
                                                  z1235
                                                  z1236
                                                  ...
```

#### 看某 sandbox 的活跃流

```bash
# 通过 bpftool 看 from_world / from_cube map
bpftool map show | grep -i cube
bpftool map dump id <id>
# 输出:
# key: 169.254.68.6:54321 -> value: src_ip=203.0.113.42 dst_ip=169.254.68.5
```

**注意**:

- **TAP 池耗尽** 时,新一轮 sandbox 启动会卡住。监控 `taps_in_use` 指标,与预期 sandbox 数量上限比保持 ≥ 1.5x buffer
- **TAP 预设** 在大并发下建议 `3000+`,否则秒级 burst 创建会失败
- `rp_filter=0` 是**必开** —— 不开的话 TPROXY 会让 packet 直接被内核丢,大量 "host 内部 silicon silicon" 错
- host 上同时跑 docker / kube,`rp_filter=0` 不能**全局**只针对 cube-dev bridge,所以要:
  ```bash
  # 只对 cube-dev disable rp_filter
  sysctl -w net.ipv4.conf.cube-dev.rp_filter=0
  # 其它接口恢复
  sysctl -w net.ipv4.conf.default.rp_filter=1
  ```
- 反 sniff 攻击:guest 内 ip 命令不需要,但 guest 内能用 raw packet 套接字 —— 这是 host kernel 把 sandbox 完全隔离在 tap 后就一次性过滤的
