---
title: "CubeMaster 架构、处理流程与安全配置"
date: 2026-07-08T00:00:00+08:00
draft: false
tags: ["cubesandbox"]
categories: ["cubesandbox"]
comments: true
showToc: true
weight: 100
---


> 调研时间: 2026/07/07
> 调研范围: `/home/zs/Develop/TraceCubeSandbox/CubeMaster/` 全量 Go 源码 + 配置 + 部署脚本
> 目的: 系统性梳理 CubeMaster (CubeSandbox 的控制面 + 调度器) 的架构、处理流程与安全配置
> 配套文档: [../安全边界/T2-operator-trust.md](../安全边界/T2-operator-trust.md) (边界视角,本文档是"内部视角")
>
> 每节都带文件位置证据,可以直接引用。

---

## 1. 概述

CubeMaster 是 CubeSandbox 项目的 **控制面 + 调度中心**,接收来自 CubeAPI 的 gRPC 请求,完成:
- 配置加载与 hotswap 热重载
- 沙箱规格 (sandboxspec) 处理与持久化
- 节点调度 (scheduler)
- 与下游 Cubelet 通信 (ttrpc)
- host-mount 路径白名单校验
- 模板中心 (templatecenter) 镜像拉取
- 元数据缓存 (Redis) 与状态存储 (MySQL)

| 属性 | 值 |
|------|-----|
| **语言** | Go (1.21+) |
| **RPC 框架** | ttrpc (与上游 CubeAPI / 下游 Cubelet 通信) |
| **HTTP API** | `http_port: 8089` (`conf.yaml:2-5`) |
| **依赖存储** | MySQL (状态持久化) + Redis (元数据 + TTL) |
| **容器命名空间** | containerd namespace (多租户隔离) |
| **关键依赖 (go.mod)** | `containerd`, `ttrpc`, `mysql`, `redis`, `cube-router/cube-api`, `cube-carter/cubelet` |

**核心职责**:
- 接收 CubeAPI gRPC 请求 (`CreateSandboxRequest` / `GetSandboxRequest` / `DeleteSandboxRequest` 等)
- 调度 sandbox 到合适的 Cubelet 节点
- 校验 host-mount 路径 (安全关键)
- 加载并热重载 conf.yaml (hotswap)
- 通过 containerd namespace 隔离多租户
- 把 sandbox 状态同步到 MySQL + Redis

---

## 2. 架构

### 2.1 顶层目录结构

```
CubeMaster/
├── Makefile                        # 构建 + 测试
├── conf.yaml                       # 主配置
├── go.mod / go.sum
├── api/                            # protobuf 定义
├── cmd/
│   ├── cubemaster/                 # 主二进制
│   │   └── main.go                 # 入口
│   └── cubemastercli/              # CLI 工具 (tpl create-from-image 等)
├── docker/                         # Dockerfile + 镜像构建
├── scripts/                        # 运维脚本
├── integration/                    # 集成测试
└── pkg/
    ├── base/
    │   └── config/                 # conf.yaml 加载 + hotswap
    │       ├── config.go           # Config 结构 + hotswap reload (1186-1193)
    ├── cubelet/                    # 与 Cubelet ttrpc 客户端
    ├── errorcode/                  # 错误码定义
    ├── instancecache/              # 内存实例缓存
    ├── lifecycle/                  # 沙箱生命周期钩子
    ├── localcache/                 # 本地缓存
    ├── nodemeta/                   # 节点元信息
    ├── sandboxspec/                # sandbox 规格解析与校验
    ├── scheduler/                  # 节点调度算法
    ├── selector/                   # 选择器 (label / namespace)
    ├── server/                     # ttrpc server 装配
    ├── service/                    # 业务服务实现
    │   └── sandbox/
    │       ├── sandbox_run.go      # 沙箱创建主流程
    │       ├── hostdir_mount.go    # host-mount 路径校验 (安全关键)
    │       ├── sandbox_info.go
    │       ├── sandbox_remove.go
    │       └── ... (10+ 文件)
    ├── task/                       # 异步任务
    └── templatecenter/             # 模板镜像拉取
        └── image/
            ├── export.go           # rootless umoci 导出
            ├── disk.go             # 全 root 路径
            └── pull.go             # skopeo pull
```

