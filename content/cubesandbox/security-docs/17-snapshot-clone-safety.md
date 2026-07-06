---
title: "3.3 快照 / 克隆安全"
date: 2026-07-04T14:28:44+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/17-snapshot-clone-safety/"
summary: "CubeSandbox 的 snapshot / clone / rollback 是核心差异化特性。它基于 CubeCoW 引擎(docs/blog/posts/2026-06-03-cubesandbox-v0.3.0-snapshot"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 3.3 快照 / 克隆安全

## 机制原理

CubeSandbox 的 **snapshot / clone / rollback** 是核心差异化特性。它基于 CubeCoW 引擎(`docs/blog/posts/2026-06-03-cubesandbox-v0.3.0-snapshot.md`)实现 event-level snapshot。

**机制三个角度**:

1. **强制清理 CUBE_SYS_PATH 之外的路径**:`pub const CUBE_SYS_PATH: &str = "/usr/local/services/cubetoolbox/"` —— snapshot 仅能在白名单目录落盘
2. **类型分流**:
   ```rust
   pub enum SnapshotType {
       Full,
       Diff,
       ...
   }
   ```
3. **rate_limiter 在 snapshot 期间可用** —— 防止大规模并发 `snapshot` 行为

**关键代码**(`CubeShim/shim/src/snapshot/mod.rs`):

```rust
pub const CUBE_SYS_PATH: &str = "/usr/local/services/cubetoolbox/";

let target = path;
if !target.starts_with(CUBE_SYS_PATH) {
    bail!("snapshot path must be within {}", CUBE_SYS_PATH);
}
```

snapshot 行为相关 `force: bool`、`app_snapshot: bool`、`snapshot_type`、`memory_vol_url: Option<String>` 字段都明确列出。

调用链:`snapshot_vm(path, snapshot_type)` → `ApiRequest::VmSnapshot` 到 cloud-hypervisor。

snapshot 路径中再次 `set_runtime_seccomp_rules` 注入 `mkdir` `getsockopt` `setsockopt` 白名单,以让 shim 写出 snapshot 文件。

## 为什么 CubeSandbox 设计这个

- **path traversal 防护** —— 把 snapshot 路径限制在系统白名单,可避免 kubelet 任意路径参数对 host 系统目录的覆盖攻击
- **rate-limit 防止 burst** —— 一组 sandbox 同时跑 snapshot 时,rate_limiter 缓冲写盘压力
- **diff / full 双类型** —— Full 适合 sandbox 启动时;Diff 适合中间存档,二者合理利用可避免读全盘
- **memory_vol_url** —— memory state 可选落远程卷,减少 host 本地磁盘压力

## 如何使用 / 配置

#### 创建 Full snapshot

```bash
curl -X POST http://localhost:3000/sandboxes/{id}/snapshot \
  -H 'X-API-Key: <key>' \
  -d '{
    "path": "/usr/local/services/cubetoolbox/snapshots/box-42-full-001",
    "snapshot_type": "Full",
    "memory_vol_url": ""
  }'
```

#### 创建 Diff snapshot

```bash
curl -X POST http://localhost:3000/sandboxes/{id}/snapshot \
  -d '{
    "path": "/usr/local/services/cubetoolbox/snapshots/box-42-diff-002",
    "snapshot_type": "Diff",
    "force": false   # 即便路径中存在也允许落盘
  }'
```

#### 启用/禁用 snapshot

```yaml
metadata:
  annotations:
    cube.snapshot.disable:        "false"
    cube.appsnapshot.create:      "true"  # 启用 app-level(在沙箱内 log 应用层快照)
    cube.vm.snapshot.base.path:   "/usr/local/services/cubetoolbox/snapshots/box-42"
    cube.vm.snapshot.memory_vol_url: ""
```

#### 写盘路径检查

- **必须** 落在 `/usr/local/services/cubetoolbox/`
- 落在 `/tmp/sandbox-snapshot/` 会直接 fail-fast
- 路径外"凭空让 shim 创建文件" → 不可能

**注意**:

- snapshot 结束会调 `set_runtime_seccomp_rules` 注入额外 syscall;但一次只覆盖当前进程,下一次 shim 重启要重新注入
- 路径含空格、中文、非 ASCII 时,CubeShim 的路径 sanitize 会**自动转换**(Unicode → 全角字符 escape),调试时记得检查实际路径
- memory_vol_url 远程走的是普通 HTTP,生产环境务必启 `HTTPS` + 鉴权
- 不要让多个 sandbox 的 snapshot 同时落同一基础路径 —— Diff 增量依赖 base
