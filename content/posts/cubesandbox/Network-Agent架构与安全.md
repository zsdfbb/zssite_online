---
title: "Network-Agent 架构、处理流程与安全配置"
date: 2026-07-08T00:00:00+08:00
draft: false
tags: ["cubesandbox"]
categories: ["cubesandbox"]
comments: true
showToc: true
weight: 100
---


> 调研时间: 2026/07/08
> 调研范围: `/home/zs/Develop/TraceCubeSandbox/network-agent/` 全量 Go 源码
> 目的: 系统性梳理 network-agent (节点网络编排代理) 的架构、处理流程与安全配置
> 配套文档: 暂无独立安全边界文档 (进程边界隶属于 Cubelet 节点域)

---

## 1. 概述

network-agent 是 CubeSandbox 的**节点级网络编排组件**,从 Cubelet 中拆分出的独立守护进程,负责管理计算节点上沙箱的 TAP 设备、IP 分配、端口代理和 eBPF 集成。

| 属性 | 值 |
|------|-----|
| **语言** | Go 1.24.8 |
| **API 协议** | HTTP + gRPC + Unix socket (SCM_RIGHTS) |
| **健康检查** | 127.0.0.1:19090 |
| **IPAM CIDR** | 192.168.0.0/18 (默认) |
| **端口范围** | 20000-29999 |
| **状态存储** | JSON 文件持久化 |
| **依赖** | cubevs (CubeNet eBPF 库), cubelog, CubeEgress |
| **通信** | localhost loopback (Unix socket + TCP) |

**核心职责**:
- TAP 设备生命周期管理 (创建/池化/回收/销毁)
- IP 地址管理 (CIDR 池分配/释放)
- 主机端口代理 (用户态 TCP 代理,SO_BINDTODEVICE)
- eBPF 集成 (通过 cubevs 注册 TAP + port mapping)
- 状态持久化与崩溃恢复 (三源比对)
- L7 策略推送 (CubeEgress 集成)
- TAP 文件描述符传递 (SCM_RIGHTS → Cubelet)
- 路由感知出口 (cube-router,iptables MASQUERADE)

---

## 2. 架构

### 2.1 目录结构

```
network-agent/
├── cmd/network-agent/
│   ├── main.go                   # 入口 + flag + 信号处理
│   └── logging.go                # 日志初始化
├── api/v1/
│   ├── network_agent.proto       # gRPC API 定义
│   ├── network_agent.pb.go       # 生成的 Go 类型
│   └── network_agent_grpc.pb.go  # 生成的 gRPC 桩
├── internal/
│   ├── service/
│   │   ├── service.go            # Service 接口 + noop 实现
│   │   ├── local_service.go      # 主实现 (Ensure/Release/Reconcile)
│   │   ├── config.go             # 配置加载 (TOML + CLI override)
│   │   ├── types.go              # 请求/响应/PortMapping/EgressRule 类型
│   │   ├── netdevice.go          # TAP 设备管理 (newTap/restoreTap/destroyTap)
│   │   ├── ipam.go               # IP 位图分配器
│   │   ├── port_allocator.go     # 端口分配器 (20000-29999)
│   │   ├── tap_lifecycle.go      # TAP 池 + 后台维护 + 异常恢复
│   │   ├── tap_fd_provider.go    # SCM_RIGHTS 文件描述符提供
│   │   ├── hostproxy.go          # TCP 主机代理 (SO_BINDTODEVICE)
│   │   ├── state_store.go        # JSON 状态持久化
│   │   ├── cube_router.go        # 路由感知出口 (iptables MASQUERADE)
│   │   ├── cubeegress_push.go    # CubeEgress 策略推送 (L7)
│   │   └── ethtool.go            # 网卡 offload 配置
│   ├── httpserver/               # HTTP REST API server
│   ├── grpcserver/               # gRPC server
│   ├── fdserver/                 # SCM_RIGHTS Unix socket server
│   └── cubeegress/               # CubeEgress admin API 客户端
├── pkg/version/                  # 版本注入
└── docs/                         # 架构/API/配置文档
```

### 2.2 模块分层

