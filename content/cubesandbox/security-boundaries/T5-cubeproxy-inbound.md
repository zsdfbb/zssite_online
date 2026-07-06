---
title: "T5 CubeProxy inbound — Internet → Sandbox"
date: 2026-07-06T23:20:49+08:00
draft: false
tags: ["cubesandbox", "security", "T5"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/boundaries/t5-cubeproxy-inbound/"
summary: "一句话定位: 公网用户通过 `*.cube.app` 通配域名进入 CubeProxy,再反向代理到 sandbox 暴露端口 的入站面。"
cover:
  image: ""
  alt: ""
  caption: ""
---
# T5 CubeProxy inbound — Internet → Sandbox

> 一句话定位: 公网用户通过 `*.cube.app` 通配域名进入 CubeProxy,再反向代理到 sandbox 暴露端口 的入站面。
> 边界类型: 真边界 (信任跃迁) —— 流量从不可信公网进入 host 信任域,落到具体 sandbox。
> SVG 位置: x=1060, y=145 (右上) ,T5 蓝框。

## 1. 边界概述

```
   ┌──────────── External Internet (Untrusted) ────────────┐
   │   公网用户 → *.cube.app 通配 DNS 解析                 │
   │   + traffic_access_token (URL 参数 / Header)          │
   └─────────────────────┬─────────────────────────────────┘
                         │ HTTPS + *.cube.app 域名 + token
                         ▼
            ╔═══════════════════════════════════╗
            ║   T5  CubeProxy inbound            ║  ← 本边界
            ║   OpenResty + Lua + Redis 元数据  ║
            ║   + AllowPublicTraffic 旧数据兼容  ║
            ╚═══════════════╤═══════════════════╝
                            │ reverse proxy → host port 20000-29999
                            ▼
                  Sandbox 暴露端口 (host:port)
```

**信任跃迁语义**: 进入 T5 前,流量是公网的"任意用户对 `*.cube.app` 的请求";穿过 CubeProxy 后,流量被绑定到具体 sandbox 的具体端口。**`traffic_access_token` 是核心鉴权凭据**,类似 API 网关的 API key,但粒度到具体 sandbox。

## 2. 涉及的纵深防御层

| 层 | 名称 | 是否参与 | 在本边界的作用 |
|----|------|---------|--------------|
| L1 | WebUI 域 | ❌ | T5 是 sandbox 入站,不经 WebUI;但共享同一 host |
| L2 | 控制面域 | ✅ | CubeProxy 注册到 Redis 元数据、CubeMaster 配置加载 |
| L3 | host 进程域 | ✅ | CubeProxy (OpenResty/Lua) 进程的 seccomp、NGINX worker sandbox |
| L4 | host 内核域 | ✅ | 入站路径 eBPF 策略、端口空间 `20000-29999` (CubeProxy 专属) |
| L5 | guest OS 域 | ❌ | T5 落到 host 端口,不直接经 guest |
| L6 | 存储域 | ✅ | nginx shared cache、Redis 元数据 (sandbox_id → host:port 映射) |
| L7 | 可观测性域 | ✅ | ingress 日志、`traffic_access_token` 校验失败计数、audit |

## 3. 机制清单

### 3.1 L2 (控制面域)

#### 机制: CubeProxy 注册到 Redis 元数据

- **文件位置**: `CubeProxy/lua/sandbox_backend.lua` (本系列新增,原清单未列) + Redis client 连接
- **作用**: sandbox 启动时,暴露端口注册到 Redis: `sandbox_id → host:port + traffic_access_token + AllowPublicTraffic`
- **配置/启用**: 自动 (sandbox 创建时由 CubeMaster 注册)
- **与本边界的关联**: T5 上 CubeProxy 路由决策的数据源

#### 机制: CubeMaster 配置加载 (conf.yaml 中的 ingress)

- **文件位置**: `CubeMaster/conf.yaml` (本系列新增,原清单未列)
- **作用**: CubeProxy 的上游地址、`*.cube.app` 域名配置、CubeMaster → CubeProxy 注册协议
- **配置/启用**: 运维修改 (T2)
- **与本边界的关联**: T5 的入口配置由 T2 注入

### 3.2 L3 (host 进程域)

#### 机制: CubeProxy (OpenResty/Lua) 进程 seccomp

- **文件位置**: `CubeProxy/Dockerfile` (OpenResty 默认 seccomp profile) (本系列新增,原清单未列)
- **作用**: OpenResty worker 进程受 seccomp 保护,只允许 HTTP 处理相关 syscall
- **配置/启用**: 编译期 OpenResty 默认配置
- **与本边界的关联**: T5 中反向代理的进程沙箱

#### 机制: NGINX worker sandbox (chroot / seccomp)

- **文件位置**: `CubeProxy/lua/sandbox_backend.lua` (本系列新增,原清单未列) + OpenResty worker 配置
- **作用**: NGINX worker 在自己的 chroot 内运行,即使 RCE 也无法访问 host 文件系统
- **配置/启用**: 启动时配置
- **与本边界的关联**: T5 的进程级隔离

### 3.3 L4 (host 内核域)

#### 机制: 入站路径 eBPF policy

- **文件位置**: `CubeNet/cubevs/miscs.go` (eBPF from_world 程序) ;`docs/architecture/network.md:30-100`
- **作用**: eBPF `from_world` 程序拦截入站流量,在 `20000-29999` 端口范围内做协议校验
- **配置/启用**: 启动时加载 BPF
- **与本边界的关联**: T5 上入站流量的 L4 拦截

#### 机制: 端口空间 `20000-29999` (CubeProxy 专属)

- **文件位置**: `docs/architecture/network.md` (端口空间划分)
- **作用**: 主机端口空间 `20000-29999` 是 CubeProxy 入站专属,与其他服务隔离
- **配置/启用**: eBPF 策略
- **与本边界的关联**: T5 上入站流量的端口范围

#### 机制: `*.cube.app` 通配 DNS

- **文件位置**: `CubeProxy/lua/sandbox_backend.lua` (本系列新增,原清单未列) + 部署 DNS 配置
- **作用**: `*.cube.app` 通配域名解析到 CubeProxy 入口 IP,所有 sandbox 共享同一域名后缀,通过 subdomain 区分
- **配置/启用**: 由部署 DNS 提供 (运维配置,T2)
- **与本边界的关联**: T5 的入口域名

### 3.4 L6 (存储域)

#### 机制: nginx shared cache

- **文件位置**: `CubeEgress/nginx.conf` (本系列新增,原清单未列,与 T4 共享 OpenResty 配置) 或 `CubeProxy/nginx.conf`
- **作用**: nginx shared cache 缓存反向代理响应,降低上游 sandbox 压力
- **配置/启用**: 启动时配置
- **与本边界的关联**: T5 上游 sandbox 的响应缓存

#### 机制: Redis 元数据 (sandbox_id → host:port 映射)

- **文件位置**: `CubeProxy/lua/sandbox_backend.lua:164-171` (本系列新增,原清单未列) + Redis TTL
- **作用**: Redis 存储 `sandbox_id → {host_port, traffic_access_token, AllowPublicTraffic, expires_at}`,sandbox 删除时自动清理
- **配置/启用**: 自动
- **与本边界的关联**: T5 的核心数据源

### 3.5 L7 (可观测性域)

#### 机制: ingress 访问日志

- **文件位置**: `CubeEgress/nginx.conf` (access_log) (本系列新增,原清单未列)
- **作用**: 记录所有入站请求: 源 IP、SNI、URL、token 校验结果、响应码、上游 sandbox_id
- **配置/启用**: 默认开启
- **与本边界的关联**: T5 的可观测性落点

#### 机制: `traffic_access_token` 校验失败计数

- **文件位置**: `CubeProxy/lua/sandbox_backend.lua` (token 校验逻辑) (本系列新增,原清单未列)
- **作用**: token 缺失 / 无效 / 过期均计数,可触发 alert
- **配置/启用**: 默认开启
- **与本边界的关联**: T5 的鉴权失败指标

#### 机制: AllowPublicTraffic 旧数据兼容 (审计)

- **文件位置**: `CubeProxy/lua/sandbox_backend.lua:164-171`
- **作用**: 旧数据 sandbox 默认 `AllowPublicTraffic=true` (即无 token 校验也放行),新数据默认 `false`;**当前缺失迁移脚本**把旧数据改成 `false`
- **配置/启用**: 旧数据自动启用,新数据需显式设置
- **与本边界的关联**: T5 上"**已知实现缺陷 C7**"所在——SVG 在 T5 框内明确标 ⚠️

## 4. 关键交互

- **数据流入自**: External Internet (公网)
- **数据流出到**:
  - **sandbox 暴露端口** (host `20000-29999` 端口) —— sandbox 内进程监听该端口提供服务
  - **T2 (Operator Trust)**: 运维修改 `*.cube.app` DNS 或 CubeProxy 配置
  - **L7**: 审计日志落 `/data/log/`
- **同信任域 L 层依赖**: L3 (CubeProxy 进程) → L2 (Redis 元数据查询) → L4 (eBPF 入站拦截) → L6 (缓存/元数据) → L7 (日志)

## 5. 设计权衡

1. **为什么 `*.cube.app` 通配域名**: 单 sandbox 共享同一域名后缀,通过 subdomain 区分;运维无需为每个 sandbox 配 DNS,A 记录指向 CubeProxy 入口 IP,subdomain 在 CubeProxy 层路由决策。代价是 DNS 配置相对复杂,但运维只在 T2 做一次。
2. **为什么用 `traffic_access_token` 而不是 session**: 这是 sandbox 级别的鉴权,sandbox 生命周期短 (分钟到小时) ,session 太重。token 可由 sandbox 创建者 (经 T1 调 CubeAPI) 注入,粒度到具体 sandbox + 具体端口。
3. **为什么端口空间 `20000-29999`**: 与出站 `30000-65535` 隔离,运维可独立管理 iptables/eBPF 规则;与本机临时端口 `10000-19999` 隔离,避免冲突。端口范围是 L4 的硬性约束。
4. **为什么 AllowPublicTraffic 默认 true (旧数据)**: 这是**已知实现缺陷 C7**——早期版本 sandbox 默认 public,新版本才改为默认 false。SVG 在 T5 框内明确标 ⚠️,**当前缺失**:迁移脚本把旧数据改成 false。这是有意识的"向后兼容 vs 安全"权衡——拒绝旧数据会让早期用户全断,但保留旧数据让公网可访问所有未迁移 sandbox。
5. **为什么 T5 不直接经 T3**: T5 落到 host 端口后,流量经 host 网络栈进入 sandbox 暴露端口,而非直接进入 T3 KVM 边界。这意味着 T5 是 host 信任域内的入站,**只有 L4 eBPF 在 T5 入站处做 L4 拦截**,T3 不参与。这是有意为之——T5 入站流量已经过 `traffic_access_token` 鉴权,不需要再过 KVM 隔离 (KVM 隔离解决的是"guest 内进程逃逸"问题,不是"入站鉴权"问题)。
6. **为什么 nginx shared cache**: 反向代理到 sandbox 后,sandbox 频繁重启,缓存响应可让客户端在 sandbox 重启窗口期仍能拿到响应。这是**可用性>一致性**的权衡——如果 sandbox 状态已变化,客户端可能拿到旧数据;但对低一致性场景 (例如 web demo) 这是可接受的。
7. **为什么 T5 与 T4 共享 OpenResty 进程**: 出入走同一 OpenResty,L7 配置统一管理,运维只需改一份 nginx.conf + Lua。代价是 L3 进程沙箱共享——若 OpenResty 被攻破,出入双向都被影响。SVG 在 Cross-cutting 面板中提到"guest-to-guest 经 host"风险,这里隐含了"egress/ingress 经 host"也类似。