### 2.2 模块分层

```
┌──────────────────────────────────────────────────────────────────┐
│  入口层                                                            │
│    • cmd/cubemaster/main.go — 启动 + 信号处理 + graceful shutdown │
├──────────────────────────────────────────────────────────────────┤
│  Server 层 (ttrpc + HTTP)                                         │
│    • pkg/server — ttrpc server 装配 + 路由                        │
│    • pkg/base/config — conf.yaml + hotswap                       │
├──────────────────────────────────────────────────────────────────┤
│  Service 层 (业务逻辑)                                             │
│    • pkg/service/sandbox — sandbox_run, hostdir_mount, ...        │
│    • pkg/templatecenter — 镜像拉取                                 │
│    • pkg/scheduler — 节点选择                                       │
│    • pkg/sandboxspec — spec 校验                                   │
├──────────────────────────────────────────────────────────────────┤
│  客户端层 (下游通信)                                                │
│    • pkg/cubelet — ttrpc client to Cubelet                         │
├──────────────────────────────────────────────────────────────────┤
│  基础设施层                                                        │
│    • MySQL (状态持久化) + Redis (元数据 + TTL)                    │
│    • containerd namespace (多租户)                                 │
│    • Redis KeyPrefix = "cube:sandbox:*"                            │
└──────────────────────────────────────────────────────────────────┘
```

### 2.3 与上下游服务关系

```
┌──────────┐    ttrpc (mTLS)    ┌─────────────┐    ttrpc     ┌────────┐
│  CubeAPI │ ─────────────────▶│  CubeMaster │ ────────────▶│Cubelet │
│  (Rust)  │ ◀─────────────────│   (Go)      │ ◀────────────│ (Go)   │
└──────────┘   CreateSandbox   └──────┬──────┘  StartVM     └────────┘
                Response             │
                                     │ MySQL (状态)
                                     │ Redis (元数据 + TTL)
                                     │
                                     ▼
                              ┌──────────────┐
                              │  containerd   │
                              │  (namespace)  │
                              └──────────────┘
```

CubeMaster 是 **控制面的大脑**,负责"决定"而非"执行"。

---

## 3. 处理流程

### 3.1 创建 Sandbox (核心流程)

```
CubeAPI                       CubeMaster                                  Cubelet
  │                              │                                          │
  │ CreateSandboxRequest         │                                          │
  │   - templateID               │                                          │
  │   - metadata["host-mount"]   │                                          │
  │   - envVars                  │                                          │
  │   - timeout                  │                                          │
  │ ────────────────────────────▶                                          │
  │                              │                                          │
  │                              │ ① base/config 读取 conf.yaml             │
  │                              │   - AllowedHostMountPrefixes             │
  │                              │                                          │
  │                              │ ② sandboxspec 解析                        │
  │                              │   - 校验 templateID 存在                 │
  │                              │   - 校验 envVars 白名单                   │
  │                              │                                          │
  │                              │ ③ hostdir_mount::injectHostDirMounts     │
  │                              │   - 解析 metadata["host-mount"]          │
  │                              │   - validateHostPath (每条)              │
  │                              │   - 注入 req.Volumes                     │
  │                              │                                          │
  │                              │ ④ scheduler 选择 Cubelet 节点            │
  │                              │   - 负载均衡 / label 匹配                 │
  │                              │                                          │
  │                              │ ⑤ service/sandbox::sandbox_run           │
  │                              │   - MySQL 写入 sandbox 状态             │
  │                              │   - Redis 写入元数据 (TTL)               │
  │                              │   - containerd namespace 关联             │
  │                              │                                          │
  │                              │ ⑥ pkg/cubelet → ttrpc 到 Cubelet        │
  │                              │ ──────────────────────────────────────▶ │
  │                              │                                          │ ⑦ Cubelet 启动
  │                              │                                          │   - CubeShim spawn
  │                              │                                          │   - cube-hypervisor
  │                              │                                          │   - KVM 创建 VM
  │                              │                                          │   - 返回 Sandbox 响应
  │                              │ ◀────────────────────────────────────── │
  │                              │                                          │
  │                              │ ⑧ 聚合响应 (Sandbox + metadata)          │
  │ ◀───────────────────────────│                                          │
  │ CreateSandboxResponse        │                                          │
  │   - sandboxID                │                                          │
  │   - clientID                 │                                          │
  │   - envdAccessToken          │                                          │
  │   - trafficAccessToken       │                                          │
  │                              │                                          │
```

