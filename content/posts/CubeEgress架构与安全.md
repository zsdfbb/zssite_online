---
title: "CubeEgress 架构、处理流程与安全配置"
date: 2026-07-08T00:00:00+08:00
draft: false
tags: ["cubesandbox"]
categories: ["cubesandbox"]
comments: true
showToc: true
weight: 100
---


> 调研时间: 2026/07/08
> 调研范围: `/home/zs/Develop/TraceCubeSandbox/CubeEgress/` 全量 Lua + 配置
> 目的: 系统性梳理 CubeEgress (出站透明代理) 的架构、处理流程与安全配置
>
> 每节都带文件位置证据,可以直接引用。

---

## 1. 概述

CubeEgress 是 CubeSandbox 项目的**出站透明代理**,基于 OpenResty (nginx + LuaJIT) 构建,通过内核 TPROXY 透明地拦截来自 sandbox 的所有出站 TCP 80/443 流量并执行安全策略。

| 属性 | 值 |
|------|-----|
| **语言** | Lua (9 模块,~1800 行) + Bash (入口/脚本) |
| **运行时** | OpenResty (nginx + LuaJIT, TPROXY 补丁) |
| **基础镜像** | openresty-tproxy (自定义 nginx 1.29.2 + TPROXY) |
| **监听地址** | `192.168.0.1:8080` (HTTP) + `192.168.0.1:8443` (HTTPS MITM) |
| **管理 API** | `127.0.0.1:9090/admin/v1/` |
| **上游证书验证** | `proxy_ssl_verify on`, 深度 4 |

**核心职责**:
- 透明拦截沙箱出站 TCP 80/443 流量 (TPROXY)
- MITM TLS 解密 (per-SNI ECDSA P-256 叶证书)
- 策略执行 (每规则 allow/deny, 默认拒绝)
- 凭据注入 (内联秘密 → HTTP 标头)
- 结构化审计日志 (JSONL)
- 安全事件检测

---

## 2. 架构

### 2.1 目录结构

```
CubeEgress/
├── nginx.conf                   # OpenResty 主配置
├── Dockerfile                   # 容器镜像构建
├── Makefile                     # 构建入口
├── start.sh                     # 容器入口点
├── gen-ca.sh                    # 根 CA 生成
├── lua/
│   ├── bootstrap.lua            # 启动策略恢复
│   ├── init_worker_phase.lua    # Worker 初始化
│   ├── policy.lua               # 策略存储 CRUD
│   ├── access_phase.lua         # 请求执行引擎
│   ├── cert_signer.lua          # per-SNI TLS 证书签名
│   ├── audit.lua                # 审计日志
│   ├── admin.lua                # 管理 API
│   ├── redactor.lua             # 审计日志编辑
│   └── debug_dump.lua           # 调试转储
├── openresty/
│   ├── Dockerfile               # OpenResty + TPROXY 补丁
│   └── 0001-nginx-support-TPROXY-listeners-via-transparent-liste.patch
└── scripts/
    ├── cube-proxy-net.service   # systemd unit
    └── cube-proxy-iptables-init.sh # TPROXY iptables 规则
```

### 2.2 模块分层

```
┌──────────────────────────────────────────────────────────┐
│  数据面 (nginx 请求处理管道)                                │
│    • access_phase.lua — 规则匹配 + 执行决策                │
│    • cert_signer.lua — TLS 握手 + 证书生成                 │
│    • policy.lua — 策略查找                                 │
├──────────────────────────────────────────────────────────┤
│  控制面 (管理 API)                                         │
│    • admin.lua — CRUD 策略 + 健康检查                      │
│    • bootstrap.lua — 启动时策略恢复                         │
├──────────────────────────────────────────────────────────┤
│  审计面                                                     │
│    • audit.lua — per-request JSONL 日志                    │
│    • redactor.lua — 敏感标头编辑                            │
├──────────────────────────────────────────────────────────┤
│  内核层 (TPROXY)                                           │
│    • iptables mangle/PREROUTING + ip rule                 │
│    • 透明拦截 -> OpenResty 监听套接字                       │
└──────────────────────────────────────────────────────────┘
```

