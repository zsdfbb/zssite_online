---
title: "1.7 virtio Rate Limiter (TokenBucket)"
date: 2026-07-04T14:24:56+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/07-virtio-rate-limiter/"
summary: "TokenBucket (令牌桶) 是一种经典流量整形算法:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 1.7 virtio Rate Limiter (TokenBucket)

## 机制原理

**TokenBucket (令牌桶)** 是一种经典流量整形算法:

- 桶容量 = `size`,初始装满
- 每 `refill_time` 时间往桶里加 `one_time_burst` 个令牌
- 每个 token 允许 1 次操作(或 1 字节 IO),桶空则被限流

CubeSandbox 把这算法集成进 virtio 设备的 IO 路径,作为**入站/出站限流器**,在 `RateLimiterConfig` 中:

```rust
RateLimiterConfig {
  bandwidth: TokenBucketConfig {   // 字节级限流
    size: u64,
    one_time_burst: u64,
    refill_time: u64,
  },
  ops: TokenBucketConfig {        // 操作数限流 (req/s)
    size: u32,
    one_time_burst: u32,
    refill_time: u64,
  },
}
```

它**既限制 IOPS,也限制带宽**,Qos 一栏就两个维度合设。代码位于 `CubeShim/shim/src/hypervisor/config.rs:280-300` 及 `CubeShim/shim/src/sandbox/config.rs:201-210`(对 `Fs` / `VirtioFs` 同样适用)。

## 为什么 CubeSandbox 使用它

- **抑制好资源邻居 (Noisy Neighbor)** —— 多租户 sandbox 共享 host IO,一个有压力测试脚本的 sandbox 不能"饿死"其它
- **限制勒索行为** —— 如果 sandbox 内用户运行加密磁盘读盘的勒索脚本,带宽封顶让它必须慢速走,易于检出
- **保护 host 上的 page cache** —— 大文件顺序读会让 host 直接 IO 阻塞,前置限流让后端有节奏处理
- **配合 cgroup v1 (memory / pids) 形成完整资源层** —— cpu/mem 由 KVM 限,带宽由 tokenbucket 限,操作数由 cgroup 限

## 如何使用 / 配置

#### annotation 形式

```yaml
metadata:
  annotations:
    cube.net.qos: |
      {
        "bw_size": 10485760,         # 桶 10 MiB
        "bw_one_time_burst": 5242880,# 首轮 burst 5 MiB
        "bw_refill_time": 100000,    # 100ms 补一次
        "ops_size": 200,             # 操作桶 200 ops
        "ops_one_time_burst": 100,
        "ops_refill_time": 1000      # 1s 补 100 ops
      }
```

#### 类型

```rust
pub struct Fs {
    pub rate_limiter_config: Option<RateLimiterConfig>,
    ...
}
```

**关键点**:

- `bw_*` 控制**每秒字节数**(`bw_size / bw_refill_time` = 稳态带宽)
- `ops_*` 控制**每秒操作数**
- 不填则是不限速(等价于无限大桶)

#### 验证

```bash
# 在 host 上跑 iperf 客户端,目标 sandbox 网卡 IP
iperf3 -c 169.254.68.6 -t 30 -i 1
# 期望上行带宽 ≈ bw_size / bw_refill_time
```

**实际部署经验**:

- LLM 推理 sandbox 默认:`bw_size=10MiB, bw_refill_time=100ms`(约 100MB/s 稳态),`ops_size=200`(200 IO/s)
- 做 ML 训练(频繁 checkpoint 写盘)时建议放宽到 50MB/s 稳态
- **不要同时把 bandwidth 和 ops 都设极严**——可能出现"1 byte IOPS = 0 ops" → 死锁场景
- 想测速直接 `--rate-limit=false` 关掉之后再开
