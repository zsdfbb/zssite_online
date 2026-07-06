---
title: "2.4 Namespaces (PID/Net/Mount/UTS/IPC/User/Cgroup)"
date: 2026-07-04T14:26:25+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/11-namespaces/"
summary: "Linux namespace 把\"全局唯一资源\"切成多个互不可见的实例。CubeSandbox 用到的共有 7 类:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 2.4 Namespaces (PID/Net/Mount/UTS/IPC/User/Cgroup)

## 机制原理

Linux namespace 把"全局唯一资源"切成多个互不可见的实例。CubeSandbox 用到的共有 7 类:

| 类型 | 内容 | CubeSandbox 中的角色 |
|---|---|---|
| `pid` | 进程 ID | 由 VMM/agent 托管,cgroup-ns **强制清除** |
| `net` | 网络设备、IP、route | 由 VMM 通过 TAP/TPROXY 提供,**不**让 sandbox 自建 |
| `mount` | 挂载点 | sandbox 内自管,但 mount 路径走 virtiofs |
| `uts` | hostname / domainname | sandbox 内自管 |
| `ipc` | SYSV IPC / POSIX mq | sandbox 内自管 |
| `user` | UID/GID 映射 | rootless 模式下必备 |
| `cgroup` | cgroup 视图 | 由 VMM 托管,**强制清除** |

`CubeShim/shim/src/container/mod.rs:285-302` 这段代码是命名空间协商的关键:

```rust
for ns in spec.get_linux().get_namespaces() {
    if ns.field_type == common::NS_CGROUP
        || ns.field_type == common::NS_NET
        || ns.field_type == common::NS_PID
    {
        continue;            // host-managed by VMM, ignore user-declared
    }
    nss.push(n.clone());
}
spec.mut_linux().set_namespaces(nss.into());
```

也就是说 **CGROUP/NET/PID 这三种 namespace 由 host-side VMM 管理**,sandbox 内即使在 OCI spec 写了也不生效——避免"双 namespace"撞车。

`agent/src/namespace.rs` 显式 unshare IPC/UTS/PID:

```rust
nix::sched::unshare(CloneFlags::CLONE_NEWIPC | CloneFlags::CLONE_NEWUTS | CloneFlags::CLONE_NEWPID)?;
```

`agent/rustjail/src/validator.rs` 负责校验 user/mount namespace 的合法性,不允许乱开。

## 为什么 CubeSandbox 这样设计

- **NET/PID/CGROUP 由 VMM 托管** —— 否则 sandbox 自建 NET 后,TAP/sidecar 都失效,网络隔离机制(后面 18/19 章节)全部不成立
- **IPC/UTS 由 sandbox 自管** —— 这些没什么全局安全相关性,放给 sandbox 提高兼容性
- **mount 必须严格校验** —— mount namespace 错乱会让 sandbox 看不到外面真实路径,严重影响调试,但放 too open 又危险
- **user ns 用于 rootless** —— 让非 root 用户也能跑 sandbox,但要求严格的 UID/GID mapping

## 如何使用 / 配置

#### OCI spec 里可填的 namespace

```json
{
  "linux": {
    "namespaces": [
      { "type": "pid" },   // ★ 被 CubeShim 丢弃
      { "type": "network" },// ★ 被 CubeShim 丢弃
      { "type": "ipc" },
      { "type": "uts" },
      { "type": "mount" },
      { "type": "user", "uidMappings":[{"container_id":0,"host_id":100000,"size":65536}] },
      { "type": "cgroup" } // ★ 被 CubeShim 丢弃
    ]
  }
}
```

注:带 ★ 的三项会被 shim 过滤。如果用户在 spec 里写了,会被静默忽略,但不进 spec.rejected——debug 时可能误以为生效。

#### agent 校验函数

```rust
// agent/rustjail/src/validator.rs:23-130
fn contain_namespace(spec: &Spec) -> Result<()> { ... }
fn usernamespace(spec: &Spec) -> Result<()> { ... }   // 检查 uid/gid mapping 在 [0, 2^32)
fn cgroupnamespace(spec: &Spec) -> Result<()> { ... } // 必须带 mapping
```

#### Kernel 支持确认

```
CONFIG_NAMESPACES=y
CONFIG_USER_NS=y
```

`/proc/sys/user/max_user_namespaces` 在 host 上调大一些(rootless 必要)。

**注意**:

- **`net`/`pid`/`cgroup` namespace 在 OCI spec 中**不要**提交** —— 即使提交也是被 shim 强删,影响可读性
- sandbox 内若需要 hosts 文件改 hostname,正常写在 OCI 即可
- rootless 启动时,如果 host `max_user_namespaces=0`,所有 rootless 调用直接失败
- mount namespace 内对挂载点的能力用 `CAP_SYS_ADMIN` 管控,前面 2.2 章节有专门约束
