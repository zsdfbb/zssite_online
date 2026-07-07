---
title: "2.1 seccomp / seccomp-bpf (三层部署)"
date: 2026-07-04T14:25:22+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/08-seccomp-three-layers/"
summary: "seccomp 是 Linux 内核的 syscall 过滤机制:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 2.1 seccomp / seccomp-bpf (三层部署)

## 机制原理

**seccomp** 是 Linux 内核的 syscall 过滤机制:

- 通过 BPF (`Berkeley Packet Filter`) 程序在内核入口对每次 `syscall` 调用做检查
- 拒绝/记录/允许,默认动作是 `SECCOMP_RET_KILL_PROCESS`(整个进程死掉)
- 严格白名单模式:只声明允许的 syscall,其它一律拒绝——"看不到的就不存在"

CubeSandbox 的 seccomp 在**三个进程级部署**:

```
┌──────────────────────────────────────────┐
│ 1) cloud-hypervisor 进程 (host)         │  2.1.1
│    - 5 类线程规则(Api/SignalHandler/...) │
│    - 默认 KillProcess                    │
├──────────────────────────────────────────┤
│ 2) CubeShim 运行时 (host)               │  2.1.2
│    - 注入额外 syscall 到白名单          │
├──────────────────────────────────────────┤
│ 3) Guest Agent (guest 内)               │  2.1.3
│    - libseccomp 解析 OCI LinuxSeccomp    │
│    - flags: TSYNC / LOG / SPEC_ALLOW    │
└──────────────────────────────────────────┘
```

### 2.1.1 VMM 进程 (host)

入口:`hypervisor/vmm/src/seccomp_filters.rs`。它把进程按线程类型分别聚合规则:

```rust
fn thread_rules(&self) -> Vec<(String, SeccompFilter)> {
    let mut vec = vec![
        ("api".to_string(), self.api_thread_rules()),
        ("vmm".to_string(), self.vmm_thread_rules()),
        ("vcpu".to_string(), self.vcpu_thread_rules()),
        ("signalhandler".to_string(), self.signal_handler_thread_rules()),
        ("pty_foreground".to_string(), self.pty_foreground_thread_rules()),
    ];
    // virtio 各设备有自己专属规则
    virtio_device_thread_rules(...)
    ...
}
```

CLI:`--seccomp true|false|log`

### 2.1.2 CubeShim 运行时 (host)

shim 通过 `cube_hypervisor::set_runtime_seccomp_rules(...)` 给 VMM 注入额外 syscall 白名单:

```rust
cube_hypervisor::set_runtime_seccomp_rules(vec![
    (libc::SYS_mkdir, vec![]),
    (libc::SYS_getsockopt, vec![]),
    (libc::SYS_setsockopt, vec![]),
    (libc::SYS_faccessat2, vec![]),
]);
```

快照代码也调用一次,加 `mkdir, getsockopt, setsockopt` 给快照路径用。

### 2.1.3 Guest Agent (guest 内)

`agent/rustjail/src/seccomp.rs` 实现 OCI `LinuxSeccomp` spec 解析:

```rust
fn init_seccomp(scmp: &LinuxSeccomp) -> Result<()> {
    let default_action = scmp.get_default_action(); // SCMP_ACT_KILL_PROCESS 等
    let architectures = scmp.get_architectures();  // SCMP_ARCH_X86_64 等
    let flags = get_filter_attr_from_flag(scmp.get_flags()); // TSYNC|LOG|SPEC_ALLOW
    set_ctl_nnp(false);                              // NoNewPrivileges 关闭
    ...
}
```

支持 OCI spec `flags`: `SECCOMP_FILTER_FLAG_TSYNC`(线程组同步)、`SECCOMP_FILTER_FLAG_LOG`(违规时 audit log)、`SECCOMP_FILTER_FLAG_SPEC_ALLOW`(允许架构 ABI 调用)。

## 为什么 CubeSandbox 这样部署

- **纵深防御** — 即使 hypervisor 进程被攻破,它"运行哪段 syscall"仍受 host kernel seccomp 钳制;即使 VMM 的 seccomp 被绕过,shim 一侧还能再挡一层;即使 host 两侧全破,guest 内 agent 仍受 guest kernel seccomp
- **按进程类型最小化** — VCPU 线程根本不必有 `socket()` 能力,API 线程也不必有 `io_uring_*` 能力——按 thread 类型切割白名单比"一个进程一个完整白名单"严格得多
- **guest 可按 OCI 配置** — 用户提交 OCI spec 时声明 `securityContext.seccomp`,CubeShim 把它下发到 guest 内,让 sandbox 内的 RUNTIME 也受限
- **默认 kill-process** — 这与 firecracker 的 default-deny 一致;firecracker 早期 CVE 很多就是因为"默认动作太宽松"

## 如何使用 / 配置

#### VMM 层(管理员侧)

```bash
./cube-hypervisor --seccomp log   # log 模式会把违规写到 audit log;需要 host audit=1
./cube-hypervisor --seccomp false # 关闭(仅 debug 用)
```

#### CubeShim 层(Rust 代码)

要追加 shim 需要的 syscall 时,在 `CubeShim/shim/src/hypervisor/cube_hypervisor.rs` 改:

```rust
cube_hypervisor::set_runtime_seccomp_rules(vec![
    (libc::SYS_futex, vec![ /* args filter */ ]),
]);
```

#### Guest Agent 层(用户 OCI spec)

```json
{
  "process": {
    "seccomp": {
      "defaultAction": "SCMP_ACT_ERRNO",
      "architectures": ["SCMP_ARCH_X86_64"],
      "syscalls": [
        {
          "names": ["read", "write", "exit_group"],
          "action": "SCMP_ACT_ALLOW"
        }
      ]
    }
  },
  "linux": {
    "seccomp": {
      "flags": ["SECCOMP_FILTER_FLAG_TSYNC", "SECCOMP_FILTER_FLAG_LOG"]
    }
  }
}
```

**注意**:

- **不要把默认动作改为 `SCMP_ACT_LOG` 或更松的 `ALLOW`**——`LOG` 仅记录不阻断,与 `seccomp=true` 矛盾
- `flags` 中包含 `LOG` 时,**host 内核必须 `audit=1`**,否则 log 不会落
- 调试期间建议:`--seccomp log` + host `audit=1`,跑一晚上沙箱流量看哪些 syscall 违规,再回到 `true`
- 升级 cloud-hypervisor 时,务必同步检查 `seccomp_filters.rs` 中新增 syscall 是否需注册