### 2.3 与上下游服务关系

```
┌──────────────┐   TPROXY redirect    ┌──────────────┐   上游   ┌──────────┐
│   Sandbox    │ ────────────────────▶│  CubeEgress  │ ────────▶│ Internet │
│  (VM/容器)   │   iptables mangle    │  (OpenResty) │  proxy   │          │
└──────────────┘                      └──────┬───────┘          └──────────┘
                                             │ 127.0.0.1:9090
                                             ▼
                                      ┌──────────────┐
                                      │ network-agent │
                                      │ (策略推送)     │
                                      └──────────────┘
```

CubeEgress 是**纯代理**,不处理业务逻辑,所有策略由 network-agent 通过管理 API 推送。

---

## 3. 处理流程

### 3.1 请求处理

```
Sandbox -> CubeEgress        策略: allow → 上游
  │                              │
  │ TCP 80/443 → TPROXY          │
  │ ────────────────────────────▶│
  │                              │
  │                              │ ① access_phase.lua
  │                              │   - 查找策略 (per-sandbox_ip)
  │                              │   - 默认拒绝 (403)
  │                              │
  │                              │ ② cert_signer.lua (HTTPS)
  │                              │   - 提取 SNI
  │                              │   - 生成/缓存 ECDSA P-256 叶证书
  │                              │   - MITM TLS 握手
  │                              │
  │                              │ ③ G1-G4 门控检查
  │                              │   - G1: scheme 合法
  │                              │   - G4: Host == SNI (HTTPS)
  │                              │
  │                              │ ④ 注入凭据
  │                              │   - 清除伪造标头
  │                              │   - 注入内联秘密
  │                              │
  │                              │ ⑤ 转发上游
  │                              │   - proxy_pass 到 dst_ip:port
  │                              │   - 验证上游 TLS 证书
  │                              │
  │                              │ ⑥ audit.lua
  │                              │   - redactor 编辑敏感字段
  │                              │   - JSONL 写入审计日志
  │                              │
  │ ◀───────────────────────────│ 允许/拒绝 响应
```

**关键文件位置**:
- access_phase: `CubeEgress/lua/access_phase.lua`
- cert_signer: `CubeEgress/lua/cert_signer.lua`
- audit: `CubeEgress/lua/audit.lua`
- policy: `CubeEgress/lua/policy.lua`

### 3.2 策略引导

```
启动时:                       运行时:
bootstrap.lua                  admin API
  │                              │
  │ GET CUBE_EGRESS_BOOTSTRAP_URL│ PUT /admin/v1/policies/:sandbox_ip
  │ 3x 指数退避重试              │
  │ ──────────▶ 外部策略服务      │ ◀─── network-agent
  │ ◀────────── JSON 策略字典     │
  │                              │
  │ policy.bulk_load()           │ policy.upsert()
  │ → 共享内存                   │ → 共享内存
```

**关键常量**: `CubeEgress/lua/bootstrap.lua:7-9`
```lua
local DEFAULT_TIMEOUT_MS = 10000   -- 10s
local MAX_RETRIES = 3              -- 最多重试 3 次
local BACKOFF_BASE_MS = 500        -- 指数退避: 500ms, 1s, 2s
```

**启动安全**: 如果引导加载策略失败（0 个有效策略且策略列表非空）,调用 `os.exit(1)` 直接退出 — 数据面绝不能在无策略时服务流量。

---

## 4. 路由与端点

### 4.1 数据面 (TPROXY 透明代理)

来源: `CubeEgress/nginx.conf:59-65`

| 协议 | 监听地址 | 说明 |
|------|---------|------|
| HTTP | `192.168.0.1:8080 transparent reuseport` | 明文 HTTP 代理 |
| HTTPS | `192.168.0.1:8443 ssl transparent reuseport` | MITM TLS 终止 |

### 4.2 管理 API (`127.0.0.1:9090/admin/v1/`)

来源: `CubeEgress/lua/admin.lua`

