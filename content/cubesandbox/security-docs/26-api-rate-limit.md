---
title: "4.2 per-API-key 速率限制"
date: 2026-07-04T14:32:07+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/26-api-rate-limit/"
summary: "CubeSandbox 在 API 层用了 token bucket 限流,按 API key 维度分配独立配额。每条请求到来:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 4.2 per-API-key 速率限制

## 机制原理

CubeSandbox 在 API 层用了 **token bucket 限流**,按 **API key** 维度分配独立配额。每条请求到来:

1. 取 credential(Bearer / X-API-Key)
2. 查一个 per-key 的 token bucket
3. 取一个 token;taken = allowed,否则 429 Too Many Requests

入口:`CubeAPI/src/state.rs:42-50`

```rust
use governor::{Quota, RateLimiter};

let quota = Quota::per_second(NonZeroU32::new(config.rate_limit_per_sec.max(1)).unwrap());
let rate_limiter = Arc::new(
    RateLimiter::keyed(quota)
);
```

`CubeAPI/src/config/mod.rs:33-37`:

```rust
#[serde(default = "default_rate_limit")]
pub rate_limit_per_sec: u32,    // 默认 100
```

限流粒度、quota 都能在 `/config` 端点查看。

## 为什么 CubeSandbox 使用它

- **公平分配** —— 一个客户不能占用大量配额,挤掉其他客户
- **防 DDoS** —— 阻止滥用 token 进行 brute force / 列表枚举
- **防止下游被打爆** —— sandbox 创建是昂贵动作,K8s API 接太多 sandbox 调度就垮
- **可观测** —— token bucket 满即 429 是结构化数字,直接画 dashboard

## 如何使用 / 配置

#### 默认配置(100 req/s 每 key)

```bash
./cube-api --rate-limit-per-sec 100
# 或
RATE_LIMIT_PER_SEC=100 ./cube-api
```

#### 提升配额(高 QPS 集群)

```bash
RATE_LIMIT_PER_SEC=2000 ./cube-api
```

#### 客户端检测 429 后行为

```python
import time, requests

def post_with_backoff(url, headers, payload, max_retries=5):
    for i in range(max_retries):
        r = requests.post(url, headers=headers, json=payload)
        if r.status_code == 429:
            retry_after = int(r.headers.get("Retry-After", "1"))
            time.sleep(retry_after * (1 + 0.2 * i))   # 指数回退
            continue
        return r
    raise Exception("rate limited after retries")
```

#### 监控

```bash
# 配额用满率
curl -H 'X-API-Key: admin' http://cube-api:3000/config | jq .rate_limit_per_sec

# 看 429 频次
curl http://cube-api:3000/metrics | grep cube_rate_limit
# cube_rate_limit_blocked_total{key="..."} 42
```

**注意**:

- **默认 100 req/s 是单 key 的** —— 不要把多个 user 共用一个 key 然后期望 quota 公平分配
- 限流是基于内存的,多 CubeAPI 实例下每个 instance 各自有 bucket → 实际配额 = `配置 × instance_count`
- 高 quota(>10k req/s)时 governor 内部 bucket 用 dashmap,内存可能膨胀;关注 `cube_rate_limiter_memory_bytes`
- OAuth/Bearer token 默认映射到 user,所有 token 共享配额;需要 per-token 隔离时可自定义 callback 传 `X-Cube-Key-Id`
- 429 不写 audit log,生产 cluster 务必把 `Retry-After` 也实时返回
- **不要把 rate_limit_per_sec 设置为 0**(虽然 max(1) 会兜底,但是字面 0 在测试时容易引发闭锁)
