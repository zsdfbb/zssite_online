---
title: "3.7 vsock 主机-客机通信"
date: 2026-07-04T14:31:07+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/23-vsock-host-guest/"
summary: "vsock 是 Linux 内核内嵌的 VM 主机-客机通信机制。它有一个独立地址族(AFVSOCK),使用 CID(contiguous identifier) 标识 host / guest:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 3.7 vsock 主机-客机通信

## 机制原理

**vsock** 是 Linux 内核内嵌的 VM 主机-客机通信机制。它有一个独立地址族(`AF_VSOCK`),使用 **CID(contiguous identifier)** 标识 host / guest:

- host CID 是常量(`VMADDR_CID_HOST = 2`)
- guest CID 一般为 2(也是常量)
- port 由应用层分配

数据通路:guest `fd=connect(VMADDR_CID_HOST, port)` → host 上 listener 收。它**不走 host 网络栈**,只在 KVM/virtio 内部流转。

CubeSandbox:

```rust
// CubeShim/shim/src/hypervisor/config.rs:355-360
fn add_vsock(id: VsockId) {
    let cfg = Utils::gen_vsock_config(&id);
    vm_cfg.vsock = Some(cfg);
}
```

`CubeShim/shim/README.md:1-20` 架构图注释:"containerd → shim → cube-agent (ttrpc over vsock)"

通信采用 **ttrpc**(轻量 gRPC),CubeSandbox 选 vsock 是因为:

1. **不走 host 网络** —— sandbox 内的 TCP 不能直接触达 host 网络栈,网络层攻击面天然没有
2. **低延迟** —— vsock 是 virtio 直通,延迟 < 1ms,适合高频控制
3. **作用域固定** —— vsock 只在 VM 内可见,host 上其他进程无法 connect 到 guest 的 vsock(只能 host ↔ guest 双向)
4. **无 firewall 配置烦恼** —— 不必配 iptables / 安全组

## 为什么 CubeSandbox 使用它

- **不能信任 sandbox 内 TCP** —— sandbox 攻击者可以在 NET NS 内自建 socket,**不能信任任何 host 上基于 TCP 的 control plane**
- **避开 sidecar trap** —— 如果改走 host network + UDS,所有 sandbox 共享一套 socket,反而比 tcp 风险更高
- **接口稳定** —— vsock 是 Linux kernel ABI,不会因 K8s 网络抖动导致 sandbox 启停失败

## 如何使用 / 配置

#### 通常自动生成

CubeShim 自动分配 vsock 设备并配置 CID;shim 与 agent 通过 ttrpc over vsock 通信,**普通用户不必手动配**。

#### 看 vsock 状态

```bash
# host 上看 vsock 工具 (内核 v5.5+)
ss -f vsock

# guest 内执行
nc -v --vsock=2:8000
```

#### 测试数据通路(host ↔ guest)

```bash
# host (作为 listener)
nc -l 127.0.0.1:0  # for ref
# 对应 host 端 vsock listener
nc --vsock -l 0 8000

# guest
echo "hello from guest" | nc --vsock=2 8000
```

#### ttrpc 服务定义(简化)

```protobuf
service Agent {
    rpc CreateContainer(ContainerSpec) returns (ContainerID);
    rpc StartContainer(ContainerID) returns (Empty);
    rpc StopContainer(ContainerID) returns (Empty);
    rpc ExecProcess(ExecRequest) returns (ExecResponse);
    ...
}
```

**guest agent 实现侧**

```rust
// ttrpc server 跑在 AF_VSOCK
let server = ttrpc::Server::new()
    .bind_vsock(VMADDR_CID_ANY, 8000)?;
server.register_service(AgentServer::new(...));
server.start().await?;
```

#### 通信矩阵

| 控制流 | 通路 | 协议 |
|---|---|---|
| containerd → shim | host local UDS | shim v2 rpc |
| shim → agent | **vsock (host ↔ guest)** | ttrpc |
| agent → 内部容器 | pid-1 IPC / UDS | OCI spec |

**注意**:

- **不要尝试把 shim → agent 通信迁到 TCP** —— 失去 vsock 后就要重新引入一整套 TLS / 认证 / 网络策略,得不偿失
- 并发启用多个 sandbox 时,**vsock port 不能复用** —— shim 自动分配一个,不必手工设
- 调试时 `bpftool map show` 不要尝试抓 vsock —— vsock 不在 host 网络栈,不会触发 eBPF
- 当 sandbox 内 guest kernel 选太旧(没用上 `vsock` 内核模块),agent 会启动失败 —— 注意 guest image 编译时 `CONFIG_VSOCK=y`、`CONFIG_VIRTIO_VSOCK=y`
- host kernel 同样要 `CONFIG_VSOCK=y`
