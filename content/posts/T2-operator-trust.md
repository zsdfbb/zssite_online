---
title: "T2 Operator Trust — 运维 → 配置 / 镜像"
date: 2026-07-06T23:18:21+08:00
draft: false
tags: ["cubesandbox", "security", "T2"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/boundaries/t2-operator-trust/"
summary: "一句话定位: 运维人员将可信根 (配置、镜像、模板、二进制) 注入 host 信任域的入口。"
cover:
  image: ""
  alt: ""
  caption: ""
---
# T2 Operator Trust — 运维 → 配置 / 镜像

> 一句话定位: 运维人员将可信根 (配置、镜像、模板、二进制) 注入 host 信任域的入口。
> 边界类型: 真边界 (信任跃迁) —— 信任起点是"运维是诚实的",但运维可能失误或被攻陷。
> SVG 位置: x=120, y=355 (左侧竖条) ,T2 米框;同时向右上方 (L2 控制面域) 和右下方 (L6 存储域) 各拉一条虚线箭头。

## 1. 边界概述

```
   ┌──────────────────────────────────────────────────┐
   │   运维 (Operator)                                │
   │   - 配置 (YAML / JSON)                           │
   │   - 镜像 (rootfs / template)                     │
   │   - 二进制 (cube-shim / cube-hypervisor)         │
   │   - Host mount 路径                             │
   └─────────────────────┬────────────────────────────┘
                         │ config.yaml / image.tar / mount path
                         ▼
            ╔═══════════════════════════════════╗
            ║   T2  Operator Trust              ║  ← 本边界
            ║   (当前缺失链:签名/cosign/CoT)   ║
            ╚═══════════════╤═══════════════════╝
                            │
                            ▼
                L2 控制面域 + L6 存储域
                (CubeMaster / CubeCoW / virtiofs)
```

**信任跃迁语义**: 进入 T2 后,配置被 CubeMaster 加载并应用到控制面,镜像被解包到 L6 存储域,二进制替换 host 进程。**T2 是"基线"边界**——所有其他边界的可信性都建立在 T2 引入的内容是可信的这一前提上。

## 2. 涉及的纵深防御层

| 层 | 名称 | 是否参与 | 在本边界的作用 |
|----|------|---------|--------------|
| L1 | WebUI 域 | ❌ | T2 走 CLI / 配置文件而非 Web UI |
| L2 | 控制面域 | ✅ | CubeMaster/conf.yaml + hotswap reload、containerd namespace、Redis TTL safety |
| L3 | host 进程域 | ✅ | 配置加载进程 (CubeMaster) 的 seccomp、二进制替换流程 |
| L4 | host 内核域 | ❌ | T2 不直接经内核 |
| L5 | guest OS 域 | ❌ | T2 不直接经 guest |
| L6 | 存储域 | ✅ | CubeCoW FICLONE/reflink、virtiofs lower_dir、`allowed_host_mount_prefixes` |
| L7 | 可观测性域 | ✅ | 配置变更审计、template center 拉取日志、hotswap reload 记录 |

## 3. 机制清单

### 3.1 L2 (控制面域)

#### 机制: CubeMaster 配置加载 (conf.yaml)

- **文件位置**: `CubeMaster/conf.yaml:1-100`
- **作用**: 主配置入口,包含 MySQL/Redis 凭据、scheduler 策略、auth callback URL、`allowed_host_mount_prefixes`
- **配置/启用**: 文件路径由 `cube_master_conf` 环境变量或 CLI flag 指定
- **与本边界的关联**: T2 的核心入口面;**生产部署必须修改默认凭据** (MySQL `cube_pass` / Redis `ceuhvu123`)

#### 机制: hotswap 配置重载

- **文件位置**: `CubeMaster/pkg/base/config/config.go:1186-1193`
- **作用**: 监听配置文件变更,部分字段支持运行时热加载,无需重启服务
- **配置/启用**: 默认开启;`hotswap` 注解标记可热加载字段
- **与本边界的关联**: T2 持续生效的通道——**当前缺失受限字段白名单与签名校验**,hotswap 期间存在 race window

#### 机制: rootless 模板导出

- **文件位置**: `CubeMaster/pkg/templatecenter/image/export.go`、`disk.go`
- **作用**: 通过 `skopeo copy` + `umoci unpack --rootless` 拉取模板镜像,自动检测环境是否 root
- **配置/启用**: 自动 (以 root 启动则非 rootless)
- **与本边界的关联**: T2 中"镜像进入 host"的官方流程,避免 root 进程拉取不可信 OCI 布局

#### 机制: containerd namespace + Redis TTL safety

- **文件位置**: `Cubelet/services/images/image_gc.go`、`Cubelet/services/cubebox/events.go`
- **作用**: 每个租户一个 containerd namespace + Redis key 前缀,TTL 自动清理过期 sandbox 元数据
- **配置/启用**: 通过 OCI spec `linux.namespaces[]` 与 Redis `EXPIRE` 命令
- **与本边界的关联**: T2 注入的"租户"语义在 L2 落地

### 3.2 L3 (host 进程域)

#### 机制: CubeMaster 进程的 seccomp + cap drop

- **文件位置**: `CubeMaster/cmd/cubemaster/main.go` (启动入口,本系列新增,原清单未列)
- **作用**: CubeMaster 进程自身受 seccomp 保护,只允许与 MySQL/Redis/gRPC 通信所需 syscall
- **配置/启用**: 编译期通过 seccomp BPF 加载
- **与本边界的关联**: 即使 T2 配置含恶意逻辑触发漏洞,CubeMaster 进程仍受 L3 约束

#### 机制: 二进制替换流程 (cube-shim / cube-hypervisor)

- **文件位置**: `Cubelet/services/cubebox/local.go` (`defaultShimPath = "/usr/local/services/cubetoolbox/cube-shim/bin/containerd-shim-cube-rs"`)
- **作用**: Cubelet 通过固定路径启动 shim binary,**没有完整性校验或签名检查**
- **配置/启用**: 路径硬编码,运维替换 binary 后需重启 Cubelet
- **与本边界的关联**: T2 的 binary 注入面——**当前缺失**:cosign 签名验证、SHA256 pinning

### 3.3 L6 (存储域)

#### 机制: CubeCoW (Copy-on-Write 池)

- **文件位置**: `cubecow/` (Cargo crate) ,`docs/blog/posts/2026-06-03-cubesandbox-v0.3.0-snapshot.md`
- **作用**: event-level snapshot / clone / rollback 引擎,基于 ext4 `FICLONE` / `reflink`
- **配置/启用**: sandbox 创建时通过 `cube.snapshot.disable` 等 annotation 控制
- **与本边界的关联**: T2 镜像进入 L6 后,CubeCoW 保证多 sandbox 共享底层 rootfs 而不复制

#### 机制: virtiofs lower_dir 注入

- **文件位置**: `CubeShim/shim/src/container/rootfs.rs:7-25` (`OverlayInfo { virtiofs_lower_dir: Vec<String> }`)
- **作用**: 容器内 rootfs 通过 virtiofs (tag `cubeShared`) 共享,overlay 下层目录来自多个 virtiofs lower
- **配置/启用**: 通过 sandbox spec 的 `cube.rootfs.info` annotation
- **与本边界的关联**: T2 注入的镜像在 L6 以 virtiofs 形式暴露给 guest

#### 机制: `allowed_host_mount_prefixes` (host-mount allowlist)

- **文件位置**: `CubeMaster/pkg/service/sandbox/hostdir_mount.go:1-100`
- **作用**: host-mount 路径必须落在配置白名单(默认 `/data/shared/`),`filepath.Clean` 中和 `..` 路径穿越,显式禁止根 `/`
- **配置/启用**: `CubeMaster/conf.yaml` 的 `allowed_host_mount_prefixes` 字段 (可多前缀)
- **与本边界的关联**: T2 的 host 路径注入面——**是最近一次安全提交 (commit 5c7025f,2026-07-05) 的核心**

### 3.4 L7 (可观测性域)

#### 机制: 配置变更审计

- **文件位置**: `CubeMaster/pkg/base/config/config.go` (本系列新增,原清单未列) —— hotswap reload 时记录旧值/新值
- **作用**: 任何配置字段热加载时,记录到 `/data/log/CubeMaster-dev/`,便于追溯配置漂移
- **配置/启用**: 默认开启
- **与本边界的关联**: T2 的可观测性落点;**当前缺失**:签名校验,审计只能事后追溯

#### 机制: template center 拉取日志

- **文件位置**: `CubeMaster/pkg/templatecenter/image/export.go` (本系列新增,原清单未列)
- **作用**: 记录每个模板的源 registry、digest、pull 时间
- **配置/启用**: 默认开启
- **与本边界的关联**: T2 中镜像进入 L6 的可追溯记录;**当前缺失**:digest pinning 与 cosign 验证

## 4. 关键交互

- **数据流入自**: 运维 (Operator) —— 通过 `kubectl apply` / `cube-cli` / `vi conf.yaml` / `docker load` / `scp binary`
- **数据流出到**:
  - **T1 (CubeAPI ingress)**: 运维修改 `auth_callback_url` 后,T1 的 auth 行为改变
  - **T3 (KVM CORE)**: 运维替换 cube-shim/cube-hypervisor binary 后,T3 进程本身改变;运维调整 kernel cmdline 后,T3 启动行为改变
  - **T4 / T5**: 通过修改 L4 eBPF 策略 / CubeProxy 配置间接影响
- **同信任域 L 层依赖**: L2 (配置加载) ← T2 注入 → L6 (镜像落盘) ← T2 注入 → L3 (binary 替换) ← T2 注入 → L7 (审计)

## 5. 设计权衡

1. **为什么 T2 当前是 5 真边界中"最薄弱"的**: SVG 在 T2 框内明确列出"当前缺失:config 签名 / 镜像签名 (cosign) / template CoT / hotswap 受限字段"。这是因为 CubeSandbox 假设部署环境是受控的内网,运维是可信的——但一旦运维失陷,所有下游边界都将被绕过。这是 SVG 把 T2 列为真边界并标 ⚠️ 的原因。
2. **为什么 rootless 模板导出是默认**: 当 CubeMaster 以非 root 运行时,`umoci --rootless` 避免 root 进程直接拉取不可信 OCI 布局。代价是 rootfull 路径在以 root 启动时仍可用——通过代码注释显式标注,运维必须明确选择。
3. **为什么 host-mount allowlist 在 T2 而不是 T3**: host-mount 路径是配置在 conf.yaml 中,本质是"运维告诉 CubeMaster 哪些 host 路径可挂载"。它的边界入口是 T2,校验逻辑在 L6 的 hostdir_mount.go。commit 5c7025f 把 T2 的可信根与 L6 的执行面通过 `allowed_host_mount_prefixes` 串起来。
4. **为什么 hotswap 没有签名但有审计**: 当前设计假设配置文件本身受文件系统权限保护 (chmod 600,仅 root 可写),审计日志足够事后追溯。但**生产部署应叠加**: (a) 把 conf.yaml 放进 git 仓库走 PR 审核; (b) 在 reload 前对比 SHA256 hash; (c) 在 reload 时强制 cAdvisor 进程暂停。
5. **为什么 L4 在 T2 不参与**: 配置文件经 L2 加载,不经网络 syscall 直接进入 L4。运维操作经过 ssh/sudo 走的是 host 本地认证路径,属于 host 信任域内的常规操作,不属于本系列文档边界 (那是 host OS 自身的安全模型)。