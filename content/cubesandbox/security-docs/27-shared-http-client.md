---
title: "4.3 共享 HTTP 客户端(连接池上限)"
date: 2026-07-04T14:32:26+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/27-shared-http-client/"
summary: "CubeAPI 启动时创建一个全局共享的 reqwest::Client 实例,所有外部 HTTP 调用(auth callback、webhook、镜像拉取)都通过它发出。"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 4.3 共享 HTTP 客户端(连接池上限)

## 机制原理

CubeAPI 启动时创建一个**全局共享**的 `reqwest::Client` 实例,所有外部 HTTP 调用(auth callback、webhook、镜像拉取)都通过它发出。

```rust
// CubeAPI/src/state.rs:50-55
http_client = reqwest::Client::builder()
    .pool_max_idle_per_host(100)
    .timeout(Duration::from_secs(30))
    .tcp_keepalive(Duration::from_secs(60))
    .connect_timeout(Duration::from_secs(10))
    .build()
    .expect("http client init"),
```

关键参数:`pool_max_idle_per_host=100` —— 每个 host 上最多保留 100 个空闲连接。

## 为什么 CubeSandbox 这样设计

- **避免 fd 耗尽** —— 没有连接池上限,1k 个 sandbox 同时回调 auth callback 可能创建 1k+ 连接,吃光 host 的 fd 上限
- **复用连接减少握手** —— TLS 握手是 ~50ms,复用已经握过的连接降低延迟
- **统一超时配置** —— `connect=10s` `total=30s` `keepalive=60s`,防止某些请求挂住耗光线程
- **观测入口集中** —— prometheus 抓 client 状态时是单一 client,debug 简单

## 如何使用 / 配置

#### 硬编码

这是 CubeAPI 默认配置,**管理员不可在运行期改**。如要修改,改源码后重新构建。

#### 验证

```bash
# 看 CubeAPI 进程的 fd
ls /proc/$(pidof cube-api)/fd | wc -l

# 看 socket count
ss -ant | grep $(pidof cube-api) | wc -l
```

#### 客户端行为

不影响普通客户端行为——只对 server-side 而言共享。

**注意**:

- 当 auth callback 同时被很多 client 调用,`pool_max_idle_per_host=100` 可能会成为瓶颈。常见调优:
  - 把 auth callback 域名走多个 VIP(L4 load balancer 散开)
  - 改成 client-side keep-alive 后,连接合并
- `connect_timeout=10s` 在网络抖动时会**连续 fast-fail**,因此 callback 失败可见
- 不要尝试关掉 keep-alive(`TcpKeepalive`):`time wait` 状态可能在高并发时把 5-tuple 占满
- 客户端构建**线程安全**,但**不要**频繁 rebuild(否则连接池全部扔掉重建)
- **典型坑**:部署时发现 fd 涨到 65535;如果 host `ulimit -n` 调到 1M,但 `net.core.somaxconn` 没调,新建连接排队 → CubeAPI 卡 10s 才能 throw 错误