```
┌──────────────────────────────────────────────────────────┐
│  API 层                                                    │
│    HTTP server (/v1/network/ensure/release/...)           │
│    gRPC server (NetworkAgent service)                     │
│    FD server (SCM_RIGHTS TAP fd 传递)                     │
├──────────────────────────────────────────────────────────┤
│  服务层 (local_service.go)                                 │
│    EnsureNetwork — 创建 TAP + IP + 端口 + eBPF            │
│    ReleaseNetwork — 清理 TAP + IP + 端口 + eBPF           │
│    Reconcile — 状态恢复 + eBPF 策略同步                    │
├──────────────────────────────────────────────────────────┤
│  资源管理层                                                │
│    netdevice.go — TAP 创建/销毁/恢复/列表                  │
│    ipam.go — IPv4 位图分配/释放/Assign                    │
│    port_allocator.go — 端口分配 (20000-29999)             │
│    tap_lifecycle.go — TAP 池 + 后台维护循环                │
├──────────────────────────────────────────────────────────┤
│  集成层                                                    │
│    cubevs (eBPF map) — TAP/端口映射注册                   │
│    cubeegress_push.go — L7 策略推送/删除/重试              │
│    cube_router.go — iptables MASQUERADE + 路由管理         │
│    hostproxy.go — SO_BINDTODEVICE 用户态代理               │
└──────────────────────────────────────────────────────────┘
```

### 2.3 交互关系

```
┌──────────────┐   EnsureNetwork     ┌──────────────────┐
│   Cubelet    │ ──────────────────▶│  network-agent    │
│  (节点代理)   │   ReleaseNetwork    │  (Go 守护进程)    │
│              │ ◀──────────────────│                   │
│              │   SCM_RIGHTS (TAP fd)                   │
│              │ ◀──────────────────│                   │
└──────────────┘                    └───────┬───────────┘
          ▲                                 │
          │                           cubevs │ (eBPF map)
          │                                 ▼
          │                          ┌──────────────┐
          │                          │   CubeNet     │
          │                          │  (eBPF 数据面) │
          │                          └──────────────┘
          │
          │ 127.0.0.1:9090
          ▼
   ┌──────────────┐
   │  CubeEgress  │
   │ (L7 策略执行) │
   └──────────────┘
```

network-agent 是**纯节点级组件**,运行在每个计算节点上,通过 loopback 与同节点的 Cubelet、CubeEgress 通信,通过 cubevs 库与内核 eBPF 数据面交互。

---

## 3. 处理流程

### 3.1 EnsureNetwork (创建沙箱网络)

来源: `network-agent/internal/service/local_service.go:398-454`

```
Cubelet                    network-agent                       内核/eBPF
  │                              │                              │
  │ EnsureNetwork(sandbox_id)    │                              │
  │ ────────────────────────────▶│                              │
  │                              │                              │
  │                              │ ① local_service.go:461-482   │
  │                              │   acquireTap()                │
  │                              │   → 优先从 TAP 池取           │
  │                              │   → 池空则新创建 (newTap)     │
  │                              │   → ipam.Allocate()           │
  │                              │   → netlink.LinkAdd(TAP)      │
  │                              │   → SetMTU / SetUp            │
  │                              │   → cubevs.AttachFilter       │
  │                              │   → addARPEntry               │
  │                              │ ──────────────────────────▶ │ netlink
  │                              │                              │
  │                              │ ② local_service.go:97-127    │
  │                              │   configurePortMappings()     │
  │                              │   → portAllocator.Allocate()  │
  │                              │   → cubevs.AddPortMapping     │
  │                              │ ──────────────────────────▶ │ eBPF
  │                              │                              │
  │                              │ ③ local_service.go:950-964   │
  │                              │   registerCubeVSTap()         │
  │                              │   → cubevs.AddTAPDevice()     │
  │                              │   → 写入 eBPF map             │
  │                              │ ──────────────────────────▶ │ eBPF
  │                              │                              │
  │                              │ ④ state_store.go:109-122     │
  │                              │   → JSON 持久化到磁盘         │
  │                              │                              │
  │                              │ ⑤ cubeegress_push.go:117-148 │
  │                              │   pushEgressForState()        │
  │                              │   → PUT /admin/v1/policies    │
  │                              │   → best-effort (不阻塞创建)  │
  │                              │                              │
  │ ◀───────────────────────────│                              │
  │ TAP fd + IP + 端口           │                              │
```