| 方法 | 路径 | Handler | 用途 |
|------|------|---------|------|
| `PUT` | `/policies/:sandbox_ip` | `h_put_policy` | 创建/更新策略 |
| `PATCH` | `/policies/:sandbox_ip` | `h_patch_policy` | 部分更新策略 (按 rule ID) |
| `GET` | `/policies/:sandbox_ip` | `h_get_policy` | 查询策略 |
| `DELETE` | `/policies/:sandbox_ip` | `h_delete_policy` | 删除策略 |
| `GET` | `/policies` | `h_get_policies` | 列出所有策略 |
| `GET` | `/dump` | `h_dump` | 所有策略转储 (秘密已编辑) |
| `GET` | `/health` | `h_health` | 健康检查 |
| `ANY` | `/secrets...` | — | 410 Gone (旧版端点已移除) |

管理 API 仅绑定 `127.0.0.1:9090`,沙箱无法访问。

---

## 5. 认证与安全机制

### 5.1 策略执行 (默认拒绝)

来源: `CubeEgress/lua/access_phase.lua`

**决策流程** (`_M.decide()`):
- 启动状态门控: 仅 `"ready"` 或 `"skipped"` 放行; `"pending"`/`"unknown"` → 403
- 策略查找: 获取 `sandbox_ip` 的策略; 无策略 → 403
- 规则匹配: 按顺序 first-match-wins; 无规则匹配 → 403 `"no_rule_match"`

**匹配字段** (所有可选,缺失即通配符):
- `sni`: 精确或 `"*."` 前缀通配符 (HTTPS 仅)
- `host`: 精确或 `"*."` 前缀通配符 (HTTP Host 头)
- `method`: 字符串数组 (任意匹配)
- `path`: 精确或尾随 `"*"` 前缀通配符
- `scheme`: `"http"` 或 `"https"`

### 5.2 注入门控 (G1-G4)

来源: `CubeEgress/lua/access_phase.lua` (`inject_gates` 函数)

| 门控 | 检查 | 失败处理 |
|------|------|----------|
| G1 | scheme 合法 (http/https) | 清除标头,不记事件 |
| G4 (HTTPS) | Host == SNI | 清除标头,记 security_event,拒绝转发伪造凭据 |
| G4 (HTTP) | Host 头存在且非空 | 缺失则记 security_event |

**门控失败语义**: "任何门控失败 → 丢弃注入; 请求根据 action.allow 继续"。G4 不匹配使请求继续进行(无秘密注入),但将决策标记为 security_event。

**注入前清洗**: 在注入前清除规则打算覆盖的所有候选标头,防止沙箱通过标头竞争实现伪造注入。

### 5.3 审计编辑

来源: `CubeEgress/lua/redactor.lua`

**敏感标头编辑** (`redact_headers`):
- 精确匹配拒绝列表: `authorization` (保留方案), `proxy-authorization`, `cookie` (保留名称), `set-cookie`, `x-api-key`, `x-auth-token`, `api-key`, `token`
- 子串匹配拒绝列表: 包含 `"token"`, `"secret"`, `"key"`, `"password"`, `"credential"`, `"auth"` 的头名

**JSON body 编辑** (`redact_json`):
- 递归扫描 JSON body
- 匹配秘密字段 → `"<redacted>"`

**指纹代替原始值**: 策略写路径上对每个内联 secret 计算 SHA-256 指纹 (`"fp-"` + 前 8 十六进制字符),审计日志仅记录 `secret_ref_synthetic`,原始值永不落地。

### 5.4 安全事件检测

| 事件 | 触发条件 | 来源 |
|------|---------|------|
| 标头注入失败 | secret 缺失/设置错误 | `access_phase.lua:allow()` |
| Host/SNI 不匹配 | G4 门控失败 | `access_phase.lua:inject_gates()` |
| 上游连接失败 | 502/504, 零字节响应 | `audit.lua:write_one()` |
| 引导未完成 | bootstrap 失败 | `bootstrap.lua` |
| TLS 握手失败 | cert_signer 异常 | `cert_signer.lua:serve()` |

