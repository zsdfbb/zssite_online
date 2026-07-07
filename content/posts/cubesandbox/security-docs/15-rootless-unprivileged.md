---
title: "3.1 rootless / 非特权运行"
date: 2026-07-04T14:28:01+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/15-rootless-unprivileged/"
summary: "Rootless 模式指 sandbox 的\"模板导出\"或\"镜像拉取\"过程不需要以 root 用户运行。CubeSandbox 通过 umoci --rootless 来导出 OCI 镜像,不需要 root 创建文件系统。"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 3.1 rootless / 非特权运行

## 机制原理

**Rootless 模式**指 sandbox 的"模板导出"或"镜像拉取"过程不需要以 root 用户运行。CubeSandbox 通过 `umoci --rootless` 来导出 OCI 镜像,不需要 root 创建文件系统。

关键代码位置:

- `docs/changelog/v0.4.0.md`:
  ```text
  Daemonless export path (#492, #506):
  When skopeo and umoci are available on the CubeMaster node, template images are pulled
  via `skopeo copy` into a local OCI layout and unpacked with `umoci unpack --rootless`
  ```
- `CubeMaster/pkg/templatecenter/image/export.go`:注释:
  ```go
  // Only pass --rootless when we are NOT running as root.
  // --rootless makes...
  ```
- `CubeMaster/pkg/templatecenter/image/disk.go`:同上,镜像相关
- `agent/rustjail/src/validator.rs`:实现 rootless(euid/cgroup)检测,要求 user namespace + uid/gid mapping

agent 内部 `CreateOpts` 中:

```rust
pub struct CreateOpts {
    pub rootless_euid: Option<u32>,    // 真实运行用户 UID
    pub rootless_cgroup: Option<bool>, // cgroup 是否自动归属当前 user
    ...
}
```

## 为什么 CubeSandbox 使用它

- **少给 root 一次,少一次提权机会** —— OCI 镜像 export 只需要建文件系统,普通用户做即可
- **多用户部署** —— 在共享工作站上,开发者拉镜像不必提 sudo
- **对齐 OCI 行业方向** —— docker, podman 默认往 rootless 走
- **agent 仍以 root 跑**(因为它要管 cgroup / netns),但模板导出 chain 已经不再依赖 root——这是更细粒度的最小特权

## 如何使用 / 配置

#### 自动识别

```bash
# 如果以 root 运行,走 --root-full
$ id -u
0
# agent / umoci 走 rootful 路径

# 如果以普通用户运行
$ id -u
1000
# 自动走 --rootless 路径
```

#### 手动启用 rootless

```yaml
# CubeMaster 启动时以非 root 身份
sudo -u cubeops ./cube-master --rootless

# 启用后,profile 默认会变为 /etc/subuid /etc/subgid
cat /etc/subuid
cubeops:100000:65536   # cubeops 在 host 上对应到 100000..165536 UID 范围
```

#### validate 阶段

```rust
// agent/rustjail/src/validator.rs:200-260
fn check_rootless_prereq(opts: &CreateOpts) -> Result<()> {
    if opts.rootless_euid.is_some() {
        require_user_ns()?;
        require_uid_gid_mapping(opts.rootless_euid.unwrap())?;
        require_subuid_subgid(opts.rootless_euid.unwrap())?;
    }
    Ok(())
}
```

**注意**:

- **rootless 不代表无特权** —— unshare user namespace 失败时(内核 `CONFIG_USER_NS=n` 或 `kernel.user_max_user_namespaces=0`),导出整条链会失败
- CubeMaster 切换 rootless 后,**部分命令需要 O_NOFOLLOW 行为检测**,代码中要 catch `ELOOP`
- subuid / subgid 的范围大小 = 一个普通用户能起的最大 sandbox 数。`65536` 是常见默认,但超出后会触发 `setgroups: Resource temporarily unavailable`
- 长期使用 rootless 时,建议 supervisor 用 `systemd --user` 而不是 cgroup v1 system.slice,在 rootless user namespaces 内看不到 system.slice