**关键文件位置**:
- 入口: `network-agent/internal/service/local_service.go:219-280` (EnsureNetwork 方法)
- 创建: `network-agent/internal/service/local_service.go:398-454` (createState)
- 获取 TAP: `network-agent/internal/service/local_service.go:461-482` (acquireTap)
- TAP 创建: `network-agent/internal/service/netdevice.go:287-352` (newTap)
- 端口配置: `network-agent/internal/service/tap_lifecycle.go:97-127` (configurePortMappings)

### 3.2 ReleaseNetwork (释放沙箱网络)

来源: `network-agent/internal/service/local_service.go:282-309`

```
Cubelet                    network-agent
  │                              │
  │ ReleaseNetwork(sandbox_id)   │
  │ ────────────────────────────▶│
  │                              │
  │                              │ ① local_service.go:587-617
  │                              │   releaseState()
  │                              │   → 关闭 hostProxy (hostproxy.go)
  │                              │   → clearPortMappings()
  │                              │   → cubevs.DelPortMapping
  │                              │   → cubevs.DelTAPDevice
  │                              │   → recycleTapLocked (回池)
  │                              │   → store.Delete (删除 JSON)
  │                              │
  │                              │ ② cubeegress_push.go:155-163
  │                              │   deleteEgressForState()
  │                              │   → DELETE /admin/v1/policies
  │                              │   → best-effort (不阻塞释放)
  │                              │
  │ ◀───────────────────────────│
  │ Released: true               │
```

### 3.3 崩溃恢复 (Reconcile)

来源: `network-agent/internal/service/local_service.go:619-729`

```
重启后:
  │ ① local_service.go:619-729 recover()
  │    ├── store.LoadAll()       → 读取 JSON 状态
  │    ├── listCubeTaps()         → 列出内核 TAP 设备
  │    ├── cubevs.ListTAPDevices → 列出 eBPF map 条目
  │    └── cubevs.ListPortMapping → 列出 eBPF 端口映射
  │
  │ ② 三源比对:
  │    磁盘状态    内核 TAP    eBPF map    处理
  │    ─────────────────────────────────────────────
  │    有          有          有          正常,保留
  │    有          无          有/无       清理 eBPF + 磁盘
  │    无          有          有          构建恢复状态 + 写磁盘
  │    无          有          无          回收入池
  │
  │ ③ 后台维护循环 (tap_lifecycle.go:212-225)
  │    ├── handleAbnormalTaps()  → 异常 TAP 恢复 (最多 3 次)
  │    ├── retryPendingEgressPushes() → 重试失败策略推送
  │    └── warmupTapPoolBackground() → 后台预热新 TAP
```

### 3.4 TAP 文件描述符传递 (FD Server)

来源: `network-agent/internal/fdserver/server.go:1-131`

```
Cubelet                    network-agent
  │                              │
  │ Unix socket connect          │
  │ ────────────────────────────▶│
  │ {"sandboxId":"xxx","name":""}│
  │ ────────────────────────────▶│
  │                              │
  │                              │ tap_fd_provider.go:23-99
  │                              │ GetTapFile()
  │                              │ → 查 managedState
  │                              │ → 返回 cached fd 或 restoreTap
  │                              │
  │ ◀───────────────────────────│
  │ {"errCode":"0","ifindex":N}  │
  │ + SCM_RIGHTS (TAP fd)        │
```

---

## 4. 路由与端点

### 4.1 HTTP API

来源: `network-agent/internal/httpserver/server.go:32-128`

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | /v1/network/ensure | 创建沙箱网络 |
| POST | /v1/network/release | 释放沙箱网络 |
| POST | /v1/network/reconcile | 恢复/同步状态 |
| POST | /v1/network/get | 查询沙箱网络 |
| POST | /v1/network/list | 列出所有沙箱网络 |
| GET | /v1/policies/dump | 批量策略导出 (CubeEgress 引导) |
| GET | /healthz | 健康检查 (200 OK) |
| GET | /readyz | 就绪检查 (200 ok) |

### 4.2 gRPC API

来源: `network-agent/api/v1/network_agent.proto:11-29`

```protobuf
service NetworkAgent {
    rpc EnsureNetwork(EnsureNetworkRequest) returns (EnsureNetworkResponse);
    rpc ReleaseNetwork(ReleaseNetworkRequest) returns (ReleaseNetworkResponse);
    rpc ReconcileNetwork(ReconcileNetworkRequest) returns (ReconcileNetworkResponse);
    rpc GetNetwork(GetNetworkRequest) returns (GetNetworkResponse);
    rpc ListNetworks(ListNetworksRequest) returns (ListNetworksResponse);
    rpc Health(HealthRequest) returns (HealthResponse);
}
```