---

## 6. 配置项

### 6.1 环境变量

| 配置项 | 环境变量 | 默认值 | 说明 |
|--------|----------|--------|------|
| Bootstrap URL | `CUBE_EGRESS_BOOTSTRAP_URL` | (None) | 启动策略恢复 URL |
| Debug | `CUBE_EGRESS_DEBUG_DUMP` | (None) | `=1` 时开启调试标头转储 |
| TPROXY IP | `CUBE_TPROXY_ON_IP` | `192.168.0.1` | 代理监听地址 |
| 入口接口 | `CUBE_INGRESS_IFACE` | `cube-dev` | TPROXY 匹配接口 |

### 6.2 路径配置

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| CA 证书路径 | `/etc/cube/ca/cube-root-ca.crt` | MITM 根 CA 证书 |
| CA 密钥路径 | `/etc/cube/ca/cube-root-ca.key` | MITM 根 CA 密钥 |
| 占位符证书 | `/etc/cube/ca/placeholder.crt` | nginx ssl_certificate 占位 |
| 占位符密钥 | `/etc/cube/ca/placeholder.key` | nginx ssl_certificate_key 占位 |
| 审计日志路径 | `/data/log/cube-egress/access.jsonl` | JSONL 审计日志 |
| 版本信息 | `/etc/cube/version.json` | `{"version","commit","build_time"}` |

### 6.3 nginx 关键配置

来源: `CubeEgress/nginx.conf:34-50`

| 配置项 | 值 | 说明 |
|--------|----|------|
| worker_processes | 2 | 工作进程数 |
| worker_rlimit_nofile | 65535 | 文件描述符上限 |
| `cert_cache` | 64m | SSL 证书缓存 |
| `cert_locks` | 8m | 签名锁 |
| `policy_store` | 16m | 策略存储 |
| `meta_store` | 1m | 启动状态 |
| `cube_ssl` | 4m | SSL 会话缓存 |
| proxy_ssl_verify | on | 上游 TLS 验证 |
| proxy_ssl_verify_depth | 4 | 证书链深度 |
| proxy_ssl_server_name | on | SNI 透传 |
| ssl_protocols | TLSv1.2 TLSv1.3 | 最低 TLS 版本 |
| ssl_ciphers | HIGH:!aNULL:!MD5 | 密码套件 |

---

## 7. 关键文件清单

| 模块 | 路径 | 关键内容 |
|------|------|----------|
| **配置** | `CubeEgress/nginx.conf` | OpenResty 配置 (数据面 + 管理面) |
| **入口** | `CubeEgress/start.sh` | 容器启动脚本 (CA 校验 + 配置检查) |
| **策略执行** | `CubeEgress/lua/access_phase.lua` | G1-G4 门控 + 注入 + 安全事件 |
| **策略存储** | `CubeEgress/lua/policy.lua` | CRUD + 指纹 + 索引 |
| **TLS** | `CubeEgress/lua/cert_signer.lua` | per-SNI ECDSA P-256 证书签名 |
| **审计** | `CubeEgress/lua/audit.lua` | JSONL 日志 + 安全事件双重写入 |
| **管理 API** | `CubeEgress/lua/admin.lua` | 策略管理 (7 端点) |
| **编辑** | `CubeEgress/lua/redactor.lua` | 敏感标头 + JSON body 编辑 |
| **引导** | `CubeEgress/lua/bootstrap.lua` | 启动恢复 (3x 指数退避) |
| **TPROXY 规则** | `CubeEgress/scripts/cube-proxy-iptables-init.sh` | iptables + ip rule |
| **systemd** | `CubeEgress/scripts/cube-proxy-net.service` | oneshot 网络设置 |
| **基础镜像** | `CubeEgress/openresty/Dockerfile` | OpenResty 1.29.2 + TPROXY 补丁 |
| **TPROXY 补丁** | `CubeEgress/openresty/0001-nginx-support-TPROXY-listeners-via-transparent-liste.patch` | nginx TPROXY listen 支持 |

