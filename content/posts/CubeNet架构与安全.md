---
title: "CubeNet 架构、处理流程与安全配置"
date: 2026-07-08T00:00:00+08:00
draft: false
tags: ["cubesandbox"]
categories: ["cubesandbox"]
comments: true
showToc: true
weight: 100
---


> 调研时间: 2026/07/08
> 调研范围: `/home/zs/Develop/TraceCubeSandbox/CubeNet/` 全量 Go + eBPF C 源码
> 目的: 系统性梳理 CubeNet (网络虚拟化 eBPF 数据面) 的架构、处理流程与安全配置
> 配套文档: [security-boundaries/T4-cubenet-datapath.md](../安全边界/T4-cubenet-datapath.md) (边界视角,本文档是"内部视角")
>
> 每节都带文件位置证据,可以直接引用。

---

## 1. 概述

CubeNet 是 CubeSandbox 的**网络虚拟化与安全执行层**,提供基于 eBPF 的内核态数据面,负责 sandbox 网络连接、NAT、策略执行和连接跟踪。

| 属性 | 值 |
|------|-----|
| **语言** | Go (用户态管理) + C (eBPF 内核数据面) |
| **eBPF 库** | `github.com/cilium/ebpf` v0.17.3 |
| **TC 管理** | `github.com/florianl/go-tc` |
| **网卡管理** | `github.com/vishvananda/netlink` (间接) |
| **vmlinux** | bpftool BTF dump (amd64 + arm64) |
| **用户态包** | cubevs (Go library) |
| **内核态** | src/*.bpf.c (BPF TC 程序) |
| **诊断工具** | cubevsmapdump (CLI) |
| **依赖 (go.mod)** | `cilium/ebpf v0.17.3`, `florianl/go-tc v0.4.1`, `golang.org/x/sys v0.38.0` |

**核心职责**:
- Sandbox TAP 设备网络连接
- 有状态 NAT (SNAT/DNAT) 支持 TCP/UDP/ICMP
- 出站网络策略执行 (LPM trie 允许/拒绝)
- DNS 感知策略 (域名 → IP 动态学习)
- L7 代理重定向 (Envoy 集成)
- 连接跟踪与会话回收
- ARP 代理

---

## 2. 架构

### 2.1 目录结构

```
CubeNet/
├── cubevs/                          # Go 用户态库
│   ├── cubevs.go                    # 核心类型 (Params/TAPDevice/常量/静态断言)
│   ├── tc.go                        # Linux TC (qdisc + BPF filter)
│   ├── tap.go                       # TAP 设备生命周期 (Add/Del/List/Get)
│   ├── port.go                      # 端口映射管理 (Add/Del/List/Get)
│   ├── netpolicy.go                 # 出站网络策略 (allow_out_v2/deny_out)
│   ├── dnspolicy.go                 # DNS 域名策略 (dns_allow)
│   ├── snat.go                      # SNAT IP 配置 (SetSNATIPs)
│   ├── miscs.go                     # 初始化 + BPF 加载 + TC 挂载 + tail-call 管理
│   ├── map.go                       # BPF pinned map 加载辅助
│   ├── reaper.go                    # 会话回收 goroutine (TCP/UDP/ICMP)
│   ├── dns_reaper.go                # DNS 状态回收 (过期策略 + 查询跟踪)
│   ├── migration.go                 # allow_out v1 → v2 迁移
│   ├── dump.go                      # BPF map 诊断转储 (11 种 map)
│   ├── util.go                      # 工具函数 (IP/MAC 转换, htons/ntohs)
│   ├── netpolicy_test.go            # 策略单元测试
│   ├── dump_test.go                 # 转储测试
│   ├── miscs_test.go                # 初始化测试
│   └── cmd/cubevsmapdump/           # 诊断 CLI
│       └── main.go                  # 入口 (flag 解析 + JSON 输出)
├── src/                             # eBPF C 内核程序
│   ├── mvmtap.bpf.c                 # MVM TAP 入口 (SNAT + 策略 + DNS 查询 + ARP)
│   ├── nodenic.bpf.c                # 主机网卡入口 (DNAT + 端口映射 + DNS 响应)
│   ├── localgw.bpf.c                # 网关设备入口 (DNAT → MVM)
│   ├── cubevs.h                     # 核心共享头文件 (常量/结构体/静态断言)
│   ├── map.h                        # BPF map 定义 (14 个 map)
│   ├── nat.h                        # NAT 基础函数 (snat/dnat 宏)
│   ├── session.h                    # 会话辅助 (lazy refresh+mark+create)
│   ├── tcp.h                        # TCP conntrack 状态机 (2x6x10 状态表)
│   ├── udp.h                        # UDP 会话 (UNREPLIED/REPLIED)
│   ├── icmp.h                       # ICMP 会话 (Echo 跟踪)
│   ├── dns_query.h                  # DNS 查询处理 (chunked 解析+匹配+跟踪)
│   ├── dns_response.h               # DNS 响应处理 (A 记录学习)
│   ├── dns_parser.h                 # DNS 协议解析 (header/qname/rr)
│   ├── l2l3.h                       # MAC 地址重写辅助
│   ├── skb.h                        # SKB 头部拉取 (pull_headers 宏)
│   └── jhash.h                      # Jenkins hash
└── vmlinux/                         # 内核 BTF 类型定义
    ├── amd64/vmlinux.h
    ├── arm64/vmlinux.h
    └── Makefile
```

### 2.2 eBPF 程序架构

```
                 ┌───────────────────────────────────┐
                 │        主机网卡 (nodenic)           │
                 │   from_world: DNAT + 端口映射       │
                 │   ingress: 入站流量                  │
                 │   SEC("tc") int from_world           │
                 └──────────┬────────────────────────┘
                            │
                 ┌──────────▼────────────────────────┐
                 │      cubegw0 (网关设备)             │
                 │   from_envoy: DNAT + SNAT → MVM     │
                 │   egress: L7 代理流量               │
                 │   SEC("tc") int from_envoy           │
                 └──────────┬────────────────────────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
     ┌────────▼───┐  ┌─────▼──────┐  ┌───▼────────┐
     │ MVM TAP 1  │  │ MVM TAP 2  │  │ MVM TAP N  │
     │ from_cube:  │  │            │  │            │
     │ SNAT + 策略 │  │            │  │            │
     │ + DNS 查询  │  │            │  │            │
     │ + ARP 代理  │  │            │  │            │
     │ SEC("tc")   │  │            │  │            │
     │ int from_cube│  │            │  │            │
     └────────────┘  └────────────┘  └────────────┘
```

**程序文件对应关系**:
- `src/mvmtap.bpf.c:927` — `SEC("tc") int from_cube`: 挂载到 MVM TAP 设备 ingress,处理出站流量
- `src/nodenic.bpf.c:406` — `SEC("tc") int from_world`: 挂载到主机网卡 ingress + cube-router egress,处理入站流量
- `src/localgw.bpf.c:18` — `SEC("tc") int from_envoy`: 挂载到 cubegw0 egress,处理 L7 代理流量

### 2.3 DNS 尾调用链 (5 段 tail-call)

```
from_cube (mvmtap)
   │
   │ udp->dest == 53 && dns_policy_enabled
   │
   ▼
dns_parse_chunk (mvmtap)    ← 分段解析 QNAME
   │
   ▼
dns_rev_chunk (mvmtap)      ← 反转域名为 LPM key
   │
   ▼
dns_finish (mvmtap)         ← 匹配 dns_allow + 跟踪 + finish UDP NAT
   │
   ▼
  ... (继续 UDP NAT 出站)

from_world (nodenic)
   │
   │ udp->source == 53 && DNS 响应
   │
   ▼
dns_handle_response_prog (nodenic)  ← 学习 A 记录到 allow_out_v2
   │
   ▼
dns_response_finish_prog (nodenic)  ← 完成入站 UDP NAT (reverse-NAT)
```

**关键文件位置**:
- `src/dns_query.h:234-265` — `dns_handle_query`: 查询入口,初始化状态后 tail-call 到 dns_parse_chunk
- `src/mvmtap.bpf.c:821-921` — 三段尾调用 (parse/reverse/finish)
- `src/nodenic.bpf.c:441-491` — 两段尾调用 (response/finish)
- `src/dns_response.h:200-249` — `dns_handle_response`: A 记录学习逻辑
- `src/map.h:224-276` — 尾调用状态 map + jump table

### 2.4 交互关系

```
┌──────────────┐  TAP fd     ┌──────────────┐  内核 eBPF   ┌──────────┐
│   Cubelet    │ ──────────▶│  CubeNet     │ ───────────▶│   主机    │
│  (节点代理)   │  AddTAP    │  (cubevs)    │  TC filter   │  网卡    │
│              │  DelTAP    │              │              │          │
│              │  PortMap   │              │              │          │
│              │  SetSNATIP │              │              │          │
└──────────────┘            └──────┬───────┘              └──────────┘
                                   │
                          ┌────────▼────────┐
                          │  network-agent  │
                          │  (设备 + 策略编排)│
                          └─────────────────┘
```

**关键文件位置**:
- `cubevs/cubevs.go:17-40` — `Params` 结构体: 定义所有网络参数的入口
- `cubevs/miscs.go:230-283` — `Init()`: 一次性初始化,加载 3 个 BPF 对象 + 挂载 TC filter
- `cubevs/miscs.go:286-304` — `AttachFilter()`: 为每个 TAP 设备挂载 BPF TC filter
- `cubevs/tap.go:46-55` — `AddTAPDevice()`: 注册 TAP 设备 + 应用策略

---

## 3. 处理流程

### 3.1 出站流量 (SNAT + 策略)

来源: `src/mvmtap.bpf.c:927-1067` (`from_cube` 函数)

```
MVM TAP                    CubeNet (BPF from_cube)               主机网卡
  │                              │                                 │
  │ 出站包 (src: MVM IP)          │                                 │
  │ ────────────────────────────▶│                                 │
  │                              │                                 │
  │                              │ ① SNAT (MVM IP → mvm_inner_ip)  │
  │                              │   src/mvmtap.bpf.c:966          │
  │                              │   snat(skb, l3, mvm_meta->ip)   │
  │                              │                                 │
  │                              │ ② 流量分离                        │
  │                              │   - daddr == mvm_gateway_ip?     │
  │                              │     → DNAT → cubegw0 (L7 代理)  │
  │                              │   - TCP 端口映射?                 │
  │                              │     → SNAT + 直发主机网卡         │
  │                              │   - 否则: 进入策略检查             │
  │                              │                                 │
  │                              │ ③ 策略检查 (check_net_policy)     │
  │                              │   src/mvmtap.bpf.c:123-162      │
  │                              │   - allow_out_v2 LPM trie 优先   │
  │                              │   - deny_out LPM trie 其次        │
  │                              │   - 默认允许                      │
  │                              │   - 永远拒绝: 见 §4.1             │
  │                              │                                 │
  │                              │ ④ L7 标记检查                     │
  │                              │   src/mvmtap.bpf.c:164-171      │
  │                              │   - L7_REQUIRED + port 80/443    │
  │                              │     → bpf_redirect(cubegw0, ..)  │
  │                              │                                 │
  │                              │ ⑤ 状态检测 NAT                    │
  │                              │   TCP: do_tcp_nat → 6 状态机     │
  │                              │   UDP: do_udp_nat → UNREPLIED/   │
  │                              │              REPLIED            │
  │                              │   ICMP: do_icmp_nat → Echo 跟踪  │
  │                              │                                 │
  │                              │ ⑥ 重写 L2 + 重定向                │
  │                              │   set_mac_pair + bpf_redirect    │
  │                              │                                 │
  │                              │ ───────────────────────────────▶│
```

**策略优先级** (src/mvmtap.bpf.c:113-117 注释):
```
allow_out_v2 > deny_out > default allow
```

即:
1. `allow_out_v2` 匹配 → 显式放行 (即使 `deny_out` 也匹配)
2. `deny_out` 匹配 → 拒绝 (TCP 发 RST,其他丢包)
3. 都不匹配 → 默认放行

**关键文件位置**:
- 策略检查: `src/mvmtap.bpf.c:123-162` — `check_net_policy()`
- TCP NAT: `src/mvmtap.bpf.c:709-817` — `do_tcp_nat()`
- UDP NAT: `src/mvmtap.bpf.c:567-661` — `do_udp_nat_inline()`
- ICMP NAT: `src/mvmtap.bpf.c:465-553` — `do_icmp_nat()`
- TCP 回复 RST: `src/mvmtap.bpf.c:285-397` — `tcp_reply_reset()`
- 策略拒绝后丢包: `src/mvmtap.bpf.c:1018-1022`

### 3.2 入站流量 (DNAT + 端口映射)

来源: `src/nodenic.bpf.c:406-429` (`from_world` 函数)

```
Internet                    CubeNet (BPF from_world)            MVM TAP
  │                              │                                 │
  │ 入站包 (dst: host IP:port)    │                                 │
  │ ────────────────────────────▶│                                 │
  │                              │                                 │
  │                              │ ① 协议分发                        │
  │                              │   src/nodenic.bpf.c:419-428     │
  │                              │   TCP → do_tcp_nat              │
  │                              │   UDP → do_udp_nat              │
  │                              │   ICMP → do_icmp_nat            │
  │                              │                                 │
  │          ──── TCP ────       │                                 │
  │                              │ ②a 端口映射查找 (nodenic 网卡)   │
  │                              │   src/nodenic.bpf.c:392-396     │
  │                              │   remote_port_mapping[dport]    │
  │                              │   → tcp_nat_proxy: DNAT + 直发  │
  │                              │                                 │
  │                              │ ②b TCP session 查找              │
  │                              │   tcp_nat_session: 查 ingress   │
  │                              │   → 找 egress → reverse DNAT   │
  │                              │   → redirect MVM TAP            │
  │                              │                                 │
  │          ──── UDP ────       │                                 │
  │                              │ ③ UDP session 查找               │
  │                              │   udp_nat_session:               │
  │                              │   - DNS 响应: tail-call 处理     │
  │                              │   - 普通: udp_nat_rewrite       │
  │                              │                                 │
  │          ──── ICMP ────      │                                 │
  │                              │ ④ ICMP Echo Reply 处理          │
  │                              │   icmp_nat_session              │
  │                              │                                 │
  │                              │ ───────────────────────────────▶│
  │                              │   DNAT 后包到达 MVM TAP          │
```

### 3.3 DNS 感知策略

来源:
- 查询路径: `src/dns_query.h` + `src/mvmtap.bpf.c` (出站)
- 响应路径: `src/dns_response.h` + `src/nodenic.bpf.c` (入站)

```
MVM                         CubeNet                         DNS Server
  │                            │                               │
  │ DNS 查询 (domain.com)       │                               │
  │ UDP/53 → MVM TAP           │                               │
  │ ──────────────────────────▶│                               │
  │                            │                               │
  │                            │ ① 查询入口                      │
  │                            │   from_cube: udp->dest==53     │
  │                            │   dns_handle_query()           │
  │                            │   src/mvmtap.bpf.c:1050-1054   │
  │                            │                               │
  │                            │ ② 解析 DNS 查询域名             │
  │                            │   尾调用: dns_parse_chunk      │
  │                            │   → dns_rev_chunk              │
  │                            │   → dns_finish                 │
  │                            │   分段每次 64 字节               │
  │                            │   src/dns_query.h:35-90         │
  │                            │                               │
  │                            │ ③ 域名策略匹配                   │
  │                            │   反转域名 → LPM trie           │
  │                            │   dns_allow[ifindex]           │
  │                            │   src/dns_query.h:128-143      │
  │                            │                               │
  │                            │ ④ 跟踪允许的查询                 │
  │                            │   dns_track_allowed_query()    │
  │                            │   → dns_query_track map        │
  │                            │   TTL: 10 秒                   │
  │                            │   src/dns_query.h:182-206      │
  │                            │                               │
  │                            │ ─────────────────────────────▶│
  │                            │                               │
  │                            │ ◀───────────────────────────── │
  │                            │ DNS 响应 (A record: 1.2.3.4)  │
  │                            │                               │
  │                            │ ⑤ 响应入口                      │
  │                            │   from_world: udp->source==53  │
  │                            │   udp_nat_session → tail-call  │
  │                            │   src/nodenic.bpf.c:259-273    │
  │                            │                               │
  │                            │ ⑥ A 记录学习                    │
  │                            │   dns_learn_response_ip()      │
  │                            │   写入 allow_out_v2:           │
  │                            │   IP=1.2.3.4, TTL=DNS TTL     │
  │                            │   flags 继承自 dns_allow       │
  │                            │   src/dns_response.h:102-126   │
  │                            │                               │
  │                            │ ⑦ 完成 reverse UDP NAT          │
  │                            │   dns_response_finish_prog     │
  │                            │   → udp_nat_rewrite → MVM     │
  │                            │                               │
  │ ◀─────────────────────────│                               │
  │ DNS 响应 (不变)             │                               │
```

**关键常量**:
- DNS 查询跟踪 TTL: `src/cubevs.h:40` — `DNS_QUERY_TRACK_TTL_NS = 10 秒`
- DNS 最大 QNAME 长度: `src/cubevs.h:36` — `MAX_DNS_NAME_LEN = 256`
- DNS 最大响应答案数: `src/dns_parser.h:31` — `DNS_MAX_RESPONSE_ANSWERS = 8`
- DNS 尾调用分段大小: `src/dns_query.h:30` — `DNS_PARSE_CHUNK_SIZE = 64`

### 3.4 L7 代理重定向 (Envoy)

来源: `src/mvmtap.bpf.c:164-171` + `src/localgw.bpf.c`

```
from_cube (MVM TAP)
  │
  │ check_net_policy 返回 policy_value.flags & L7_REQUIRED
  │ && (dport == 80 || dport == 443)
  │
  ▼
bpf_redirect(cubegw0_ifindex, BPF_F_INGRESS)
  │
  ▼
cubegw0 (网关设备)
  │
  │ from_envoy (localgw.bpf.c:18)
  │ DNAT: daddr → mvm_inner_ip
  │ SNAT: saddr==cubegw0_ip → mvm_gateway_ip
  │ mvmip_to_ifindex → bpf_redirect(TAP ifindex, 0)
  │
  ▼
MVM TAP → Sandbox (Envoy 代理后的流量)
```

**关键文件位置**:
- `src/mvmtap.bpf.c:166-171` — `should_redirect_to_l7_proxy()`: 检查 L7_REQUIRED + port
- `src/mvmtap.bpf.c:1037-1038` — L7 重定向调用点
- `src/localgw.bpf.c:18-59` — `from_envoy()`: DNAT + SNAT + redirect

### 3.5 ARP 代理

来源: `src/mvmtap.bpf.c:29-92` (`handle_arp` 函数)

```
Sandbox ARP Request (who-has 169.254.68.5?)
  │
  ▼
handle_arp(skb, ifindex)
  │
  │ 校验 ARP 头部 (Ethernet/IPv4/Request)
  │
  ▼
构造 ARP Reply:
  - Sender MAC → cubegw0 MAC (ARP 代理)
  - Target MAC → 原始请求者 MAC
  │
  ▼
bpf_redirect(ifindex, 0)  // 发回 TAP 设备
```

**关键文件位置**: `src/mvmtap.bpf.c:29-92` (整个 `handle_arp` 函数)

### 3.6 TCP Conntrack 状态机

来源: `src/tcp.h:68-296`

CubeNet 实现了一个完整的 TCP 连接跟踪状态机,支持 10 个状态和 2 个方向:

```
状态: NONE → SYN_SENT → SYN_RECV → ESTABLISHED → FIN_WAIT/CLOSE_WAIT/LAST_ACK → TIME_WAIT → CLOSE

方向:
  - IP_CT_DIR_ORIGINAL: 出站方向 (MVM → Internet)
  - IP_CT_DIR_REPLY:    入站方向 (Internet → MVM)
```

**状态转换表**: `src/tcp.h:68-196` — `tcp_conntracks[2][6][TCP_CONNTRACK_MAX]`
- TCP 连接超时: `cubevs/reaper.go:114-127` — `tcpTimeouts` (ESTABLISHED = 3h)

---

## 4. 认证与安全机制

### 4.1 永远拒绝的私有地址

来源: `cubevs/netpolicy.go:17-23`

```go
var alwaysDeniedSandboxCIDRs = []string{
    "10.0.0.0/8",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "172.16.0.0/12",
    "192.168.0.0/16",
}
```

这些 CIDR 始终附加到 `deny_out` 条目中 (`netpolicy.go:325`),保护 SSRF 攻击和内网访问:
- `10.0.0.0/8` — 私有 A 类网络
- `127.0.0.0/8` — 本地回环
- `169.254.0.0/16` — 链路本地地址 (含 MVM 内部 IP)
- `172.16.0.0/12` — 私有 B 类网络
- `192.168.0.0/16` — 私有 C 类网络

### 4.2 安全特性

| # | 特性 | 位置 | 说明 |
|---|------|------|------|
| S1 | **eBPF 内核态执行** | `src/*.bpf.c` | 绕过用户态,防旁路攻击;TC 程序在内核网络栈中执行 |
| S2 | **硬编码私有地址拒绝** | `cubevs/netpolicy.go:17-23` | 始终附加到 deny_out,SSRF 防护最后防线 |
| S3 | **DNS 动态学习** | `src/dns_response.h:102-126` | 最小权限原则:仅放行 DNS 解析过的 IP,TTL 自动过期 |
| S4 | **有状态防火墙** | `src/session.h` + `src/tcp.h` | TCP 完整 conntrack 状态机 (10 态),UDP/ICMP 伪连接跟踪 |
| S5 | **最大条目限制** | `cubevs/netpolicy.go:361-426` | 防 BPF map 耗尽攻击 (allow_out/deny_out 各 8192,DNS 1024) |
| S6 | **启动清理** | `cubevs/miscs.go:231-233` | 删除旧 `tungrp_to_tuns` 和 `dns_query_track`,防跨 sandbox 污染 |
| S7 | **BPF map pinning** | `/sys/fs/bpf/` | 持久化 BPF map,崩溃恢复后状态不丢失 |
| S8 | **大小静态断言** | `src/cubevs.h:244-260` + `cubevs/cubevs.go:203-311` | 双端 (Go + C) 编译时校验 struct ABI,防版本不匹配 |
| S9 | **go-tc atomic replace** | `cubevs/tc.go:68` | `Filter().Replace()` 原子替换 TC filter,无中断窗口 |
| S10 | **基于 Ifindex 隔离** | 所有 BPF map | 每个 sandbox 独立的内层 LPM trie map,互不干扰 |

### 4.3 底层安全机制说明

**S1 eBPF 内核态执行**:
- BPF 程序通过 verifier 检查后方可加载 (`cilium/ebpf` 库自动处理)
- TC `clsact` qdisc + `direct-action` mode 确保程序在内核网络栈直接处理
- 用户态无法绕过 BPF 策略 (即使进程崩溃,eBPF 程序仍在运行)

**S3 DNS 动态学习流程**:
```
① 出站 DNS 查询 → dns_handle_query()
   ↓
② dns_finish() 匹配 dns_allow 规则
   ↓
③ dns_track_allowed_query() → dns_query_track map (TTL 10s)
   ↓
④ 入站 DNS 响应 → dns_handle_response()
   ↓
⑤ dns_learn_response_ip() → 写入 allow_out_v2
   IP=1.2.3.4, expires_at_ns=now+DNS_TTL
   ↓
⑥ DNS 查询跟踪条目自动删除
```

**学习限制**:
- 仅支持 IN A 记录 (`dns_response.h:132-135`)
- 仅支持标准查询 (`dns_parser.h:97-107`)
- 不会降级已有静态规则 (`dns_response.h:118-123`)

**S8 静态断言**:

Go 侧 (`cubevs/cubevs.go:203-311`):
```go
func _() {
    {   // static assert, make sure MVMIdentity is of size 128
        var arr [128]struct{}
        var obj mvmMetadata
        const size = unsafe.Sizeof(obj)
        _ = arr[size-1]   // error if size > 128
        _ = arr[size-128] // error if size < 128
    }
    // ... (共 10 个静态断言)
}
```

C 侧 (`src/cubevs.h:244-260`):
```c
static __always_inline int _()
{
    int b[sizeof(struct mvm_meta) == 128 ? 1 : -1] = {};
    int d[sizeof(struct lpm_key) == 8 ? 1 : -1] = {};
    // ... (共 12 个静态断言)
    return b[0] + d[0] + ...;
}
```

---

## 5. 配置项

CubeNet 通过 Go API 配置,无配置文件。关键参数通过 `Params` struct 传入:

来源: `cubevs/cubevs.go:17-40`

### 5.1 参数结构

```go
type Params struct {
    MVMInnerIP        net.IP           // Sandbox 内部 IP (默认 169.254.68.6)
    MVMMacAddr        net.HardwareAddr // Sandbox MAC 地址
    MVMGatewayIP      net.IP           // 网关 IP (默认 169.254.68.5)
    Cubegw0Ifindex    uint32           // cubegw0 设备 ifindex
    Cubegw0IP         net.IP           // cubegw0 IP (默认 203.0.113.1)
    Cubegw0MacAddr    net.HardwareAddr // cubegw0 MAC 地址
    EgressSrcMacAddr  net.HardwareAddr // 出站源 MAC
    EgressDstMacAddr  net.HardwareAddr // 出站目标 MAC
    EgressRedirectFlags uint64         // 出站重定向标志
    CubeRouterIfindex uint32           // cube-router ifindex (0=禁用)
    NodeIfindex       uint32           // 主机网卡 ifindex
    NodeIP            net.IP           // 主机 IP
    NodeMacAddr       net.HardwareAddr // 主机 MAC
    NodeGatewayMacAddr net.HardwareAddr // 主机网关 MAC (下一跳)
}
```

### 5.2 关键默认值

eBPF 常量定义在 `src/cubevs.h:74-105`:

| 参数 | 默认值 | 宏 (cubevs.h) | 说明 |
|------|--------|----------------|------|
| MVM Inner IP | `169.254.68.6` | `mvm_inner_ip` | Sandbox 内部 IP |
| MVM Gateway IP | `169.254.68.5` | `mvm_gateway_ip` | 网关 IP (cubegw0) |
| Gateway IP | `203.0.113.1` | `cubegw0_ip` | cubegw0 设备 IP |
| Node NIC | ifindex `2` | `nodenic_ifindex` | 主机网卡索引 |
| SNAT IP 池 | 4 个 | `MAX_SNAT_IPS` | SNAT IP 池大小 |
| Max Sessions | `1,048,576` | `MAX_SESSIONS` | 最大连接数 |
| TCP Established | `3h` | `tcpTimeouts` | 连接超时 |
| UDP Replied | `180s` | `udpTimeouts` | UDP 回复后超时 |
| UDP Unreplied | `30s` | `udpTimeouts` | UDP 无回复超时 |
| ICMP Echo | `30s` | `icmpTimeout` | ICMP Echo 超时 |
| 策略最大条目 | `8192` | `MAX_IP_RULE_ENTRIES` | allow/deny LPM 上限 |
| DNS 域名最大 | `1024` | `MAX_DOMAIN_RULE_ENTRIES` | dns_allow 上限 |
| DNS 查询跟踪 | `65536` | `MAX_DNS_QUERY_TRACK_ENTRIES` | 跟踪上限 |
| 端口映射最大 | `65536` | `MAX_PORTS` | 端口映射上限 |
| SNAT 端口起始 | `30000` | `MAX_PORT_START` | SNAT 端口分配起点 |
| 会话回收间隔 | `5s` | `reapSessionsInterval` | goroutine 扫描间隔 |
| 高水位告警 | `80%` | `maxSessionPercentage` | 会话数 > 80% 发警报 |

### 5.3 启动流程

来源: `cubevs/miscs.go:230-283`

```go
func Init(params Params) error {
    // ① 清理旧 BPF pin (防污染)
    os.Remove(pinPath("tungrp_to_tuns"))
    os.Remove(pinPath(MapNameDNSQueryTrack))

    // ② 加载 3 个 BPF 对象
    loadObject(params, loadLocalgw, "loadLocalgw") // localgw
    loadObject(params, loadMvmtap, "loadMvmtap")   // mvmtap
    refreshDNSTailCalls()                           // 刷新 DNS tail-call 表
    loadObject(params, loadNodenic, "loadNodenic") // nodenic
    refreshDNSTailCalls()                           // 再次刷新 (nodenic 的 DNS 响应处理)

    // ③ 迁移 legacy allow_out v1 → v2
    migrateAllowOutV1ToV2()

    // ④ 挂载 TC filter
    attachTCFilter("from_envoy", cubegw0_ifindex, TCEgress)
    attachTCFilter("from_world", cube_router_ifindex, TCEgress) // 可选
    attachTCFilter("from_world", node_ifindex, TCIngress)
}
```

每个 TAP 设备添加时调用 `AttachFilter()` (`cubevs/miscs.go:286-304`):
```go
func AttachFilter(ifindex uint32) error {
    // 加载 from_cube 程序 → 创建 clsact → 挂载 ingress filter
    attachFilter(ifindex, progFD, "from_cube", TCIngress)
    // 初始化 per-sandbox 策略 map
    initNetPolicy(ifindex)
}
```

---

## 6. BPF Map 模型

来源: `src/map.h` (完整 14 个 BPF map 定义)

### 6.1 核心数据 Map

| Map 名称 | 类型 | Key | Value | 最大条目 | 用途 |
|----------|------|-----|-------|---------|------|
| `mvmip_to_ifindex` | HASH | `__u32` (MVM IP) | `__u32` (ifindex) | 8192 | MVM IP → TAP ifindex |
| `ifindex_to_mvmmeta` | HASH | `__u32` (ifindex) | `struct mvm_meta` (128B) | 8192 | TAP ifindex → 元数据 |
| `remote_port_mapping` | HASH | `__u16` (host port) | `struct mvm_port` (8B) | 65536 | 端口映射: 主机→MVM |
| `local_port_mapping` | HASH | `struct mvm_port` (8B) | `__u16` (host port) | 65536 | 端口映射: MVM→主机 |
| `egress_sessions` | HASH | `struct session_key` (20B) | `struct nat_session` (64B) | 1048576 | 出站 NAT 会话表 |
| `ingress_sessions` | HASH | `struct session_key` (20B) | `struct ingress_session` (16B) | 1048576 | 入站 NAT 会话表 |
| `snat_iplist` | ARRAY | `__u32` (index) | `struct snat_ip` (16B) | 4 | SNAT IP 池 |

### 6.2 策略 Map

| Map 名称 | 类型 | Key | Value | 最大条目 | 用途 |
|----------|------|-----|-------|---------|------|
| `allow_out_v2` | HASH_OF_MAPS | `__u32` (ifindex) | inner LPM trie `(net_policy_value_v2)` | 8192 outer | 出站允许列表 |
| `deny_out` | HASH_OF_MAPS | `__u32` (ifindex) | inner LPM trie `(__u32)` | 8192 outer | 出站拒绝列表 |
| `dns_allow` | HASH_OF_MAPS | `__u32` (ifindex) | inner LPM trie `(dns_allow_value)` | 8192 outer | DNS 域名策略 |
| `dns_query_track` | LRU_HASH | `struct dns_query_track_key` (24B) | `struct dns_query_track_value` (16B) | 65536 | DNS 查询跟踪 |
| `dns_tail_calls` | PROG_ARRAY | `__u32` (slot) | `__u32` (prog FD) | 16 | DNS 尾调用跳转表 |

### 6.3 运行时 Map

| Map 名称 | 类型 | 用途 |
|----------|------|------|
| `dns_query_scratch` | PERCPU_ARRAY(1) | DNS QNAME 解析暂存 (LPM key) |
| `dns_query_state` | PERCPU_ARRAY(1) | DNS 查询解析状态 (chunked) |
| `dns_response_state` | PERCPU_ARRAY(1) | DNS 响应处理状态 |

### 6.4 HASH_OF_MAPS 分层结构

```
allow_out_v2 / deny_out / dns_allow
  │
  ├── key: ifindex (sandbox 标识)
  │
  └── value: inner LPM trie 的 map fd
               │
               ├── key: LPM key (prefixlen + IP 或 reversed domain)
               │
               └── value: 策略值 (net_policy_value_v2 / uint32 / dns_allow_value)
```

每个 sandbox 拥有独立的 inner map,实现 sandbox 间网络策略隔离。

---

## 7. 会话回收机制

### 7.1 Session Reaper

来源: `cubevs/reaper.go:234-401`

```go
func StartSessionReaper() <-chan Event {
    // 启动后台 goroutine,每 5 秒扫描一次
    go doReap()  // 每 5 秒: reapSessions() + reapDNSState()
}
```

**会话超时**:

| 协议 | 状态 | 超时 | Go 常量 |
|------|------|------|---------|
| TCP | SYN_SENT | 1 min | `tcpTimeouts[tcpCTSynSent]` |
| TCP | ESTABLISHED | **3 hours** | `tcpTimeouts[tcpCTEstablished]` |
| TCP | FIN_WAIT | 2 min | `tcpTimeouts[tcpCTFinWait]` |
| TCP | CLOSE_WAIT | 1 min | `tcpTimeouts[tcpCTCloseWait]` |
| TCP | LAST_ACK | 30 s | `tcpTimeouts[tcpCTLastAck]` |
| TCP | TIME_WAIT | 2 min | `tcpTimeouts[tcpCTTimeWait]` |
| TCP | CLOSE | 10 s | `tcpTimeouts[tcpCTClose]` |
| UDP | UNREPLIED | 30 s | `udpTimeouts[udpCTUnreplied]` |
| UDP | REPLIED | 180 s | `udpTimeouts[udpCTReplied]` |
| ICMP | 任意 | 30 s | `icmpTimeout` |

**高水位告警**: `reaper.go:269-276` — 当会话数超过 `maxSessions * 0.8` (838,860) 时通过 channel 发送事件

**异常关闭检测**: `reaper.go:376-381` — 非正常终止的会话会在 event channel 中报告

### 7.2 DNS State Reaper

来源: `cubevs/dns_reaper.go:11-121`

- `reapDNSLearnedPolicies()`: 扫描 `allow_out_v2` 所有 inner map,删除已过期 DNS 学习条目
- `reapDNSQueryTrack()`: 扫描 `dns_query_track` map,删除超时未收到响应的 DNS 查询跟踪

---

## 8. 关键文件清单

| 模块 | 路径 | 关键内容 |
|------|------|----------|
| **核心类型** | `cubevs/cubevs.go:17-40` | Params 结构 + TAPDevice + 常量 + 静态断言 |
| **TC 管理** | `cubevs/tc.go:16-75` | createQdisc + attachFilter |
| **TAP 管理** | `cubevs/tap.go:46-161` | AddTAPDevice/UpsertTAPDevice/DelTAPDevice |
| **端口映射** | `cubevs/port.go:11-145` | AddPortMapping/DelPortMapping/ListPortMapping |
| **出站策略** | `cubevs/netpolicy.go:1-687` | allow_out_v2/deny_out LPM trie 完整实现 |
| **DNS 策略** | `cubevs/dnspolicy.go:1-228` | dns_allow 域名策略 + 反转 LPM 编码 |
| **SNAT IP** | `cubevs/snat.go:35-85` | SetSNATIPs + max 4 个 SNAT IP |
| **初始化** | `cubevs/miscs.go:34-304` | BPF 加载 + TC 挂载 + DNS tail-call 管理 |
| **BPF map** | `cubevs/map.go:10-25` | loadPinnedMap / pinPath |
| **会话回收** | `cubevs/reaper.go:234-401` | StartSessionReaper + doReap |
| **DNS 回收** | `cubevs/dns_reaper.go:11-121` | reapDNSLearnedPolicies + reapDNSQueryTrack |
| **诊断转储** | `cubevs/dump.go:1-857` | 11 种 BPF map 的 JSON dump |
| **迁移** | `cubevs/migration.go:17-101` | allow_out v1 → v2 迁移 |
| **诊断 CLI** | `cubevs/cmd/cubevsmapdump/main.go` | 命令行 JSON dump 工具 |
| **MVM TAP BPF** | `src/mvmtap.bpf.c:927-1067` | from_cube: SNAT + 策略 + DNS + ARP |
| **主机网卡 BPF** | `src/nodenic.bpf.c:406-429` | from_world: DNAT + 端口映射 + DNS 响应 |
| **网关 BPF** | `src/localgw.bpf.c:18-59` | from_envoy: DNAT → MVM |
| **核心头文件** | `src/cubevs.h:1-262` | 数据结构 + 常量 + 静态断言 |
| **Map 定义** | `src/map.h:1-278` | 14 个 BPF map 定义 |
| **NAT 基础** | `src/nat.h:14-68` | nat_rewrite + snat/dnat 宏 |
| **会话辅助** | `src/session.h:18-83` | lazy refresh + mark + create |
| **TCP conntrack** | `src/tcp.h:68-296` | 2x6x10 状态机 + update_session |
| **UDP conntrack** | `src/udp.h:13-29` | update_udp_session + create_udp_sessions |
| **ICMP conntrack** | `src/icmp.h:29-43` | update_icmp_session + create_icmp_sessions |
| **DNS 查询** | `src/dns_query.h:234-265` | dns_handle_query + chunked 解析 |
| **DNS 响应** | `src/dns_response.h:200-249` | dns_handle_response + A 记录学习 |
| **DNS 解析** | `src/dns_parser.h:1-259` | DNS wire protocol 解析 |
| **L2/L3 辅助** | `src/l2l3.h:17-29` | set_mac_pair |
| **SKB 辅助** | `src/skb.h:6-106` | pull_headers + 协议特化宏 |
| **BPF 常量默认值** | `src/cubevs.h:74-105` | 所有 volatile const 默认值 |

---

## 9. 安全注意事项

### 9.1 已知风险

| # | 风险 | 位置 | 等级 | 说明 |
|---|------|------|------|------|
| **R1** | **无 IPv6 支持** | `src/cubevs.h:13` | 🟡 中 | 仅处理 `ETH_P_IP` (IPv4), `ETH_P_IPV6` 包被丢弃 (TC_ACT_OK) |
| **R2** | **最大 SNAT IP 硬编码 4** | `cubevs/snat.go:17` | 🟢 低 | `maxSNATIPs = 4`,端口耗尽时无法扩展 |
| **R3** | **策略条目上限 8192** | `cubevs/netpolicy.go:15` | 🟢 低 | `maxNetPolicyEntries = 8192`,大范围策略可能超限 |
| **R4** | **无 DNAT 端口冲突检测** | `cubevs/port.go:26-28` | 🟢 低 | `AddPortMapping` 使用 `UpdateAny`,可覆盖已有映射 |
| **R5** | **DNS 仅支持 IN A 记录** | `src/dns_parser.h:254-257` | 🟢 低 | AAAA 记录/IPv6 域名无法学习 |
| **R6** | **DNS 尾调用槽位竞争** | `cubevs/miscs.go:143-148` | 🟢 低 | 不同 BPF 对象的尾调用表独立填充,首次调用可能缺失 |
| **R7** | **eBPF 5.4 内核限制** | `src/tcp.h:462` 注释 | 🟡 中 | 5.4 内核限制: tail-call 程序不能含 bpf-to-bpf 调用 |

### 9.2 R1 详解: 无 IPv6 支持

来源: `src/mvmtap.bpf.c:947-951`, `src/nodenic.bpf.c:412-413`, `src/localgw.bpf.c:26-27`

所有 eBPF 程序入口都检查 `skb->protocol != ETH_P_IP`,IPv6 包 (protocol `0x86DD`) 统一返回 `TC_ACT_OK` (放行) 或 `TC_ACT_SHOT` (丢弃,取决于具体程序):
- `from_cube`: IPv6 → `TC_ACT_SHOT` (丢弃)
- `from_world`: IPv6 → `TC_ACT_OK` (直接放行,不经过 NAT)
- `from_envoy`: IPv6 → `TC_ACT_OK` (直接放行)

这意味着 IPv6 流量要么被丢弃,要么绕过 NAT 和策略检查。

### 9.3 R7 详解: 5.4 内核的 verifier 限制

来源: `src/dns_query.h` + `src/dns_response.h` 中的多处注释

在 Linux 5.4 内核上:
1. **tail-call 程序不能含 bpf-to-bpf 调用** (subprog calls):
   - 因此 DNS 尾调用路径中的程序 (`dns_parse_chunk`, `dns_rev_chunk`, `dns_finish`, `dns_handle_response_prog`, `dns_response_finish_prog`) 都使用 `__always_inline` 展开
2. **from_cube 不能含 bpf-to-bpf 调用**:
   - `do_udp_nat_inline` 必须内联,因为 `from_cube` 中已有 `bpf_tail_call` (通过 `dns_handle_query`)
   - `do_udp_nat` 则是非内联版本 (给 `dns_finish` 使用,后者不含 tail-call)
3. **from_world 的 DNS 响应处理需要尾调用分拆**:
   - 完整 DNS 响应处理 + UDP NAT 在 from_world 中超出 verifier 1M 指令限制
   - 解决方案: 拆分为 `dns_handle_response_prog` + `dns_response_finish_prog` 两段尾调用

### 9.4 默认值安全风险

来源: `src/cubevs.h:74-105`

eBPF 程序包含编译期默认值,但会在加载时被 Go 侧通过 `rewriteConstants` 重写 (`cubevs/miscs.go:34-66`)。如果用户态未正确配置 `Params`,将使用 eBPF 内的默认值:

```c
const volatile __u32 mvm_inner_ip       = 0x0644fea9;   /* 169.254.68.6 */
const volatile __u32 mvm_gateway_ip     = 0x0544fea9;   /* 169.254.68.5 */
const volatile __u32 cubegw0_ip         = 0x017100cb;   /* 203.0.113.1 */
const volatile __u32 nodenic_ifindex    = 2;             /* ifindex 2 */
```

这些默认值适合测试环境,生产部署时必须由上层正确配置。

### 9.5 DNS 学习的安全边界

- 仅学习 IN A 记录 (非 A 记录/非 IN class 被忽略)
- 学习条目的 `expires_at_ns` 继承 DNS TTL,过期后自动删除
- 不会降级 (downgrade) 已有静态 allow 规则 (`dns_response.h:118-123`)
- 不会覆盖已有静态规则的 `flags` (OR 合并)
- 查询跟踪 TTL 仅 10 秒 (`DNS_QUERY_TRACK_TTL_NS`),防重放
- 跟踪条目在响应处理后立即删除 (`dns_response.h:248`)

### 9.6 go-tc 版本兼容

来源: `cubevs/tc.go`

使用 `github.com/florianl/go-tc v0.4.1` 管理 TC qdisc 和 filter:
- Qdisc: `clsact` 类型,提供 ingress/egress 挂载点
- Filter: `bpf` 类型,`direct-action` 模式
- `Filter().Replace()` 原子替换,避免 filter 重复

---

## 10. 与 SVG 边界模型的关系

CubeNet 是 SVG 中 **T4 (Network datapath)** 的关键执行点:

| SVG 边界 | CubeNet 中的对应 |
|----------|------------------|
| **T4** (Network datapath) | 整个 CubeNet eBPF 数据面 |
| **L4** (host 内核域) | eBPF TC 程序 (from_cube / from_world / from_envoy) |
| **L5** (host 用户态域) | cubevs Go 库 + cubevsmapdump CLI |
| **L6** (网络域) | SNAT/DNAT 转换 + LPM 策略执行 + DNS 学习 |
| **T3** (Agent Control) | network-agent 通过 cubevs API 编排策略 |

详细边界视角见 [security-boundaries/T4-cubenet-datapath.md](../安全边界/T4-cubenet-datapath.md)。

---

## 11. 总结: 安全设计权衡

1. **内核态 eBPF 执行**: 高性能 (零用户态上下文切换),防用户态旁路 (即使进程崩溃,eBPF 程序持续运行)。代价是调试困难 (需 bpftool + 内核日志) 且受内核 verifier 限制。

2. **有状态 NAT + 防火墙**: TCP/UDP/ICMP 全协议支持,完整的 TCP conntrack 状态机 (10 状态)。会话回收机制防止 map 无限增长。代价是内存开销 (每个会话 64B,最大 1M 会话约 64MB)。

3. **DNS 感知策略 (最小权限)**: 域名级控制,自动将 DNS 解析 IP 动态加入允许列表,TTL 到期自动清理。代价是仅支持 IPv4 A 记录,不支持 IPv6 AAAA 和 CNAME 展开。

4. **硬编码私有地址拒绝**: SSRF 防护最后防线,始终附加到 deny_out,用户无法覆盖。代价是如果真正的用例需要访问某些内网服务,需要手动修改代码。

5. **L7 代理重定向**: 策略标记 (`L7_REQUIRED`) 将 HTTP/HTTPS 流量重定向到 cubegw0 (Envoy) 做深度检查。代价是仅支持 port 80/443,其他端口直接通过。

6. **HASH_OF_MAPS 分层隔离**: 每个 sandbox 独立的内层 LPM trie map,避免 sandbox 间策略干扰。代价是 map 嵌套增加 eBPF 代码复杂度。

7. **双端静态断言**: Go + C 编译时校验 struct ABI 一致性,防止跨语言数据结构不匹配导致的严重安全问题 (如 map 读写越界)。代价是每次修改数据结构的需要同步更新两边的断言。

8. **5.4 内核兼容的尾调用设计**: DNS 处理拆分为 5 段尾调用,绕过 verifier 1M 指令限制。代价是代码复杂度显著增加 (需要 extra `__always_inline` + `__noinline` 标记)。

---

## 12. 学习路线建议

| Phase | 重点研读章节 |
|-------|-------------|
| **Phase 0** (架构总览) | §2 架构 + §4 安全机制 + §6 BPF map 模型 |
| **Phase 1** (出站流量) | §3.1 出站处理 + `src/mvmtap.bpf.c:927-1067` |
| **Phase 2** (入站流量) | §3.2 入站处理 + `src/nodenic.bpf.c:406-429` |
| **Phase 3** (DNS 策略) | §3.3 DNS + `src/dns_query.h` + `src/dns_response.h` |
| **Phase 4** (L7 代理) | §3.4 L7 重定向 + `src/localgw.bpf.c` |
| **Phase 5** (连接跟踪) | §7 会话回收 + `src/tcp.h` + `src/session.h` |
| **Phase 6** (用户态管理) | `cubevs/miscs.go` Init + `cubevs/netpolicy.go` 策略编排 |