### 4.3 FD Server (Unix socket)

- 端点: `unix:///tmp/cube/network-agent-tap.sock` (默认)
- 协议: JSON 请求 + SCM_RIGHTS 文件描述符传递
- 请求格式: `{"sandboxId":"<id>","name":"<tapName>"}`
- 响应格式: `{"errCode":"0","errMsg":"Success","ifindex":N}` + fd

---

## 5. 安全机制

### 5.1 默认安全配置

| # | 特性 | 文件位置 | 说明 |
|---|------|----------|------|
| S1 | Unix socket 隔离 | `main.go:37-39` | HTTP/gRPC 默认监听 Unix socket,不可网络访问 |
| S2 | 健康检查绑定 loopback | `main.go:38` | 127.0.0.1:19090 |
| S3 | SO_BINDTODEVICE | `hostproxy.go:77-81` | 主机代理绑定到 sandbox 特定 TAP,防跨沙箱 |
| S4 | 沙箱 ID 路径清洗 | `state_store.go:163-164` | 拒绝 `.` `/` `\` 路径遍历字符 |
| S5 | CubeEgress IP 验证 | `cubeegress/client.go:175-189` | 拒绝 URL 敏感字符 (`/?#`),空值 |
| S6 | 4xx 永久错误不重试 | `cubeegress_push.go:137-142` | 防放大配置错误 |
| S7 | 三次失败隔离 | `tap_lifecycle.go:261-263` | TAP 恢复 3 次失败后隔离到 quarantinedTaps |
| S8 | 状态持久化 + 三源比对 | `local_service.go:619-729` | 崩溃恢复防状态不一致 |
| S9 | host-proxy-bind-ip 默认 127.0.0.1 | `config.go:73` | 代理监听仅限 loopback |
| S10 | 创建防并发竞态 | `local_service.go:240-265` | creating guard channel 防 TOCTOU 竞态 |
| S11 | 释放等待创建完成 | `local_service.go:283-289` | waitForInflightCreation 防孤儿 TAP |
| S12 | pprof 默认关闭 | `main.go:56` | `--pprof-listen` 为空时禁用 |

### 5.2 沙箱 ID 路径清洗

来源: `network-agent/internal/service/state_store.go:162-167`

```go
func (s *stateStore) path(sandboxID string) (string, error) {
    if strings.ContainsAny(sandboxID, `/\.`) || sandboxID == "" {
        return "", fmt.Errorf("invalid sandboxID %q: contains path separators or traversal characters", sandboxID)
    }
    return filepath.Join(s.dir, sandboxID+".json"), nil
}
```

拒绝 `.` `/` `\` 字符防止路径遍历攻击。

### 5.3 CubeEgress IP 验证

来源: `network-agent/internal/cubeegress/client.go:175-189`

```go
func validateSandboxIP(s string) error {
    if s == "" {
        return errors.New("cubeegress: sandbox_ip is empty")
    }
    if strings.ContainsAny(s, "/?#") {
        return fmt.Errorf("cubeegress: sandbox_ip contains URL-meaningful characters: %q", s)
    }
    if escaped := url.PathEscape(s); escaped != s {
        return fmt.Errorf("cubeegress: sandbox_ip not URL-clean: %q", s)
    }
    return nil
}
```

阻止将非法的 sandbox IP 注入到 HTTP URL 路径中。

### 5.4 SO_BINDTODEVICE 绑定

来源: `network-agent/internal/service/hostproxy.go:73-86`

```go
dialer := &net.Dialer{
    Control: func(network, address string, c syscall.RawConn) error {
        return c.Control(func(fd uintptr) {
            ctrlErr = unix.SetsockoptString(int(fd), unix.SOL_SOCKET,
                unix.SO_BINDTODEVICE, tapName)
        })
    },
}
```

每个主机代理的 TCP 连接都通过 `SO_BINDTODEVICE` 绑定到特定 TAP 设备,防止网络流量逃逸到其他沙箱的接口。

### 5.5 并发安全与竞态防护

来源: `network-agent/internal/service/local_service.go:240-265`

```go
// 注册 guard channel 在同一个临界区中
done := make(chan struct{})
s.creating[req.SandboxID] = done
s.mu.Unlock()

