---
title: "4.6 WebUI 数字助理 (DB-backed session)"
date: 2026-07-04T14:33:28+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/30-webui-session/"
summary: "CubeSandbox 自带 WebUI 控制台,登入后由后端数据库(mysql/postgres)管理 session,不发 cookie,而是用 x-session-token header 携带不透明 token。"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 4.6 WebUI 数字助理 (DB-backed session)

## 机制原理

CubeSandbox 自带 WebUI 控制台,登入后由**后端数据库**(mysql/postgres)管理 session,不发 cookie,而是用 `x-session-token` header 携带不透明 token。

完整逻辑在 `CubeAPI/src/handlers/auth.rs:1-100`:

```rust
const SESSION_HEADER: &str = "x-session-token";
const SESSION_TTL_SECS: i64 = 24 * 60 * 60;     // 24 小时

async fn login(
    State(state): State<Arc<AppState>>,
    Json(req): Json<LoginRequest>,
) -> Result<Json<LoginResponse>> {
    let user = state.db.find_user_by_username(&req.username).await?;
    if !password_matches(&req.password, &user.password_hash) {
        return Err(Error::Unauthorized);
    }
    let token = generate_session_token();
    let expires_at = Utc::now().timestamp() + SESSION_TTL_SECS;
    state.db.create_session(&token, user.id, expires_at).await?;
    Ok(Json(LoginResponse { session_token: token }))
}

async fn change_password(
    State(state): State<Arc<AppState>>,
    Json(req): Json<ChangePasswordRequest>,
) -> Result<()> {
    let user_id = req.session.user_id;
    if !password_matches(&req.old_password, ...) {
        return Err(Error::Unauthorized);
    }
    state.db.update_password(user_id, hash_password(&req.new_password)).await?;
    Ok(())
}
```

密码哈希在 `CubeAPI/src/crypto.rs` `verify_password()`。

## 为什么 CubeSandbox 这样设计

- **DB-backed session 利于多实例共享** —— 不必粘 nginx / sticky session,任意 instance 看到 token 都能验证
- **不透明 session token** 不带数据,即便泄漏也无法反推出 user_id(若配合 secure hash)
- **TTL 24h** —— 平衡安全与 ops 便利(短至 30 分钟 ops 频繁被踢)
- **首次启动播种 admin/admin** —— 注释显式提到首次 migrate 时创建 admin 用户,要求首次登录必改密码

## 如何使用 / 配置

#### 登录

```bash
curl -X POST http://cube-api:3000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username": "admin", "password": "admin"}'

# 返回
{
  "session_token": "st_dGhpcyBpcyBzdXBlciBsb25nIHNlc3Npb24gdG9rZW4="
}
```

#### 调用受保护端点

```bash
curl http://cube-api:3000/sandboxes \
  -H 'x-session-token: st_...'
```

#### 修改密码(强制流)

```bash
curl -X POST http://cube-api:3000/auth/change-password \
  -H 'x-session-token: st_' \
  -H 'Content-Type: application/json' \
  -d '{"old_password": "admin", "new_password": "Your$StrongP@ss"}'
```

#### WebUI 默认用户

| 项 | 默认 |
|---|---|
| 用户名 | `admin` |
| 密码 | `admin` |
| 首次行为 | 第一次登录强制改密码(常见实践) |

#### 登出 / token 失效

```bash
curl -X DELETE http://cube-api:3000/auth/session \
  -H 'x-session-token: st_...'
```

**注意**:

- **必须** 首次登录后改密码 —— admin/admin 默认密码在多租户场景是高风险
- session token 选 token-rotation:
  - 用户可疑活动后立刻踢所有 session
  - 改密码后旧 session token 立即失效
- TTL 24h 太长,**生产建议 1-4h**,token 自动失效
- session token 必须走 `HTTPS`(否则 header 被网络嗅探)
- 不必在客户端用 cookie —— CubeSandbox 偏偏用 header 是为 API/CLI 友好;但**前端必须妥善防御 XSS**,因为 token 暴露在 header 被窃取
- `password_matches` 使用 argon2 / scrypt / bcrypt 等慢哈希;配置错误时降级成 SHA256,风险升高
- session 数据库表必须**定期清理过期行**,避免 mysql 主键空间膨胀
- **强烈建议给 CubeAPI 接 SSO**(OIDC / LDAP),而不是 admin/admin 散伙
