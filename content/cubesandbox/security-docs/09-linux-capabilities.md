---
title: "2.2 Linux Capabilities(细粒度)"
date: 2026-07-04T14:25:41+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/09-linux-capabilities/"
summary: "Linux 把传统 root 权限切成 40+ 个细粒度 capability(CAPCHOWN、CAPNETADMIN、CAPSYSADMIN 等)。每个进程同时持 5 个 capability set:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 2.2 Linux Capabilities(细粒度)

## 机制原理

Linux 把传统 root 权限切成 40+ 个细粒度 capability(`CAP_CHOWN`、`CAP_NET_ADMIN`、`CAP_SYS_ADMIN` 等)。每个进程同时持 5 个 capability set:

| Set | 含义 |
|---|---|
| **Permitted** | 进程可以"尝试"使用的 capability 上限 |
| **Effective** | 当前调用是否实际生效,内核只查此 set |
| **Inheritable** | exec 时新进程继承的 capability |
| **Bounding** | exec 时允许获得的 capability 上界 |
| **Ambient** | 不带任何文件 capability 即可继承的 capability |

完整实现见 `agent/rustjail/src/capabilities.rs` —— 解析 OCI spec 中的 capability 列表、编译、调用 `capset()` 写入 5 个 set。

`drop_privileges(cfd_log, caps: &LinuxCapabilities)` 这一函数确保:exec 后实际有效 capability 严格等于 spec 声明的部分,多一点都不给。

## 为什么 CubeSandbox 使用它

- **root 不是万能钥匙** —— 把 root 切成 40+ 个 cap,即使 sandbox 内某 root-only syscall 仍可调,也得保证它的 cap 是声明过的
- **可逆向约束** —— spec 声明 `CAP_NET_BIND_SERVICE` 给 sandbox,程序才能 bind 80/443;否则 bind 不上
- **与 seccomp 正交** —— seccomp 控"能调哪个 syscall",cap 控"通过 syscall 边检时还有什么权限";两者缺一不可
- **跨进程** —— ambient/inheritable 这两个 set 让 fork+exec 链上的子进程也能继承约束,不会因为一次 exec 就"逃出"sandbox

## 如何使用 / 配置

#### OCI spec(用户视角)

```json
{
  "process": {
    "capabilities": {
      "bounding":  ["CAP_NET_BIND_SERVICE"],
      "effective":  ["CAP_NET_BIND_SERVICE"],
      "permitted":  ["CAP_NET_BIND_SERVICE"],
      "inheritable": [],
      "ambient":    []
    }
  }
}
```

上面这个 spec 让 sandbox 内进程只多了 bind 80/443 的能力,其它一切皆按 no-cap。

#### 主机侧(Cubelet 编译)

`Cubelet/pkg/container/capability/capability.go` 用 `cap.Current()` 读宿主机的 capability,然后根据 OCI spec 算出"差集"再下发:

```go
specCaps := parseSpecCaps(spec.Linux.Capabilities)
hostCaps := cap.GetProc().GetAllCaps()
final := intersect(hostCaps, specCaps)
```

#### guest 落地(rustjail 侧)

```rust
drop_privileges(
    cfd_log,
    &LinuxCapabilities {
        bounding:   to_capshashset(spec.linux.capabilities.bounding),
        effective:  to_capshashset(spec.linux.capabilities.effective),
        permitted:  to_capshashset(spec.linux.capabilities.permitted),
        inheritable: to_capshashset(spec.linux.capabilities.inheritable),
        ambient:    to_capshashset(spec.linux.capabilities.ambient),
    },
)?;
```

**推荐实践**:

- **不要给 `CAP_SYS_ADMIN`** —— 等价于很多场景下的 root
- 真的需要 mount 给 `CAP_SYS_ADMIN` 时,把 spec 用 `NoNewPrivs` 包起来防止再升级
- 多 set 必须**严格包含关系**:`effective ⊆ permitted`,否则 rustjail 会拒绝启动
- `ambient` 慎用——它能跨 exec 继承,是 ansible / apt-get 工作的关键,但滥用很危险