state, createErr := s.createState(ctx, req)

s.mu.Lock()
delete(s.creating, req.SandboxID)
if createErr == nil {
    s.states[state.SandboxID] = state
}
close(done)
```

通过 `creating` guard channel 机制确保并发创建时 `ReleaseNetwork` 不会误判"未找到"而跳过早创建的 TAP。

### 5.6 恢复重试限制

来源: `network-agent/internal/service/tap_lifecycle.go:261-263`

```go
if tap.FailureCount >= maxAbnormalRecoveryAttempts {
    s.quarantinedTaps[tap.Name] = tap
    // 隔离,不再自动恢复
}
```

`maxAbnormalRecoveryAttempts = 3`,失败 3 次后移到隔离池,不再占用恢复循环。

### 5.7 策略推送失败处理

来源: `network-agent/internal/service/cubeegress_push.go:114-148`

- **4xx 永久错误**: 记录日志,清除 pending 标志,不重试
- **5xx/传输错误**: 设置 `pendingEgressPush = true`,后台循环重试
- **无规则**: 跳过推送,清除 pending 标志

---

## 6. 配置项

### 6.1 配置结构

来源: `network-agent/internal/service/config.go:21-57`

| 配置项 | CLI flag | 默认值 | 说明 |
|--------|----------|--------|------|
| eth-name | `--eth-name` | (required) | 节点上行接口名,必须配置 |
| listen | `--listen` | `unix:///tmp/cube/network-agent.sock` | HTTP 监听端点 |
| grpc-listen | `--grpc-listen` | `unix:///tmp/cube/network-agent-grpc.sock` | gRPC 监听端点 |
| health-listen | `--health-listen` | `127.0.0.1:19090` | 健康检查地址 |
| tap-fd-listen | `--tap-fd-listen` | `unix:///tmp/cube/network-agent-tap.sock` | FD server |
| cidr | `--cidr` | `192.168.0.0/18` | IPAM CIDR |
| mvm-inner-ip | `--mvm-inner-ip` | `169.254.68.6` | 客户可见 IP |
| mvm-mac-addr | `--mvm-mac-addr` | `20:90:6f:fc:fc:fc` | 客户 MAC 地址 |
| mvm-gw-dest-ip | `--mvm-gw-dest-ip` | `169.254.68.5` | 网关目标 IP |
| mvm-gw-mac-addr | `--mvm-gw-mac-addr` | `20:90:6f:cf:cf:cf` | 网关 MAC 地址 |
| mvm-mask | `--mvm-mask` | 30 | 客户掩码 |
| mvm-mtu | `--mvm-mtu` | 1500 | 客户 MTU |
| host-proxy-bind-ip | `--host-proxy-bind-ip` | `127.0.0.1` | 代理绑定地址 |
| state-dir | `--state-dir` | `/data/cubelet/network-agent/state` | 状态存储目录 |
| cubelet-config | `--cubelet-config` | "" | Cubelet TOML 配置路径 |
| cube-egress-url | (from config) | `http://127.0.0.1:9090` | CubeEgress admin URL |
| cube-egress-push-timeout | (from config) | 2s | 策略推送超时 |
| cube-router-enable | `--cube-router-enable` | false | 路由感知出口 |
| cube-router-cidr | `--cube-router-cidr` | "" | 可选路由器 CIDR |
| cube-router-mac-addr | `--cube-router-mac-addr` | `22:90:6f:cf:cf:cf` | 路由器 MAC |
| logpath | `--logpath` | `/data/log/cubelet` | 日志路径 |
| pprof-listen | `--pprof-listen` | "" | pprof 调试端点 (默认关闭) |

### 6.2 配置加载优先级

来源: `network-agent/cmd/network-agent/main.go:76-131`

```
① DefaultConfig() 默认值
   ↓
② Cubelet TOML (--cubelet-config) → 覆盖默认值
   ↓
③ CLI flag (--eth-name, --cidr, ...) → 覆盖 TOML
   ↓ 最终配置
```

### 6.3 启动示例

```bash
# 最小启动 (必须指定 eth-name)
./network-agent --eth-name eth0

# 完整启动
./network-agent \
  --eth-name eth0 \
  --cidr 192.168.0.0/18 \
  --cubelet-config /etc/cubelet/config.toml \
  --cube-router-enable \
  --state-dir /data/cubelet/network-agent/state

# 启用 pprof 调试
./network-agent --eth-name eth0 --pprof-listen 127.0.0.1:6060
```

