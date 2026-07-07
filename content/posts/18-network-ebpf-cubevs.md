---
title: "3.4.1 网络隔离 - 主机侧 eBPF (CubeVS)"
date: 2026-07-04T14:29:05+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/18-network-ebpf-cubevs/"
summary: "CubeVS 是 CubeSandbox 的内核态 L3/L4 网络安全层。三个 eBPF 程序挂在 TC (traffic control) 钩子点,在内核态直接做策略执行,不必到 user space:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 3.4.1 网络隔离 - 主机侧 eBPF (CubeVS)

## 机制原理

**CubeVS** 是 CubeSandbox 的内核态 L3/L4 网络安全层。三个 eBPF 程序挂在 **TC (traffic control) 钩子点**,在内核态直接做策略执行,不必到 user space:

| 程序 | 触发 | 职责 |
|---|---|---|
| `from_cube` | 来自 sandbox 网络栈(sidecar/virtio-net) | 做 SNAT / 内核 session tracking |
| `from_world` | 来自外部世界 / host | 做 anti-spoofing,policy check |
| `from_envoy` | 来自 CubeEnvoy(其他 sidecar) | 路由 + RBAC |

依据: `docs/architecture/network.md:30-100` 三程序架构表

代码落地:`CubeNet/cubevs/miscs.go:60-80` 中有 BPF map 加载、常量重写等:`rlimit.RemoveMemlock()`(提高 BPF map 内存锁上限,以容纳大 BPF 程序)。

#### 内置 always-deny CIDR

`docs/architecture/network.md:200-260` 显式声明了 sandbox 永远无法访问的目标:

```text
10.0.0.0/8           # 保留私网
127.0.0.0/8          # loopback
169.254.0.0/16       # link-local
172.16.0.0/12        # 保留私网
192.168.0.0/16       # 保留私网
```

sandbox IP 固定 `169.254.68.6`,网关 `169.254.68.5`。
主机端口空间分区:

```text
10000-19999    # 本机临时端口
20000-29999    # CubeProxy 入站
30000-65535    # SNAT 出站
```

## 为什么 CubeSandbox 这样设计

- **内核态执行 ≠ user space proxy** —— 走 user space proxy(L4 NAT)就会被攻击者用流量打爆 CPU。eBPF TC 程序每包开销 < 1μs
- **三层 flow 分类** —— 区分 inside / outside / sidecar,policy 在不同 hook 点表达
- **always-deny 私网** —— 阻止 sandbox 内非法访问 host 上 kube-apiserver / mysql / redis 等敏感内部网
- **端口空间规划** —— host 上每个角色端口明确分离,debug 时直接看端口就知道是哪个组件

## 如何使用 / 配置

#### 启动 eBPF 加载

```bash
# cubevs 启动时会把 BPF 对象 load 到 /sys/fs/bpf/cubevs/
ls -la /sys/fs/bpf/cubevs/ | head
# 每个 sandbox 分配专属 TAP(z 前缀)
ip link | grep '^-' | awk '{print $2}' | grep ^z
# 输出示例:
# z1234: <BROADCAST,MULTICAST> ...
```

#### 写 IP allowlist / denylist

通过 CubeProxy 配置下发,落在 BPF map:

```yaml
# CubeMaster conf.yaml 中:
cubevs:
  ingress:
    - name: "deny-private-net"
      cidr: ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
      action: "deny"
  egress:
    - name: "allow-llm-api"
      domain: "api.openai.com"
      action: "allow"
    - name: "allow-my-bucket"
      cidr: "203.0.113.42/32"
      action: "allow"
```

#### 数据路径

```
guest-net-stack → TAP(z1234) → cube-dev bridge
                                ↓
                          TC ingress (from_world) → BPF program
                                ↓
                          TC egress (from_cube)  → BPF program
                                ↓
                          veth → host
```

#### 实时观察

```bash
# 用 tc tool 看 BPF 统计
tc -s filter show dev cube-dev ingress

# /sys/fs/bpf/cubevs/ 里的 map 可以直接看
bpftool map show
bpftool map dump id <id>
```

**注意**:

- **`/dev/kvm` 之外 / 不要把 BPF program 直接放在生产 traffic 路径** —— BPF verifier 会在加载时 sanity-check,但 custom BPF 仍可能 panic
- eBPF 程序由 cubevs 编译并加载,**不要自行在生产 host 手写 BPF** —— 通过 CubeMaster 配置就行
- always-deny CIDR 包含 sandbox IP 自身 `169.254.68.6/32` 不允许出站(除网关 169.254.68.5)—— 这看似"自我拒绝",但 169.254.68.5 在 allowlist 中,所以 sandbox 能继续走 CubeProxy 出去
- 高峰时观察 CPU:**eBPF 在大量 map miss 时会反弹**,系统监控中关注 `cubevs_drop_total` 指标
- 一旦 sandbox IP 段变更(改了 CIDR 段),**所有 BPF map 都需重新 reload**,Cubelet 会自动处理但要预留时间