---

## 8. 安全注意事项

### 8.1 核心安全设计

1. **默认拒绝**: 无策略 / 引导未完成 → 403
2. **注入前清洗**: 沙箱伪造的凭据标头在注入前清除
3. **审计编辑**: 秘密仅存指纹 (SHA-256 前 8 字符),原始值永不落地
4. **管理隔离**: admin API 仅限 `127.0.0.1`
5. **CA 检查**: `start.sh` 启动时验证根 CA 有效且未过期
6. **启动严格模式**: bootstrap 加载 0 个有效策略时直接 `os.exit(1)`
7. **审计文件验证**: `init_worker()` 时验证审计文件可写,否则 `os.exit(1)`

### 8.2 已知风险

| # | 风险 | 位置 | 说明 |
|---|------|------|------|
| **R1** | dump 端点可能暴露秘密 | `CubeEgress/lua/admin.lua:h_dump` | GET 响应已替换 secret 为 `"***REDACTED***"`,但需持续确认不引入新泄露路径 |
| **R2** | debug_dump 不安全 | `CubeEgress/lua/debug_dump.lua` | `CUBE_EGRESS_DEBUG_DUMP=1` 时完全绕过 redactor 泄露所有标头,仅限开发调试 |
| **R3** | MITM CA 信任根 | `gen-ca.sh` + `start.sh` | 根 CA 私钥泄露 → 攻击者可解密所有 TLS 流量;需严格保管私钥 |
| **R4** | 索引锁单点故障 | `CubeEgress/lua/policy.lua` | `__index_lock__` 用 `shared:add()` 实现临界区,单写者假设成立,多 writer 竞争有丢失更新风险 |
| **R5** | TPROXY 绕过 | `scripts/cube-proxy-iptables-init.sh` | 如果 iptables 规则被误删除或重排,沙箱出站流量直接绕过代理 |

### 8.3 与 SVG 边界模型

| SVG 边界 | CubeEgress 中的对应 |
|----------|---------------------|
| T6 (Egress) | 整个 CubeEgress 进程 |
| L4 (host 内核域) | TPROXY + iptables mangle/PREROUTING |
| L7 (可观测性) | 审计日志 (JSONL) + 安全事件 |

---

## 9. 总结:安全设计权衡

1. **TPROXY 透明代理 vs 传统代理**: 不需要沙箱配置代理环境变量,但需要宿主内核补丁 (TPROXY + ip rule) 和 nginx TPROXY 补丁。
2. **MITM TLS 解密 vs 透传**: 能对加密流量做策略检查,但引入根 CA 私钥保管风险和证书兼容性问题。
3. **内联秘密模型 vs 外部 KMS**: 凭据随策略一起下发,简化部署但也意味着凭据存在于共享内存中。审计用 SHA-256 指纹而非原始值。
4. **双层审计机制**: nginx `access_log` stdout (ops grep) + Lua JSONL 文件 (合规审计),双重保证但增加了 I/O。
5. **默认拒绝 + 启动门控**: 保证即使在引导期间也不会有未经允许的流量通过,代价是启动时必须依赖外部策略服务可达。
6. **G1-G4 门控**: 防止沙箱伪造秘密标头进行注入欺骗,特别是 G4 (Host == SNI) 防止 TLS 请求走私攻击。

---

## 10. 学习路线建议

| Phase | 重点研读章节 |
|-------|-------------|
| **Phase 0** (透明代理概要) | §4 路由 + §2 架构 + §3 处理流程 |
| **Phase 1** (策略执行引擎) | §5.1 策略执行 + §5.2 门控 + `access_phase.lua` |
| **Phase 2** (MITM TLS) | §5.3 审计编辑 + `cert_signer.lua` + `redactor.lua` |
| **Phase 3** (安全与运维) | §8 安全注意事项 + §6 配置项 + `start.sh` |
| **Phase 4** (TPROXY 内核层) | §2 架构 + `scripts/cube-proxy-iptables-init.sh` + nginx TPROXY 补丁 |