---

## 7. 状态存储

### 7.1 persistedState 结构

来源: `network-agent/internal/service/state_store.go:16-28`

```json
{
  "sandboxID": "sbx-xxx",
  "networkHandle": "sbx-xxx",
  "tapName": "z192.168.x.x",
  "tapIfIndex": 42,
  "sandboxIP": "192.168.x.x",
  "interfaces": [...],
  "routes": [...],
  "arpNeighbors": [...],
  "portMappings": [...],
  "cubeNetworkConfig": {...},
  "persistMetadata": {
    "sandbox_ip": "192.168.x.x",
    "host_tap_name": "z192.168.x.x",
    "mvm_inner_ip": "169.254.68.6",
    "gateway_ip": "169.254.68.5"
  }
}
```

### 7.2 向后兼容

来源: `network-agent/internal/service/state_store.go:38-96`

- 写入时双写 `cubeNetworkConfig` 和 `cubevsContext` 两个 key
- 读取时新 key 优先,旧 key 作为回退
- 支持回滚到旧二进制

---

## 8. TAP 生命周期

### 8.1 TAP 池状态流转

来源: `network-agent/internal/service/tap_lifecycle.go:36-281`

```
  ┌───────────┐  ensureTapInventory  ┌───────────┐
  │ 创建新 TAP │ ──────────────────▶│  池化 TAP  │
  │ (newTap)  │                     │ (tapPool)  │
  └───────────┘                     └─────┬─────┘
                                          │ acquireTap
                                          ▼
                                   ┌──────────────┐
                                   │  使用中 TAP   │
                                   │ (managedState)│
                                   └──────┬───────┘
                          recycleTapLocked │ releaseState
                                          ▼
                                   ┌──────────────┐
                                   │   池化 TAP    │
                                   │ (tapPool)    │
                                   └──────┬───────┘
                                          │ 恢复失败 ≥3次
                                          ▼
                                   ┌──────────────┐
                                   │   隔离 TAP     │
                                   │(quarantined)  │
                                   └──────────────┘
```

### 8.2 后台维护循环

来源: `network-agent/internal/service/tap_lifecycle.go:212-225`

- 间隔: 5 秒 (`maintenanceInterval`)
- 任务 1: `handleAbnormalTaps()` — 异常 TAP 恢复
- 任务 2: `retryPendingEgressPushes()` — 重试失败的策略推送
- 启动时机: `NewLocalService` 构造函数中通过 `go s.startMaintenanceLoop()` 启动

---

## 9. 关键文件清单

| 模块 | 路径 | 关键内容 |
|------|------|----------|
| **入口** | `network-agent/cmd/network-agent/main.go` | flag 解析 + server 启动 + 信号处理 |
| **Service 接口** | `network-agent/internal/service/service.go` | Service interface + noop 实现 |
| **主服务** | `network-agent/internal/service/local_service.go` | Ensure/Release/Reconcile/recover |
| **配置** | `network-agent/internal/service/config.go` | Config + 默认值 + TOML 加载 |
| **类型定义** | `network-agent/internal/service/types.go` | 请求/响应/EgressRule 类型 |
| **TAP 管理** | `network-agent/internal/service/netdevice.go` | newTap/restoreTap/destroyTap/listCubeTaps |
| **IPAM** | `network-agent/internal/service/ipam.go` | ipAllocator 位图分配器 |
| **端口分配** | `network-agent/internal/service/port_allocator.go` | portAllocator 20000-29999 |
| **TAP 池** | `network-agent/internal/service/tap_lifecycle.go` | 池维护 + 异常恢复 |
| **FD 传递** | `network-agent/internal/service/tap_fd_provider.go` | GetTapFile + restoreTap fallback |
| **主机代理** | `network-agent/internal/service/hostproxy.go` | TCP 用户态代理 SO_BINDTODEVICE |
| **状态存储** | `network-agent/internal/service/state_store.go` | JSON 持久化 + 路径清洗 |
| **策略推送** | `network-agent/internal/service/cubeegress_push.go` | CubeEgress PUT/DELETE/重试 |
| **路由出口** | `network-agent/internal/service/cube_router.go` | iptables MASQUERADE + 路由管理 |
| **HTTP server** | `network-agent/internal/httpserver/server.go` | REST API 路由注册 |
| **gRPC server** | `network-agent/internal/grpcserver/server.go` | gRPC 服务实现 |
| **FD server** | `network-agent/internal/fdserver/server.go` | SCM_RIGHTS Unix socket |
| **CubeEgress 客户端** | `network-agent/internal/cubeegress/client.go` | admin API 客户端 + IP 验证 |
| **Protocol Buffers** | `network-agent/api/v1/network_agent.proto` | gRPC 服务 + 消息定义 |
| **ethtool** | `network-agent/internal/service/ethtool.go` | tx-tcp-mangleid-segmentation |

