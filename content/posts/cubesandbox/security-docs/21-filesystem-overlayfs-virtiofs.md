---
title: "3.5 文件系统隔离 (overlayfs / virtiofs)"
date: 2026-07-04T14:30:15+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/21-filesystem-overlayfs-virtiofs/"
summary: "CubeSandbox 的文件系统层级是两层:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 3.5 文件系统隔离 (overlayfs / virtiofs)

## 机制原理

CubeSandbox 的文件系统层级是**两层**:

```
guest kernel
  ├ /dev/pmem0   (DAX virtio-pmem, rootfs, RO)
  └ /run/cube-containers/shared/
        ├─ cubeShared/   (virtiofs tag, host 共享目录)
        └─ containers/<box-id>/
                └ overlay mount (lower=[lower_dir_virtiofs], upper=temporary)
```

#### 关键结构

```rust
// CubeShim/shim/src/container/rootfs.rs:7-25
pub struct OverlayInfo {
    pub virtiofs_lower_dir: Vec<String>,
}
pub struct MountInfo {
    pub virtiofs_id: String,
    pub virtiofs_source: String,
    pub container_dest: String,
    pub r#type: String,
    pub options: String,
}
pub struct EroImage {
    pub path: String,
    pub lower_dir: String,
}
```

```rust
// CubeShim/shim/src/container/rootfs.rs:34-60
fn fix_virtiofs(&self) {
    // 拼接 guest 内部路径 /run/cube-containers/shared/containers/<box-id>/...
}
```

#### mount flag

`agent/src/mount.rs:30-50` 清单含 `MS_NOSUID`、`MS_NODEV`、`MS_NOEXEC`:

- `MS_NOSUID` —— 禁用 suid 位解释
- `MS_NODEV` —— 不解析 device 文件
- `MS_NOEXEC` —— 不允许此 mount 上有可执行文件(对 LLM 镜像重要)

#### 缓存策略

```rust
// CubeShim/shim/src/sandbox/config.rs:35-37
pub const SHARE_CACHE_ALWAYS: i32 = 1;
pub const SHARE_CACHE_NEVER: i32 = 2;
```

控制 virtiofs cache 共享。

## 为什么 CubeSandbox 使用它

- **DAX virtio-pmem** —— rootfs 直接 map 到 host 大页,启动极快,接近"全 RAM rootfs"的体验;`ro` 标则禁止 guest 写
- **virtiofs 共享** —— 多 sandbox 共享同一 host 目录(代码仓库、模型权重)无需复制
- **overlayfs on top** —— sandbox 在 upper 层任意修改,销毁时 upper 一扔不会污染原镜像
- **`MS_NOSUID/NODEV/NOEXEC`** —— 在 sandbox 内挂载任何外部 mount 时,几乎所有可能被滥用的特性都关了

## 如何使用 / 配置

#### annotation

```yaml
metadata:
  annotations:
    cube.virtiofs: |
      [
        {
          "tag": "cubeShared",
          "source": "/var/lib/cubelet/sandboxes/box-42/rootfs",
          "cache": "always"
        }
      ]
    cube.rootfs.info: |
      {
        "overlay_lower_dirs": ["/run/cube-containers/shared/cubeShared"],
        "overlay_upper_dir": "/var/lib/cubelet/sandboxes/box-42/upper",
        "overlay_work_dir": "/var/lib/cubelet/sandboxes/box-42/work"
      }
```

#### OCI spec 挂载点

```json
{
  "mounts": [
    {
      "destination": "/workspace",
      "source": "cubeShared",
      "type": "bind",
      "options": ["rw", "nosuid", "nodev", "noexec"]
    }
  ]
}
```

#### 缓存策略选择

| 场景 | cache | 风险 |
|---|---|---|
| 仓库代码(只读共享) | `always` | host 端改了文件,vm 不会立刻看到 |
| 日志写盘 | `never` | VM 写立刻落 host,数据一致 |
| 模型权重 | `always` | 几十 GB,sandbox 端不必 dup |
| 配置 / secrets | `never` | 防止缓存炸出 host panic |

#### 验证

```bash
# host 上
ls /var/lib/cubelet/sandboxes/box-42/upper  # 应该只有 sandbox 进程能写
# sandbox 内
mount | grep workspace
# 输出:
# cubeShared on /workspace type fuse ... rw,nosuid,nodev,noexec
```

**注意**:

- **upper / work 一定要在同一文件系统**(都是 overlayfs 强约束)
- `cache: always` 在 sandbox 死锁或断电时可能内容不一致 — 对数据完整性重要场景务必 `never`
- source 路径**不要**包含敏感数据目录,例如 `/etc` 或 `/home/admin/.ssh`——virtiofs 上层没有正经隔离,sandbox 内实际上能读
- 同名 virtiofs tag 冲突时,所有 sandbox 看到的目录一致 — 这是 LLM 多 agent 共享 workspace 的便利,但要防止 secrets 共享
- share cache 时多 sandbox 同时修改同文件可能 data race;不是 POSIX 强约束就最好 race-free 改造上层