**关键文件位置**:
- 入口: `CubeMaster/cmd/cubemaster/main.go`
- Server: `CubeMaster/pkg/server/`
- sandbox_run 主流程: `CubeMaster/pkg/service/sandbox/sandbox_run.go`
- host-mount 校验: `CubeMaster/pkg/service/sandbox/hostdir_mount.go:37` (`injectHostDirMounts`)
- validateHostPath: `CubeMaster/pkg/service/sandbox/hostdir_mount.go:114-124`
- 配置加载: `CubeMaster/pkg/base/config/config.go`

### 3.2 host-mount 子流程

**这是 CubeMaster 的核心安全功能**。来源: `CubeMaster/pkg/service/sandbox/hostdir_mount.go`

```go
// hostdir_mount.go:37 injectHostDirMounts
func injectHostDirMounts(req *CreateSandboxRequest) error {
    annotations := req.Annotations
    raw, ok := annotations["host-mount"]
    if !ok { return nil }  // 无 host-mount 直接放行

    var mounts []HostDirMountOption
    if err := json.Unmarshal([]byte(raw), &mounts); err != nil {
        return fmt.Errorf("invalid host-mount JSON: %w", err)
    }

    for i, m := range mounts {
        if err := validateHostPath(m.HostPath); err != nil {
            return fmt.Errorf("host-mount entry[%d]: %w", i, err)
        }
    }

    // 注入到 req.Volumes + req.Containers[*].VolumeMounts
    for _, m := range mounts {
        req.Volumes = append(req.Volumes, ...)
        for _, c := range req.Containers {
            c.VolumeMounts = append(c.VolumeMounts, ...)
        }
    }
    return nil
}

// hostdir_mount.go:114-124 validateHostPath
func validateHostPath(hostPath string) error {
    cleaned := filepath.Clean(hostPath)  // 解析 ..

    // 拒绝根目录
    if cleaned == "/" {
        return fmt.Errorf("hostPath %q is not allowed", hostPath)
    }

    // 必须落在 allowed_host_mount_prefixes 白名单内
    for _, prefix := range getAllowedHostMountPrefixes() {
        if strings.HasPrefix(cleaned, prefix) {
            return nil  // 校验通过
        }
    }

    return fmt.Errorf("hostPath %q is not within an allowed mount prefix",
        hostPath)
}
```

**关键点**:
1. **`filepath.Clean` 中和 `..` 路径穿越**: `cleaned := filepath.Clean(hostPath)` 把 `/data/shared/../etc/passwd` 转为 `/etc/passwd`
2. **根目录显式拒绝**: 即使白名单含 `/`,单独 `/` 也被拒绝
3. **前缀匹配**: 必须落在配置的 `allowed_host_mount_prefixes` 内 (默认 `["/data/shared/"]`)
4. **校验时机**: 在 CubeMaster 收到 `CreateSandboxRequest` 后,VM 启动前

**当前安全限制 (commit 5c7025f,2026-07-05)**:
- 字符串级校验,无 inode 检查 → **TOCTOU 风险**: VM 启动时 host 路径可能被攻击者替换符号链接
- 无 owner / permission 检查
- 无 mount propagation 配置