---

## 10. 依赖关系

### 10.1 内部依赖

| 包 | 用途 |
|----|------|
| `github.com/tencentcloud/CubeSandbox/CubeNet/cubevs` | eBPF map 操作 (TAP/端口映射) |
| `github.com/tencentcloud/CubeSandbox/cubelog` | 结构化日志 |
| `github.com/vishvananda/netlink` | netlink 操作 (TAP/路由/ARP) |
| `golang.org/x/sys/unix` | 系统调用 (ioctl/SO_BINDTODEVICE) |
| `google.golang.org/grpc` | gRPC server + health |
| `github.com/cilium/ebpf` | eBPF 错误类型 |
| `github.com/pelletier/go-toml/v2` | Cubelet TOML 解析 |

---

## 11. 安全注意事项

| # | 风险 | 等级 | 说明 |
|---|------|------|------|
| R1 | 需要 root 权限 | 🟡 中 | TAP/netlink/eBPF 操作需要 CAP_NET_ADMIN |
| R2 | /v1/policies/dump 暴露秘密 | 🟡 中 | 但仅限 loopback 访问(CubeEgress 同机) |
| R3 | host-proxy-bind-ip 可配 0.0.0.0 | 🟠 中 | 默认 127.0.0.1 安全,但可被改造成外部可访问 |
| R4 | 无 CubeEgress 策略内容验证 | 🟢 低 | trust 内部通信,无内容消毒 |
| R5 | pprof 默认关闭可被启用 | 🟢 低 | `--pprof-listen` 若配为非 loopback 暴露调试信息 |
| R6 | IP 耗尽无降级 | 🟢 低 | `errIPExhausted` 返回错误,沙箱创建失败 |

---

## 12. 与 SVG 边界模型的关系

| SVG 边界 | network-agent 中的对应 |
|----------|----------------------|
| T3 (Node Trust) | 整个 network-agent 进程 (节点信任域) |
| L4 (host 内核域) | TAP 设备 + netlink + ARP 表 |
| L6 (网络域) | 端口代理 + eBPF map (cubevs) |
| L7 (可观测性域) | 状态持久化 + 健康检查 + pprof |

network-agent 完全运行在**节点信任域 T3** 内,不暴露任何外部网络端口(除 pprof 可选)。所有外部通信通过节点本机的 loopback Unix socket 或 TCP 进行。

---

## 13. 总结

1. **拆分自 Cubelet**: 独立守护进程,职责分离,降低 Cubelet 复杂度和重启影响面
2. **三 API 层 (HTTP/gRPC/FD)**: 按需选择通信方式,FD server 使用 SCM_RIGHTS 高效传递 TAP fd
3. **TAP 池化**: 预热 + 回收 + 异常恢复,提高创建速度,降低 P99 延迟
4. **三源恢复**: 磁盘/内核 TAP/eBPF map 完整状态比对,崩溃后自动收敛
5. **SO_BINDTODEVICE**: 每连接绑定到特定 TAP,防跨沙箱网络访问
6. **loopback 默认**: HTTP/gRPC/FD 默认仅 Unix socket 或 127.0.0.1,避免非必要的网络暴露
7. **Coplanar TOCTOU 防护**: creating guard channel 确保并发 EnsureNetwork + ReleaseNetwork 不竞态
8. **CubeEgress 集成**: L7 策略推送 + 批量导出 + 失败重试,确保沙箱网络策略一致性
9. **路由感知出口**: cube-router + iptables MASQUERADE,支持 SNAT 端口范围隔离(30000-65535)
10. **向后兼容状态格式**: 双写 cubeNetworkConfig/cubevsContext,安全版本演进
