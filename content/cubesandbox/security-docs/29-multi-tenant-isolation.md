---
title: "4.5 多租户隔离"
date: 2026-07-04T14:33:05+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/29-multi-tenant-isolation/"
summary: "CubeSandbox 的多租户基于 containerd namespaces(namespaces.WithNamespace(ctx, ns))+ tenant label + Redis keyspace 三层组合。"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 4.5 多租户隔离

## 机制原理

CubeSandbox 的多租户基于 **containerd namespaces**(`namespaces.WithNamespace(ctx, ns)`)+ **tenant label** + **Redis keyspace** 三层组合。

```go
// Cubelet/services/images/image_gc.go
nsCtx := namespaces.WithNamespace(ctx, ns)

// Cubelet/services/cubebox/events.go
ctx := namespaces.WithNamespace(context.Background(), e.Namespace)

// Cubelet/services/cubebox/runc_container_op.go
ctx = namespaces.WithNamespace(ctx, sb.Namespace)

// Cubelet/services/images/service.go:50
ns := namespaces.Default
ctx = namespaces.WithNamespace(ctx, ns)
```

要点:

1. **containerd namespace 隔离 image / container 元数据** —— 不同 tenant 的 image 即使同名也不互相干扰
2. **tenant label 在 CRUD 阶段注入** —— `e.Namespace = tenant_xxx`
3. **Redis keyspace**:Redis 使用 prefix 区分,`redis:tenant:xxx:sandboxes:*`
4. **mysql 同样按 tenant 命名空间划分**

## 为什么 CubeSandbox 这样设计

- **同一容器名前缀互不可见** —— 攻击者即使 enumeration 也只能在自己 namespace 内
- **资源按 namespace 配额** —— CPU / 内存 / disk per tenant 配额可追溯
- **fault domain** —— 某个 tenant 的故障不能拖累 host 上的其他 tenant
- **audit 归因** —— audit log 自然就能写明 tenant_id

## 如何使用 / 配置

#### containerd 命名空间

containerd 默认只有 `default` namespace,多租户必须显式声明:

```toml
# /etc/containerd/config.toml
[plugins."io.containerd.svc"]
  disabled_plugins = []
  required_plugins = []
```

每租户配置:

```go
import "github.com/containerd/containerd/namespaces"

ctx = namespaces.WithNamespace(ctx, "tenant-alpha")
```

#### CubeMaster conf.yaml 启用多租户

```yaml
multitenant:
  enabled: true
  namespaces:
    - name: tenant-alpha
      quota:
        cpu:    "1000m"   # 1 vCPU
        memory: "4Gi"
        disk:   "100Gi"
        max_sandboxes: 100
    - name: tenant-beta
      quota:
        cpu:    "500m"
        memory: "2Gi"
        max_sandboxes: 50
```

#### 通过 callback 注入 tenant

CubeAPI 在 auth callback 处推断:

```python
@app.post("/cube/verify")
async def verify(req: Request):
    user = extract_user(req)
    tenant = user.organization_id
    # 把 tenant 放回 X-Tenant 给 CubeAPI
    return {"ok": True, "headers": {"X-Cube-Tenant": tenant}}
```

```rust
// CubeAPI auth middleware
if let Some(tenant) = resp.headers().get("X-Cube-Tenant") {
    req.extensions_mut().insert(TenantId(tenant.to_string()));
}
```

#### 实际操作

```bash
# tenant-alpha 创建 sandbox
curl -X POST http://cube-api:3000/sandboxes \
  -H 'Authorization: Bearer tenant-alpha-token' \
  -H 'X-Cube-Tenant: tenant-alpha' \
  -d '{...}'

# 验证 sandbox 命名空间
curl -H 'Authorization: Bearer tenant-alpha-token' \
     http://cube-api:3000/sandboxes | jq '.[].namespace'
# 应该全是 "tenant-alpha"
```

#### 跨 tenant 的不同维度

| 数据 | 隔离 |
|---|---|
| containerd image | `nsCtx` |
| containerd container | `nsCtx` + `e.Namespace` |
| redis | `prefix:tenant-id:` |
| mysql table | `tenant_id` column |
| 文件 (sandbox rootfs) | `/var/lib/cubelet/sandboxes/<tenant-id>/<box-id>` |

**注意**:

- **跨 tenant 的资源引用必须显式** —— 不要把 tenant _id 直接写到 image tag 里
- **`/config` 端点必须返回当前 tenant 配额**,否则 ops 看不到超额原因
- 监控必须能区分 tenant——避免"集群总健康"假象掩盖单 tenant 故障
- **callback 实现务必返回 tenant-id** —— 没有 X-Cube-Tenant 头时,CubeAPI 默认 `default` namespace,等同于"共享租户"
- Redis 的 namespace 与 mysql 不同步时,迁移工具要先做 root bucket 分配,避免数据串
- 升级 containerd 时,**namespace 行为可能有变化**(尤其 v1.7+),务必在 staging 验证