### 3.3 配置 hotswap 热重载

来源: `CubeMaster/pkg/base/config/config.go:1186-1193`

```go
// 监听 conf.yaml 变更,自动重载
func watchConfigReload() {
    watcher, _ := fsnotify.NewWatcher()
    defer watcher.Close()

    for {
        select {
        case event := <-watcher.Events:
            if event.Op&fsnotify.Write == fsnotify.Write {
                if err := reloadConfig(); err != nil {
                    log.Errorf("config reload failed: %v", err)
                } else {
                    log.Info("config reloaded successfully")
                }
            }
        case err := <-watcher.Errors:
            log.Errorf("config watcher error: %v", err)
        }
    }
}
```

**支持的 hotswap 字段** (部分): `AllowedHostMountPrefixes` / `RateLimit*` / `AuthCallback*`

**不支持 hotswap 的字段**: 需重启服务: `MySQL` / `Redis` 凭据 / `HTTP port` / 数据库连接池大小

**已知风险**:
- hotswap 期间存在 race window (新请求使用旧配置,旧请求使用新配置)
- 无字段白名单: 任何 YAML 字段变更都触发重载
- 无签名校验: conf.yaml 被替换为恶意内容会被立即接受
- 无审计: 缺少 hotswap 历史记录

### 3.4 沙箱生命周期其他流程

| 操作 | 端点 (ttrpc) | Handler | 关键逻辑 |
|------|-------------|---------|---------|
| **列出** | `ListSandboxes` | `service/sandbox/sandbox_list.go` | Redis SCAN + MySQL 查询 |
| **详情** | `GetSandbox` | `service/sandbox/sandbox_info.go` | Redis 优先 + MySQL fallback |
| **销毁** | `DeleteSandbox` | `service/sandbox/sandbox_remove.go` | Cubelet 停止 + Redis/MySQL 清理 |
| **更新超时** | `UpdateSandbox` | `service/sandbox/sandbox_update.go` | MySQL UPDATE |
| **执行** | `RunCommand` | `service/sandbox/sandbox_exec.go` | ttrpc → Cubelet → vsock → guest |
| **暴露端口** | `ExposePort` | `service/sandbox/exposed_port_endpoint.go` | Redis 写入端口映射 |
| **快照** | `CreateSnapshot` | `service/sandbox/snapshot/mod.rs` (通过 cubelet 转发) | |

---

## 4. 路由与端点

### 4.1 ttrpc (与 CubeAPI)

| Service | Method | 用途 |
|---------|--------|------|
| `SandboxService` | `CreateSandbox` | 创建 sandbox |
| `SandboxService` | `GetSandbox` | 查询详情 |
| `SandboxService` | `ListSandboxes` | 列出 |
| `SandboxService` | `DeleteSandbox` | 销毁 |
| `SandboxService` | `UpdateSandbox` | 更新 (timeout / metadata) |
| `SandboxService` | `RunCommand` | 执行命令 |
| `SandboxService` | `ExposePort` | 暴露端口 |
| `TemplateService` | `CreateTemplate` / `GetTemplate` / `ListTemplates` / `DeleteTemplate` / `RebuildTemplate` | 模板 CRUD |
| `ClusterService` | `GetClusterOverview` / `GetVersions` | 集群信息 |

### 4.2 HTTP API (内部)

来源: `CubeMaster/conf.yaml:2-5`

```yaml
http_port: 8089
```

**典型 HTTP 端点** (来自 `pkg/server/`):
- `GET /health` — 健康检查
- `GET /api/v1/cluster/overview` — 集群概览 (dashboard)
- `GET /api/v1/sandboxes` — 列出 sandbox
- `GET /api/v1/nodes` — 列出节点
- `GET /api/v1/config` — 查公开配置

(具体路由需查 `pkg/server/router.go` 或类似文件)

### 4.3 CLI 工具 (`cubemastercli`)

来源: `CubeMaster/cmd/cubemastercli/`

