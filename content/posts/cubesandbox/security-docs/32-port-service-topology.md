---
title: "4.8 端口与服务拓扑"
date: 2026-07-04T14:34:09+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/32-port-service-topology/"
summary: "CubeSandbox 各组件在 host 上分配明确端口,互不冲突:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 4.8 端口与服务拓扑

## 机制原理

CubeSandbox 各组件在 host 上分配明确端口,**互不冲突**:

| 组件 | 端口 | 用途 | 证据 |
|------|------|------|------|
| CubeAPI | **3000** | 公开 HTTP gateway | `CubeAPI/src/config/mod.rs:30-32` `pub bind: String` 默认 `"0.0.0.0:3000"` |
| CubeMaster | **8089** | 内部 control plane HTTP | `CubeMaster/conf.yaml:2-5` `http_port: 8089` |
| MySQL | **3306** | 元数据库 | 默认外部 mysql |
| Redis | **6379** | quota / session | 默认外部 redis |

```rust
// CubeAPI/src/config/mod.rs:30-32
#[serde(default = "default_bind")]
pub bind: String,
// default: "0.0.0.0:3000"
```

```yaml
# CubeMaster/conf.yaml:2-5
server:
  http_port: 8089
  grpc_port: 9090   # control plane gRPC
```

端口空间规划还体现在 3.4.1 网络隔离:

```text
10000-19999    # 本机临时端口
20000-29999    # CubeProxy 入站
30000-65535    # SNAT 出站
```

## 为什么 CubeSandbox 这样设计

- **端口固定好做防火墙规则** —— 安全团队能在边界路由器上只放行 3000(CubeAPI)/ 8089(CubeMaster)
- **依赖简洁** —— 没有复杂的反向代理,NAT 直通
- **生产可改**:bind / http_port 都是字符串,自定义就行
- **端口空间划分**避免 host network 端口混乱

## 如何使用 / 配置

#### 修改默认端口

```bash
# 改 CubeAPI 到 38000
./cube-api --bind 0.0.0.0:38000

# 改 CubeMaster http_port
cat >> CubeMaster/conf.yaml <<EOF
server:
  http_port: 38089
EOF
```

#### 防火墙

```bash
# 公共 CubeAPI(3000)对 client 开放
firewall-cmd --permanent --add-port=3000/tcp

# 内部 CubeMaster(8089)只允许内部子网
firewall-cmd --permanent --zone=internal --add-port=8089/tcp
firewall-cmd --permanent --zone=internal --add-source=10.0.0.0/8

# 永久拒绝 mysql / redis 对外
firewall-cmd --permanent --remove-port=3306/tcp
firewall-cmd --permanent --remove-port=6379/tcp
```

#### 内部服务互调

```bash
# CubeMaster → MySQL
mysql -h 127.0.0.1 -P 3306 -u cube -p

# Cubelet → CubeMaster(grpc/9090)
```

#### 端口空间策略

| 用途 | 范围 | 保留 |
|------|------|------|
| System ports | < 1024 | root only |
| Host server | 1024-9999 | docker / apps |
| CubeAPI 公开 | 3000(default) | sandbox 流量 |
| CubeProxy 入站 | 20000-29999 | 内部 sidecar |
| SNAT 出站 | 30000-65535 | egress |
| ephemeral | 10000-19999 | 短连接 |

#### 查看当前监听

```bash
ss -tlnp | grep -E '(cube|mysql|redis)'
# LISTEN 0 128 *:3000 *:* users:(("cube-api",pid=...,fd=...))
# LISTEN 0 128 *:8089 *:* users:(("cube-master",pid=...,fd=...))
# LISTEN 0 128 127.0.0.1:3306 *:* users:(("mysqld",pid=...,fd=...))
# LISTEN 0 128 127.0.0.1:6379 *:* users:(("redis-server",pid=...,fd=...))
```

**注意**:

- **不要在 0.0.0.0 上 listen** 内部服务(mysql / redis / cube-master)—— bind `127.0.0.1` 或 LAN 内部 IP
- 端口冲突:`cube-api=3000` 与 node 上其它 framework 可能撞,**生产上建议改**
- K8s service 用 NodePort 时务必 port > 30000,避开 SNAT 出站范围
- **gRPC 端口 9090** 默认开,config 中可关
- 对外 3000 务必 TLS(nginx / envoy 前终止)
- 不要在生产里 bind 0.0.0.0 CubeMaster control plane —— 它通常不应被外部看到
