---
title: "3.8 no_reaper / no_sub_reaper"
date: 2026-07-04T14:31:29+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/24-no-reaper/"
summary: "Linux 进程树的\"reaper\"机制决定:谁负责回收\"孤儿进程\"(parent 死掉但子进程还活着)。在 Linux 中:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 3.8 no_reaper / no_sub_reaper

## 机制原理

Linux 进程树的"reaper"机制决定:谁负责回收"孤儿进程"(parent 死掉但子进程还活着)。在 Linux 中:

- **PID 1 (init)**:进程死后其孤儿进程挂到 PID 1
- **subreaper**:`prctl(PR_SET_CHILD_SUBREAPER)` 后,它就成"局部 init"

Container default 是期望"container 内 PID 1 reap 内部所有孤儿",**但绝不能让 host 上 PID 1 reap sandbox 内的进程**,否则会出现 sandbox 内 zombie 跑回 host 上 PID 1 名下。

CubeShim 在 `CubeShim/shim/src/main.rs:17-22` 显式:

```rust
let c = Config {
    no_reaper:        true,
    no_setup_logger:  true,
    no_sub_reaper:    true,
    ..Default::default()
};
```

即:

- **no_reaper** —— CubeShim 不接管任何 sandbox 内进程的 reaping
- **no_sub_reaper** —— CubeShim 自己也不是 subreaper,所以孤儿会**逐级上抛**到 host 上 PID 1(由 host 上 PID 1 reap),符合 KVM 隔离

## 为什么 CubeSandbox 这样设计

- **不能让 host 上 PID 1 取得 sandbox 进程信息** —— sandbox 内 forkbomb 后,大量 zombie 推到 host PID 1,反而要 host 上 systemd 收尸
- **不能让 sandbox 把 shim 当 subreaper** —— 攻击者 fork 进程后自杀让子进程"挂"在 shim,获 shim 资源视图
- **每个 microVM 在 guest kernel 内有自己 PID 1** —— 孤儿都是 guest 内 reap;vsock 不在 host 网络,但 PID 仍属 host 视角
- **配置简化,debug 友好** —— 不带 reaper/subreaper 的行为,`ps` 看到的就是真实生命周期

## 如何使用 / 配置

#### 硬编码

这是默认行为,运维 **不需要** 配置。

#### 验证

```bash
# 查 CubeShim 进程的 status
cat /proc/$(pidof containerd-shim-cube-rs)/status | grep -i "Cap\|Reaper"

# 期望:
# CapBnd ...
# 没 Pid (subreaper) = 0
```

#### 验证孤儿进程归属

```bash
# 在 sandbox 内 fork + kill parent
agent$ (sh -c "sleep 999 & pkill -P $$ sh"; cat)

# 在 host 上
ps -o pid,ppid,stat,command -e | grep sleep

# 期望这个 sleep 的 PPID = host 上 systemd 1 (= 1)
# 而非 CubeShim 进程
```

**调试技巧**:

- 想用 rustjail/seccomp filter 防止 forkbomb?用 cgroup `pids.max`(10 号文章)即可
- 想 trace 进程 ifdepid?用 cgroup freezer——冻结后所有进程都不会消亡
- 想在 sandbox 内看到自己 zombie 累积?只有 guest kernel PID 1 会 reap,所以 sandbox 内不可观测到 host 的 PPID

**注意**:

- **不要尝试把 no_reaper 改成 false** —— 它直接破坏沙箱隔离边界
- 这一项是硬编码,但万一你在做 fork,在 `cubeHalt`/`cubeKill` 时它就发挥作用,会向上抛 SIGCHLD
- 当 `containerd-shim-cube-rs` 出现 PPID 是自己的睡眠进程,大概率是 debug 模式下临时改过这个配置
- 若 containerd 上游有 pid 1 reaper 的兼容 patch,在排查系统 init 时务必验证下