| 子命令 | 用途 |
|--------|------|
| `tpl create-from-image` | 从镜像创建模板 |
| `tpl list` | 列模板 |
| `tpl build` | 构建模板 |
| `node list` | 列节点 |
| `cluster status` | 集群状态 |

`cubemastercli` 通过 ttrpc 连接到运行中的 CubeMaster 实例,执行管理操作。

---

## 5. 认证机制

### 5.1 三层信任模型

CubeMaster **不直接面向公网**,依赖上游 CubeAPI 的鉴权:

```
Internet → CubeAPI (T1 鉴权) → CubeMaster (信任上游)
                                   ↓
                                  Cubelet (信任 CubeMaster)
```

**CubeMaster 自身几乎不鉴权** — 它假设调用者 (CubeAPI) 已经通过 T1 鉴权。

### 5.2 ttrpc mTLS

来源: `CubeMaster/conf.yaml` (TLS 段)

CubeMaster 与 CubeAPI / Cubelet 之间走 ttrpc over **mTLS**:
- 双向证书认证
- 证书路径在 `conf.yaml` 中配置 (`tls_cert` / `tls_key` / `tls_ca`)
- 无 token / OAuth — 完全依赖 mTLS 证书信任链

### 5.3 WebUI 间接鉴权

WebUI 用户登录走 `CubeAPI/src/handlers/auth.rs`,CubeMaster 不参与鉴权决策,只接收来自 CubeAPI 的已认证请求。

### 5.4 凭据管理

**MySQL / Redis 默认凭据**: 来源 `CubeMaster/conf.yaml:48,60`
```yaml
mysql:
  pwd: "cube_pass"        # ⚠️ 默认弱口令,生产必须改
redis:
  password: "ceuhvu123"   # ⚠️ 默认弱口令,生产必须改
```

**环境变量覆盖**:
- `CUBE_EXTERNAL_MYSQL_PASSWORD`
- `CUBE_EXTERNAL_REDIS_PASSWORD`

**部署时强制警告**: 来源 `deploy/one-click/install.sh:85-98` (`warn_default_external_credentials()`)
```bash
if [[ "$mysql_pwd" == "cube_pass" || "$redis_pwd" == "ceuhvu123" ]]; then
    echo "⚠️  WARNING: Default credentials detected. Change before production!"
    exit 1  # 生产环境强制失败
fi
```

---

## 6. 速率限制

**CubeMaster 本身无应用层速率限制** — 速率限制由上游 CubeAPI (`CubeAPI/src/state.rs:42-50`) 处理。

**间接保护**:
- ttrpc 连接数限制 (操作系统层面)
- MySQL / Redis 连接池上限 (默认 100)
- 单节点最大 sandbox 数 (由 `sandboxspec` 校验)

---

## 7. 配置项

### 7.1 conf.yaml 结构

来源: `CubeMaster/conf.yaml`

```yaml
# HTTP API
http_port: 8089

# 日志
log:
  path: "/data/log/CubeMaster-dev"
  level: "info"

# 容器运行时 (containerd)
runtime:
  namespace: "cube"
  snapshotter: "overlayfs"

# MySQL
mysql:
  host: "127.0.0.1"
  port: 3306
  user: "cube"
  pwd: "cube_pass"           # ⚠️ 必改
  db: "cube_master"

# Redis
redis:
  addr: "127.0.0.1:6379"
  password: "ceuhvu123"       # ⚠️ 必改

# 调度
scheduler:
  strategy: "least-loaded"    # least-loaded / round-robin / random
  weight_cpu: 0.5
  weight_memory: 0.5

# 安全 (关键)
security:
  allowed_host_mount_prefixes:
    - "/data/shared/"
    # 不含 / 根目录
  # hotswap_reload:
  #   enabled: true  # 默认开启

# TLS (ttrpc mTLS)
tls:
  cert_file: "/etc/cubemaster/tls/server.crt"
  key_file: "/etc/cubemaster/tls/server.key"
  ca_file: "/etc/cubemaster/tls/ca.crt"
  verify_client: true

# Sandbox 默认参数
sandbox:
  default_timeout: 300        # 秒
  max_timeout: 86400
  default_cpu: 1.0
  default_memory: 512         # MB
  max_per_node: 100
```

