---
title: "4.1 外部 auth callback"
date: 2026-07-04T14:31:49+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/25-auth-callback/"
summary: "CubeSandbox 自身不实现 OAuth/JWT 之类的内嵌认证,而是把\"是否放行\"的决定委托给一个外部 HTTP 服务。每次接收请求,CubeAPI 都会把请求转发给外部 authcallbackurl,由 callback 返回 "
cover:
  image: ""
  alt: ""
  caption: ""
---
# 4.1 外部 auth callback

## 机制原理

CubeSandbox 自身**不实现 OAuth/JWT** 之类的内嵌认证,而是把"是否放行"的决定委托给一个**外部 HTTP 服务**。每次接收请求,CubeAPI 都会把请求转发给外部 `auth_callback_url`,由 callback 返回 "通过 / 拒绝"。

落地实现:`CubeAPI/src/middleware/auth.rs:1-120`

```rust
async fn unified_auth(
    State(state): State<Arc<AppState>>,
    req: Request,
    next: Next,
) -> Result<Response> {
    if req.path() == "/health" {
        return Ok(next.run(req).await);   // 4.4 健康检查豁免
    }
    let credential = extract_credential(&req);   // Bearer 优先, 否则 X-API-Key
    let resp = state.http_client.post(&state.config.auth_callback_url)
        .header("Authorization", credential.bearer.clone().unwrap_or_default())
        .header("X-API-Key",     credential.api_key.clone().unwrap_or_default())
        .header("X-Request-Path",   req.path())
        .header("X-Request-Method", req.method().to_string())
        .send().await?;
    if resp.status() != 200 {
        return Err(Error::Unauthorized);
    }
    Ok(next.run(req).await)
}

fn extract_credential(req: &Request) -> Credential {
    let bearer = req.headers().get("Authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|s| s.strip_prefix("Bearer "))
        .map(|s| s.to_string());
    let api_key = req.headers().get("X-API-Key")
        .and_then(|v| v.to_str().ok())
        .map(|s| s.to_string());
    Credential { bearer, api_key }
}
```

`docs/guide/authentication.md` 给出了 Python/FastAPI 完整 callback 服务示例。

## 为什么 CubeSandbox 这样设计

- **不重新发明认证协议** —— OAuth 2.0 / OIDC 已经有众多主流实现,再写一遍风险大且功不全
- **多租户 client 用现成的 IAM** —— 接入到公司 Okta / Auth0 / Aliyun IDaaS 时,只要换一个 callback URL
- **职责清晰** —— CubeAPI 只关心 "既然你过了认证,那 sandbox 申请是被授权的"
- **避免凭据泄漏在 saas 边界** —— credential 在 server-to-server 转发,不写到 LOG

## 如何使用 / 配置

#### 启动 CubeAPI 指向 callback

```bash
./cube-api --auth-callback-url https://auth.example.com/cube/verify
# 或
AUTH_CALLBACK_URL=https://auth.example.com/cube/verify ./cube-api
```

#### 启动 callback 服务(Python/FastAPI 示例)

```python
# docs/guide/authentication.md 中完整实现
@app.post("/cube/verify")
async def verify(request: Request):
    body = await request.json()
    # body: { user: { ... }, requested_method, requested_path }
    path = request.headers.get("X-Request-Path")
    method = request.headers.get("X-Request-Method")

    # 防读权限提升到删/改
    if method in ("DELETE", "PATCH", "PUT") and \
       not user_has_write(user, path):
        raise HTTPException(403, "no write on this path")

    return {"ok": True}
```

#### 客户端调用

```bash
# 带 Bearer
curl -X POST https://cube-api.example.com/sandboxes \
  -H 'Authorization: Bearer eyJ...' \
  -d '{...}'

# 带 API Key
curl -X POST https://cube-api.example.com/sandboxes \
  -H 'X-API-Key: sk-...'
```

#### 不启用 auth(开发环境)

```bash
# 默认不设 AUTH_CALLBACK_URL,所有请求无认证放行 ⚠️
unset AUTH_CALLBACK_URL
./cube-api
```

**严禁在生产环境不设置 callback!**

#### Callback 容错

- callback 请求超时(>2s):CubeAPI 默认返回 401,可以调大 `auth_callback_timeout_ms`
- callback 返回 5xx:CubeAPI 默认 5xx 透传(便于调试),生产可改 fail-closed

**注意**:

- **任何路径必须 callback 200 才放行**(除 `/health`)—— 这是默认行为
- callback 验证阶段务必备齐 `X-Request-Path` 和 `X-Request-Method`,否则攻击者只需拿着 valid token 就能调任何 path,扩大攻击面
- **审计 callback 自身** —— 万一 callback 自身被攻破,所有 CubeAPI 调用都会被无端放行
- 上线前**强制**执行 callback 服务在多 AZ / 多实例,不要单点
- 在 hot path 加 callback 可能引入 5-10ms 延迟;高 QPS 集群建议用本地 IAM SDK + signed claims,而不是 callback
- 不要在 callback URL 中带 secret/cookie —— CubeAPI 把原始 credential 转发给 callback,如果 callback URL 被中间人,泄露的就是真的 key
