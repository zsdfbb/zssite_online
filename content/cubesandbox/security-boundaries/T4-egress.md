---
title: "T4 Egress — Guest → Internet"
date: 2026-07-06T23:20:09+08:00
draft: false
tags: ["cubesandbox", "security", "T4"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/boundaries/t4-egress/"
summary: "一句话定位: Guest 内 sandbox 通过 host 网络栈出到互联网 (LLM API / 第三方服务 / 模型下载) 的强制审计面。"
cover:
  image: ""
  alt: ""
  caption: ""
---
# T4 Egress — Guest → Internet

> 一句话定位: Guest 内 sandbox 通过 host 网络栈出到互联网 (LLM API / 第三方服务 / 模型下载) 的强制审计面。
> 边界类型: 真边界 (信任跃迁) —— 流量从可信 guest 出到不可信公网。
> SVG 位置: x=700, y=882 (下方) ,蓝框,标"T4 出网边界 (Guest → Internet)"。

## 1. 边界概述

```
   ┌──────────────── Guest (UNTRUSTED) ────────────────┐
   │   sandbox 进程                                     │
   │   - LLM API 调用                                   │
   │   - 模型下载                                        │
   │   - 第三方 HTTP(S)                                  │
   └─────────────────────┬─────────────────────────────┘
                         │ TAP z-XXXX (link-local 169.254.68.6/32)
                         ▼
            ╔═══════════════════════════════════╗
            ║   T4  出网边界 (Egress)            ║  ← 本边界
            ║   eBPF L3/L4 → TPROXY → L7 MITM   ║
            ╚═══════════════╤═══════════════════╝
                             │ (1) eBPF TC 拦截 + policy
                             │ (2) mangle TPROXY 改路由
                             │ (3) OpenResty L7 MITM 校验
                             ▼
                  External Internet (Untrusted)
```

**信任跃迁语义**: 出 T4 后,流量被 eBPF 内核态策略审计,经 TPROXY 透明代理走 OpenResty L7 拦截,然后才到达不可信公网。**重要**: guest 内的 sandbox 自身**不持有 LLM API key / 第三方凭据**——这些由 CubeEgress 在 OpenResty 层通过 `header rewrite` 注入,凭据完全留在 host 信任域内。

## 2. 涉及的纵深防御层

| 层 | 名称 | 是否参与 | 在本边界的作用 |
|----|------|---------|--------------|
| L1 | WebUI 域 | ❌ | T4 是 sandbox 出网,不经 WebUI |
| L2 | 控制面域 | ❌ | T4 由 sandbox spec 启动,不直接经 L2 |
| L3 | host 进程域 | ✅ | CubeEgress (OpenResty) 进程的 seccomp + cap drop + no_reaper |
| L4 | host 内核域 | ✅★ | eBPF CubeVS (3 TC 程序) 、TAP sysctl、cgroup 出站带宽、iptables mangle TPROXY |
| L5 | guest OS 域 | ✅ | sandbox 内出网路由、TAP `z` 前缀命名空间、eBPF ARP proxy + SNAT + LPM-trie policy |
| L6 | 存储域 | ✅ | virtiofs lower_dir (作为出网数据落地,例如模型下载落盘) |
| L7 | 可观测性域 | ✅ | CubeEgress 访问日志、TPROXY 拦截记录、credential inject 日志 |

## 3. 机制清单

### 3.1 L3 (host 进程域)

#### 机制: CubeEgress (OpenResty) 进程 seccomp + cap drop

- **文件位置**: `CubeEgress/scripts/cube-proxy-iptables-init.sh` (本系列新增,原清单未列) + OpenResty 默认 seccomp profile
- **作用**: OpenResty worker 进程受 seccomp 保护,只允许 HTTP 处理相关 syscall
- **配置/启用**: 编译期 OpenResty 默认配置
- **与本边界的关联**: T4 中 L7 MITM 的进程沙箱

### 3.2 L4 (host 内核域) ★

#### 机制: eBPF CubeVS (3 个 TC 程序)

- **文件位置**: `CubeNet/cubevs/miscs.go:60-80` (`rlimit.RemoveMemlock()`) ;`docs/architecture/network.md:30-100` 三程序架构
- **作用**: **3 个 eBPF 程序** (`from_cube` / `from_world` / `from_envoy`) 在 TC 钩子点实现 **ARP proxy、SNAT、policy check、session tracking**,全部在内核态执行
- **配置/启用**: 启动时加载 BPF 对象到 `/sys/fs/bpf/`,每个沙箱分配专属 TAP
- **与本边界的关联**: T4 的**第一道拦截**——在 L3/L4 线速拦截出网流量

#### 机制: 防火墙级 sysctl (TPROXY 重定向支撑)

- **文件位置**: `CubeEgress/scripts/cube-proxy-iptables-init.sh:60-80` (`apply_sysctls` 函数)
- **作用**: `net.ipv4.conf.all.rp_filter=0`、`net.ipv4.conf.cube-dev.route_localnet=1`、`net.ipv4.conf.cube-dev.accept_local=1` 支持 TPROXY 重定向到 lo
- **配置/启用**: 启动时由 iptables-init.sh 应用
- **与本边界的关联**: T4 中 TPROXY 必须依赖这些 sysctl 才能工作

#### 机制: iptables mangle TPROXY (L4 → L7)

- **文件位置**: `CubeEgress/scripts/cube-proxy-iptables-init.sh:60-90`
- **作用**: `iptables -t mangle -A "${CHAIN}" -i cube-dev -p tcp --dport 80/443 -j TPROXY --on-ip 192.168.0.1` —— 把出网 80/443 重定向到本地 OpenResty
- **配置/启用**: 启动时应用 iptables 规则
- **与本边界的关联**: T4 的**第二道拦截**——把 L3/L4 流量桥接到 L7 MITM

#### 机制: built-in always-deny CIDR

- **文件位置**: `docs/architecture/network.md:200-260`
- **作用**: 内置 always-deny CIDR: `10.0.0.0/8, 127.0.0.0/8, 169.254.0.0/16, 172.16.0.0/12, 192.168.0.0/16` —— 防止 sandbox 出网到内网/LB/元数据服务
- **配置/启用**: 硬编码,eBPF policy 默认拒绝
- **与本边界的关联**: T4 的内网保护——即使 L7 失守,guest 也不能访问 host 内网

#### 机制: cgroup 出站带宽限制

- **文件位置**: `agent/rustjail/src/cgroups/` (`notifier.rs` 类) (本系列新增,原清单未列)
- **作用**: 每个 sandbox 的 cgroup 配置网络 IO 带宽上限,防止 DoS
- **配置/启用**: 通过 OCI spec `resources.network` 字段 (若支持)
- **与本边界的关联**: T4 上出网速率限制

### 3.3 L5 (guest OS 域)

#### 机制: TAP 设备隔离 (`z` 前缀 + link-local)

- **文件位置**: `Cubelet/network/plugin_tap.go:43-50` (`const tapNamePrefix = "z"; const cubeDev = "cube-dev"; const eth0 = "eth0";`) ;`docs/blog/posts/2026-06-23-cubesandbox-network-deep-dive.md:90-100`
- **作用**: 每个沙箱独立 TAP (`z` 前缀),固定 link-local IP `169.254.68.6/32` 网关 `169.254.68.5`,无广播域,无 L2 共享
- **配置/启用**: network-agent 维护 500+ TAP 池,沙箱启动时分配
- **与本边界的关联**: T4 上 guest 的网络出口唯一通道

#### 机制: eBPF ARP proxy + SNAT + LPM-trie policy

- **文件位置**: `docs/architecture/network.md` (eBPF 三程序说明) ;`CubeNet/cubevs/miscs.go` (本系列新增,原清单未列)
- **作用**: 内核态做 ARP 响应 (沙箱看不到真实网关) + SNAT (沙箱内 IP `169.254.68.6` 翻译成 host 端口 `30000-65535`) + LPM-trie 匹配策略
- **配置/启用**: eBPF 启动时加载
- **与本边界的关联**: T4 的 host 内核态流量改写

#### 机制: 端口空间分区 (30000-65535 SNAT 出站)

- **文件位置**: `docs/architecture/network.md` (本系列新增,原清单未列)
- **作用**: 主机端口空间分区:`10000-19999` (本机临时端口) + `20000-29999` (CubeProxy 入站) + `30000-65535` (SNAT 出站)
- **配置/启用**: eBPF 策略
- **与本边界的关联**: T4 上 SNAT 出站使用的端口范围,避免与 T5 入站冲突

#### 机制: 沙箱内部 IP 固定 `169.254.68.6`

- **文件位置**: `docs/architecture/network.md:200-260`
- **作用**: 所有 sandbox 内部 IP 都是 `169.254.68.6` (link-local,无路由意义) + 网关 `169.254.68.5` —— 避免 IP 冲突,简化 eBPF 策略
- **配置/启用**: eBPF 默认
- **与本边界的关联**: T4 上 guest 网络命名空间隔离

### 3.4 L6 (存储域)

#### 机制: virtiofs lower_dir (出网数据落地)

- **文件位置**: `CubeShim/shim/src/container/rootfs.rs:7-25` (`OverlayInfo { virtiofs_lower_dir: Vec<String> }`)
- **作用**: 模型下载 / LLM 响应落盘到 virtiofs lower_dir,通过 virtiofs 暴露给 sandbox
- **配置/启用**: 容器 spec 的 `mounts` 字段
- **与本边界的关联**: T4 出网数据的存储路径 (本系列新增,原清单未列)

### 3.5 L7 (可观测性域)

#### 机制: CubeEgress 访问日志 (L7 MITM 拦截)

- **文件位置**: `CubeEgress/nginx.conf` (access_log 配置) (本系列新增,原清单未列)
- **作用**: OpenResty 记录所有 L7 rule 命中 (`allow` / `deny` / `inject`)、响应码、TLS SNI、host header
- **配置/启用**: 默认开启,日志路径 `CubeEgress` 容器内
- **与本边界的关联**: T4 上 L7 的可观测性落点

#### 机制: credential inject (header rewrite,无 secret 落地 sandbox)

- **文件位置**: `CubeEgress/nginx.conf` (lua 注入逻辑) ;`docs/guide/egress-network-policy.md`
- **作用**: LLM API key / 第三方凭据由 CubeEgress header rewrite 注入到出网请求,sandbox 内不持有 secret
- **配置/启用**: 通过 `network.rules[].action.inject.headers` 字段
- **与本边界的关联**: T4 的核心安全特性——secret 完全留在 host 信任域

#### 机制: TPROXY 拦截记录

- **文件位置**: `CubeEgress/scripts/cube-proxy-iptables-init.sh` (iptables log 规则) (本系列新增,原清单未列)
- **作用**: iptables mangle 表的 TPROXY 命中计数,可触发 audit
- **配置/启用**: 默认开启
- **与本边界的关联**: T4 上 L4 → L7 桥接点的可观测性

## 4. 关键交互

- **数据流入自**: T3 (KVM CORE) —— guest 内 sandbox 出网流量
- **数据流出到**:
  - **External Internet**: LLM API (OpenAI / Anthropic) / 第三方服务 / 模型下载 (HuggingFace / S3)
  - **T2 (Operator Trust)**: 通过修改 `network.rules[]` 配置,运维可调整 L7 policy
  - **L7**: 审计日志落 `/data/log/`
- **同信任域 L 层依赖**: L5 (guest 出网路由) → L4 (eBPF 拦截 + sysctl + iptables) → L3 (OpenResty 进程) → L7 (L7 MITM 注入 + 日志)

## 5. 设计权衡

1. **为什么 eBPF 在 L4 而不是在 L5**: eBPF 程序挂载点必须是 host 内核的 TC classifier,如果放在 guest 内,guest root 可通过 `bpftool prog detach` 卸载/替换策略。这违反了"策略执行点必须在不可信域外"原则。L4 是 host 信任域的最后一关,eBPF 在 L4 是必然选择。
2. **为什么 T4 不用 iptables 走传统路径,而用 eBPF + TPROXY**: 传统 iptables 在 L3/large-sacle 时性能崩塌,且无法做 session tracking。eBPF TC 程序在内核态做 policy check,session table 由 BPF map 维护,线速拦截;TPROXY 把 L7 决策从 iptables 提到 OpenResty,L4 只管路由,职责清晰。
3. **为什么 always-deny CIDR 写死**: 内网段 (`10.0.0.0/8` 等) 是 host 内网/LB/元数据服务。sandbox 出网到这些地址意味着横向移动攻击,必须拒绝。写死避免运维配置失误。
4. **为什么 sandbox 内 IP 都是 `169.254.68.6`**: 看似冲突,实则所有 sandbox 都在自己的 network namespace,IP 重复是设计而非 bug。eBPF 通过 TAP 接口区分不同 sandbox,不需要 IP 唯一。这简化了 eBPF 策略 (只需看 TAP,不必解析 IP)。
5. **为什么凭据由 CubeEgress 注入而非 sandbox 持有**: 这是**安全>便利**的决定——把 LLM API key 放在 sandbox 内意味着 sandbox 任何 RCE 都会泄露;放在 CubeEgress 内则只在出网那一刻注入,sandbox 完全看不到 secret。代价是 L7 rule 配置稍微复杂。
6. **为什么 TPROXY 重定向到 lo 而不是单独 IP**: 重定向到 lo (`192.168.0.1`) 是为了让 OpenResty 监听 lo 即可,不需要额外的网络接口。配合 `accept_local=1` 和 `route_localnet=1` 实现"自己路由给自己"。这是 OpenResty TPROXY 模式的标准做法。
7. **为什么 L5 在 T4 也参与**: guest 内的 sandbox 需要配置默认路由指向 `169.254.68.5` (eBPF 伪网关) 才能出网。这是 L5 的"客户端配置"——guest kernel 必须接受 eBPF 伪网关,这要求 guest kernel 不能强制 RPF (reverse path filtering),而 T3 启动参数已经关闭 RPF。