### 7.2 环境变量覆盖

| 配置项 | 环境变量 |
|--------|----------|
| `mysql.pwd` | `CUBE_EXTERNAL_MYSQL_PASSWORD` |
| `redis.password` | `CUBE_EXTERNAL_REDIS_PASSWORD` |
| `http_port` | `CUBE_MASTER_HTTP_PORT` |
| `log.path` | `CUBE_MASTER_LOG_PATH` |
| `runtime.namespace` | `CUBE_MASTER_NAMESPACE` |

### 7.3 hotswap 支持

来源: `CubeMaster/pkg/base/config/config.go:1186-1193`

**支持 hotswap 的字段** (YAML 修改后无需重启):
- `security.allowed_host_mount_prefixes` ✓
- `scheduler.*` ✓
- `log.level` ✓

**不支持 hotswap** (需重启):
- `mysql.*` / `redis.*` (连接已建立)
- `http_port` (监听已绑定)
- `tls.*` (证书已加载)

### 7.4 启动示例

```bash
# 默认
./cubemaster --config /etc/cubemaster/conf.yaml

# 覆盖 MySQL 密码
CUBE_EXTERNAL_MYSQL_PASSWORD='MyStr0ng!Pass' \
CUBE_EXTERNAL_REDIS_PASSWORD='MyStr0ng!Pass' \
./cubemaster --config conf.yaml

# CLI 工具
./cubemastercli tpl create-from-image \
    --image docker.io/library/ubuntu:22.04 \
    --probe 49999 \
    --expose-port 49999 \
    --expose-port 49983
```

---

## 8. 关键文件清单

| 模块 | 路径 | 关键内容 |
|------|------|----------|
| **入口** | `CubeMaster/cmd/cubemaster/main.go` | 启动 + 优雅关闭 |
| **CLI** | `CubeMaster/cmd/cubemastercli/` | tpl/node/cluster 管理 |
| **配置** | `CubeMaster/conf.yaml` | 主配置 (~80 行) |
| **配置加载** | `CubeMaster/pkg/base/config/config.go` | Config struct + hotswap (line 1186-1193) |
| **HTTP server** | `CubeMaster/pkg/server/` | ttrpc + HTTP 路由 |
| **Sandbox service** | `CubeMaster/pkg/service/sandbox/` | sandbox_run, hostdir_mount, sandbox_info 等 10+ 文件 |
| **hostdir_mount** | `CubeMaster/pkg/service/sandbox/hostdir_mount.go:37` | `injectHostDirMounts` |
| **validateHostPath** | `CubeMaster/pkg/service/sandbox/hostdir_mount.go:114-124` | 路径白名单校验 |
| **scheduler** | `CubeMaster/pkg/scheduler/` | 节点选择算法 |
| **sandboxspec** | `CubeMaster/pkg/sandboxspec/` | 规格校验 |
| **templatecenter** | `CubeMaster/pkg/templatecenter/image/export.go` | rootless umoci |
| **templatecenter** | `CubeMaster/pkg/templatecenter/image/disk.go` | 全 root 路径 |
| **cubelet client** | `CubeMaster/pkg/cubelet/` | ttrpc 客户端 |
| **lifecycle** | `CubeMaster/pkg/lifecycle/` | 沙箱生命周期钩子 |
| **errorcode** | `CubeMaster/pkg/errorcode/` | 错误码定义 |
| **scheduler CLI** | `CubeMaster/cmd/cubemastercli/tpl/` | 模板管理 CLI |

---

## 9. 安全注意事项

### 9.1 已知实现缺陷 (来自 SVG 边界模型)

