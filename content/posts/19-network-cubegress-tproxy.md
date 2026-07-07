---
title: "3.4.2 网络隔离 - L7 透明代理 (CubeEgress)"
date: 2026-07-04T14:29:28+08:00
draft: false
tags: ["cubesandbox", "security", "T4"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/19-network-cubegress-tproxy/"
summary: "CubeEgress 是 L7 透明代理 / HTTPS MITM,在 host 上拦截 sandbox 的 80/443 出站:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 3.4.2 网络隔离 - L7 透明代理 (CubeEgress)

## 机制原理

CubeEgress 是 **L7 透明代理 / HTTPS MITM**,在 host 上拦截 sandbox 的 80/443 出站:

```
guest-app → TAP(z1234) → cube-dev (bridge)
                              ↓
                       iptables mangle/PREROUTING
                              ↓
                TPROXY --on-ip 192.168.0.1:8080   (HTTP 明文,直接转发)
                TPROXY --on-ip 192.168.0.1:8443   (HTTPS MITM,动态签 leaf cert)
                              ↓
                       OpenResty (192.168.0.1)
                              ↓
                    L7 rule 匹配 (allow/deny/inject)
                              ↓
                          上游真实服务
```

OpenResty 中通过 `ssl_certificate_by_lua` 在握手时动态签发 leaf cert,并在 Lua 层校验 SNI / 路径 / Method,匹配规则后**注入**(如加 header / 替换)或拦截。

iptables 落地在 `CubeEgress/scripts/cube-proxy-iptables-init.sh:60-90`:

```bash
iptables -t mangle -A "${CHAIN}" -i cube-dev -p tcp --dport 80 -j TPROXY \
    --on-ip 192.168.0.1 --on-port 8080
iptables -t mangle -A "${CHAIN}" -i cube-dev -p tcp --dport 443 -j TPROXY \
    --on-ip 192.168.0.1 --on-port 8443
```

OpenResty 配置:`CubeEgress/nginx.conf`。`ssl_certificate_by_lua` 动态签 leaf cert。
`docs/guide/egress-network-policy.md:160-200` 给出规则字段表:

```text
match:
  scheme:    http | https
  sni:       example.com
  host:      api.example.com
  method:    [GET, POST]
  path:      /v1/*
action:
  allow      # 正常放行
  audit      # 通过但记日志
  inject     # 注入修改请求/响应
  deny       # 直接拒绝
```

## 为什么 CubeSandbox 这样设计

- **看不到就不能跨** —— sandbox 即使发出 https://internal-server 也是先到 OpenResty;即使 host 上 internal-server 没有公网证书,只要 OpenResty 拒绝,sandbox 就连不上
- **headers 注入** —— 在 eBPF 看不到的 L7 层业务头(Authorization / Cookie / X-Trace-Id)做注入/转译
- **审计友好** —— `audit` action 让所有出站动作有 audit trail
- **白盒可控** —— 所有规则集中在 config,**比"hardcode 一个 L4 ACL"灵活得多**

## 如何使用 / 配置

#### 启动 CubeEgress

通常由 CubeMaster 拉起:

```bash
# CubeEgress 默认以 host network container 跑
podman run -d --net=host \
    --name cube-egress \
    -v /etc/cube/egress:/etc/openresty/conf.d:ro \
    ghcr.io/cubesandbox/cube-egress:latest
```

#### 写规则(网络策略对象)

YAML 形式:

```yaml
apiVersion: cube.cubesandbox.io/v1
kind: EgressNetworkPolicy
metadata:
  name: allow-openai-only
spec:
  targets:
    - selector: { role: llm-agent }   # 作用在哪些 sandbox
  rules:
    - match:
        scheme: https
        sni: "api.openai.com"
      action: allow
    - match:
        scheme: https
        sni: "*.anthropic.com"
      action: allow
    - match:
        scheme: "*"
      action: deny      # 默认 deny
    - match:
        scheme: "*"
      action: audit     # 加 audit log
```

#### 注入示例(inject)

```yaml
    - match:
        host: "*.anthropic.com"
      action: inject
      inject:
        request_headers:
          add:
            X-Cube-Tenant: "tenant-${tenant_id}"
          remove:
            - "X-Internal-Token"
```

`docs/guide/egress-network-policy.md` 给出完整字段表。

**注意**:

- **HTTPS MITM 需要在 sandbox 内信任 OpenResty 的 CA** —— 否则 sandbox 内 curl 会跳出 SSL 错误。Cubesandbox 镜像一般预装好这个 CA(`/etc/ssl/certs/cube-mitm-ca.pem`),但自有 image 可能缺
- 默认情况下,**action 是白名单最严格策略**——任何没有匹配 allow 规则的连接都被 deny。允许"什么都过"反而需要显式声明
- L7 注入与 deny 可以组合:**先 deny 满足条件,再 inject 允许的包**。调试时保持策略简化
- `audit` action 写日志到 `/var/log/openresty/cube-egress-audit.log`,高 QPS 时务必 rotate
- TPROXY 不能与某些老内核共存;`net.ipv4.conf.all.rp_filter=0` 等 sysctl 见 20 号文章
