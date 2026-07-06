---
title: "4.4 /health 豁免"
date: 2026-07-04T14:32:46+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/28-health-endpoint-exempt/"
summary: "Every request (except `/health`) must carry an `Authorization: Bearer <token>` or `X-API-Key: <key>` header"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 4.4 /health 豁免

## 机制原理

`/health` 是 CubeAPI 内置的 liveness / readiness 探针,**不受 auth callback / rate limit 约束**。

`docs/guide/authentication.md:8-12`:

> Every request (except `/health`) must carry an `Authorization: Bearer <token>` or `X-API-Key: <key>` header

中间件层面:

```rust
async fn unified_auth(...) -> Result<Response> {
    if req.path() == "/health" {
        return Ok(next.run(req).await);    // 直接放行
    }
    // ... 走 callback 验证
}
```

## 为什么 CubeSandbox 这样设计

- **健康检查不该被认证挡死** —— 否则一旦 callback 服务挂掉,CubeAPI 自己被 K8s 重启,既检测不到也拉不起来
- **readiness 端点** 由 K8s 在 rolling update 时高速轮询,需要每次 < 10ms 响应,如果走 callback 路径可能延迟几十 ms 甚至 timeout
- **健康检查常常来自 untrusted agent** —— 如果未来 CubeAPI 把健康端点公开给 internet,默认还要 auth 就是 anti-pattern
- **故障恢复路径** —— 生产常见操作"我把 callback 搞挂,先 fix callback,等它返回,但 CubeAPI 自己需要保持 healthy";`/health` 不受此牵连

## 如何使用 / 配置

#### 客户端调用

```bash
# 不需要任何 header
curl http://cube-api:3000/health

# 输出
{"status": "ok", "components": {"mysql": "ok", "redis": "ok"}}
```

#### K8s 探针

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 3000
  initialDelaySeconds: 30
  periodSeconds: 10
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /health
    port: 3000
  initialDelaySeconds: 5
  periodSeconds: 5
  failureThreshold: 1
```

#### LB 层

```bash
# HAProxy config
backend cube_api
    balance roundrobin
    option httpchk GET /health
    http-check expect status 200
```

#### 安全注意

- **/health 不带 sensitive 信息** —— 不能返回 mysql 密码 / vm 列表等
- 真要分细:liveness vs readiness 拆成 `/livez` 和 `/readyz`
- `/health` 不论何人都可触发 DoS——上 NGINX 前限速
- 监控 `/health` 触达频率,如果 K8s endpoint controller 频繁调,说明状态不是真的 healthy

**最佳实践**:

- 对 health 端点做 K8s 专用 ServiceAccount + NetworkPolicy,避免对外网暴露
- 内部回调才走 auth callback,但 health 端点**禁用 callback**
- 如果组件是 mysql / redis,即便主库挂也建议返回 degraded,不要直接 500 —— 否则 K8s 频繁重启 CubeAPI
- 高频轮询时,`/health` 端点响应小于 1ms,latency 由组件轮询频率决定

**注意**:

- /health 端点本身**默认 200**,除非状态 down;如果业务希望 truthy 反映 sandbox 创建失败,需要 inline 探测
- 不要把 /health 改名为其他字符串——大量 daemon-side 配置(K8s manifest, Prometheus)都 hard-code 它
- 实验新 auth callback 时**首先**验证 /health 工作正常,否则一旦误配 callback 路由,CubeAPI 不可恢复
- 如果想改 /health 行为(添加 custom checks),需要 fork 源码