| # | 缺陷 | 位置 | 风险 |
|---|------|------|------|
| **C1** | `cube.master.*` annotation 全量透传到 shim,调用者可换内核 / 追加 cmdline | `CubeMaster/pkg/service/sandbox/util.go:680-686` | 🔴 高 — RCE 风险 |
| **C2** | `validateHostPath` 纯字符串校验,无 inode / symlink 强制,**TOCTOU** | `CubeMaster/pkg/service/sandbox/hostdir_mount.go:114-124` | 🔴 高 — 路径穿越 |
| **C6** | 默认凭据 `cube_pass` / `ceuhvu123` 写在仓库里 | `CubeMaster/conf.yaml:48,60` | 🟠 中高 — 凭据泄露 |
| **C8** | `cfg` 重载 race (hotswap 期间新请求用旧配置,旧请求用新配置) | `CubeMaster/pkg/base/config/config.go:1186-1193` | 🟡 中 — 配置不一致 |

### 9.2 默认凭据警告

来源: `CubeMaster/conf.yaml:48,60` + `deploy/one-click/install.sh:85-98`

```yaml
mysql:
  pwd: "cube_pass"          # ⚠️ 默认
redis:
  password: "ceuhvu123"      # ⚠️ 默认
```

部署脚本 `install.sh` 在检测到默认值时输出 WARNING 并在生产模式 exit 1。

**修复**:
1. 改 conf.yaml 显式密码
2. 用环境变量 `CUBE_EXTERNAL_MYSQL_PASSWORD` / `CUBE_EXTERNAL_REDIS_PASSWORD`
3. 启用 TLS + 密码轮换策略
4. 外部部署用 vault / secret manager

### 9.3 host-mount 安全风险 (C2 详解)

**当前校验**:
```go
cleaned := filepath.Clean(hostPath)  // 字符串清理
if cleaned == "/" { return error }
if strings.HasPrefix(cleaned, prefix) { return nil }  // 前缀匹配
return error
```

**已知问题**:
1. **TOCTOU**: 校验在 `CreateSandboxRequest` 收到时,VM 启动在几秒后。期间 host 路径可能被攻击者替换为符号链接 → 校验时 `/data/shared/x` 是普通目录,启动时变成 `/etc/passwd` 的符号链接
2. **无 inode 检查**: 不验证路径是否真为目录而非符号链接
3. **无 owner / mode 检查**: 不验证 host 路径的权限,可能被任意用户写入
4. **mount propagation**: 未配置 rshared / rslave,可能导致 sandbox 内挂载传播到 host
5. **空 hostPath 处理**: 需检查是否对 `""` / 相对路径有保护 (commit 5c7025f 已扩展测试,但运行时校验可能不完整)

### 9.4 annotation 透传 (C1 详解)

来源: `CubeMaster/pkg/service/sandbox/util.go:680-686`

```go
// 当前: 把 request.Annotations 全量透传给 shim
func buildSandboxRequest(req *CreateSandboxRequest) *runtime.CreateSandboxRequest {
    return &runtime.CreateSandboxRequest{
        // ... 其他字段
        Annotations: req.Annotations,  // ⚠️ 全量透传
    }
}
```

**风险**:
- `cube.master.*` 前缀的 annotation 可被客户端设置 (如果上游未过滤)
- `cube.vm.kernel.path` 可被改成恶意 kernel
- `cube.vm.kernel.cmdline.append` 可追加任意 cmdline 参数
- 这些会被 CubeShim / cube-hypervisor 直接接受

**修复方向**: 应该有白名单 (允许透传的 annotation key) + 黑名单 (cube.master.* 必须由 CubeMaster 内部设置)

### 9.5 调度器信任

CubeMaster scheduler 选择 Cubelet 节点时:
- 无 sandbox 隔离检查: 同节点上多 sandbox 共享资源 (cgroup / network namespace)
- 无 tenant affinity: 同租户 sandbox 可能被调度到不同节点 → guest-to-guest 攻击面增加
- 无 zone / region 感知: 不支持跨可用区调度

### 9.6 数据库安全

**MySQL**:
- 默认 pwd `cube_pass` (C6)
- 无强制 TLS 连接 (取决于 MySQL 服务器配置)
- 凭据轮换需手动改 conf.yaml (不支持运行时)
- sandbox 状态存储无加密 (sensitive metadata 可能泄露)

**Redis**:
- 默认 password `ceuhvu123` (C6)
- 无 ACL 配置 (默认 sandbox 可读写所有 key)
- TTL safety: Redis key 自动过期 (设计安全)
- namespace: 应该是 cube:* 但需确认

---

## 10. 与 SVG 边界模型的关系

CubeMaster 是 SVG 中 **T2 (Operator Trust)** 真边界的核心执行点:

| SVG 边界 | CubeMaster 中的对应 |
|----------|------------------|
| T2 (Operator Trust) | conf.yaml 加载 + 镜像处理 + hotswap |
| L2 (控制面域) | 整个 CubeMaster 服务 |
| L3 (host 进程域) | CubeMaster 进程本身 (seccomp + cap drop) |
| L6 (存储域) | `hostdir_mount.go` validateHostPath |
| L7 (可观测性域) | 配置变更审计 + 模板中心日志 |

详细边界视角见 [security-boundaries/T2-operator-trust.md](../安全边界/T2-operator-trust.md)。

---

## 11. 总结:安全设计权衡

1. **几乎不鉴权**: CubeMaster 假设上游 (CubeAPI) 已鉴权,自身不重复 — 简化设计,但任一上游绕过即失控。
2. **字符串级路径校验**: `filepath.Clean` 简单有效,但无 inode 检查 → 已知 TOCTOU 风险 (C2)。
3. **annotation 透传**: 全量透传灵活但危险 (C1),应有白名单。
4. **默认凭据暴露**: `cube_pass` / `ceuhvu123` 写在 conf.yaml,部署时强制警告但仍需人工检查。
5. **hotswap 无签名**: conf.yaml 修改立即生效,无审计 + 无签名校验 → 配置注入风险 (C8)。
6. **MySQL/Redis 无强制 TLS**: 默认明文连接 (取决于服务端配置),凭据泄露面扩大。
7. **scheduler 无 tenant 隔离**: 多租户 sandbox 可调度到同节点,扩大 guest-to-guest 攻击面。

---

## 12. 学习路线建议

| Phase | 重点研读章节 |
|-------|-------------|
| **Phase 0** (控制面架构) | §2 架构 + §3 处理流程 |
| **Phase 1** (配置 + 安全) | §7 配置项 + §9 安全注意事项 |
| **Phase 2** (host-mount) | §3.2 host-mount 子流程 (commit 5c7025f) |
| **Phase 3** (调度 + 持久化) | §3.1 + scheduler/ + MySQL/Redis 集成 |
| **Phase 4** (修复 C1/C2/C8) | §9.1-9.4 + 配套提交 |

---

## 附录:与 CubeAPI 的对比

| 维度 | CubeAPI | CubeMaster |
|------|---------|------------|
| 语言 | Rust | Go |
| 公网暴露 | ✅ (`:3000`) | ❌ (`:8089` 仅内部) |
| 鉴权 | unified_auth (delegated) | 无 (信任上游) |
| 速率限制 | token bucket (100 req/s) | 无 (上游限制) |
| 核心职责 | HTTP 网关 | 控制面 + 调度 |
| 上游 | 客户端 (Internet) | CubeAPI |
| 下游 | CubeMaster (ttrpc) | Cubelet (ttrpc) + MySQL + Redis |
| 关键安全 | auth callback 默认关闭 (C4) | host-mount 字符串校验 (C2) |
| 默认凭据 | 无 | MySQL `cube_pass` / Redis `ceuhvu123` (C6) |
| 配置热重载 | 无 | hotswap (C8) |
| 多租户隔离 | API key | containerd namespace |