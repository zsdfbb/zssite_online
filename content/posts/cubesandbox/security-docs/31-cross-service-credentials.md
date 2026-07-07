---
title: "4.7 跨服务凭据"
date: 2026-07-04T14:33:47+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/31-cross-service-credentials/"
summary: "CubeSandbox 部署依赖多个组件:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 4.7 跨服务凭据

## 机制原理

CubeSandbox 部署依赖多个组件:

- CubeMaster (control plane)
- CubeAPI (HTTP gateway)
- Cubelet (per-node)
- MySQL (元数据)
- Redis (quota / session)

各组件之间互相认证需要凭据;CubeMaster conf.yaml 中显式给出默认凭据:

```yaml
mysql:
  host: 127.0.0.1
  port: 3306
  user: cube
  pwd:  "cube_pass"        # ← 默认密码
  db:   cube
redis:
  host: 127.0.0.1
  port: 6379
  password: "ceuhvu123"    # ← 默认密码
```

安装脚本 `deploy/one-click/install.sh:85-98` 中有 `warn_default_external_credentials()` 函数:**在用户使用默认外部凭据时输出 WARNING**。

```bash
warn_default_external_credentials() {
    if [[ "$CUBE_EXTERNAL_MYSQL_PASSWORD" == "cube_pass" ]]; then
        warn "⚠️  Default MySQL password 'cube_pass' is in use."
        warn "   Please override via CUBE_EXTERNAL_MYSQL_PASSWORD."
    fi
    if [[ "$CUBE_EXTERNAL_REDIS_PASSWORD" == "ceuhvu123" ]]; then
        warn "⚠️  Default Redis password 'ceuhvu123' is in use."
        warn "   Please override via CUBE_EXTERNAL_REDIS_PASSWORD."
    fi
}
```

## 为什么 CubeSandbox 这样设计

- **dev-env 开箱即用**:默认凭据让"clone + 装上 + 起服务"无缝
- **生产强制提醒** —— 靠 `warn` 让用户必须显式 export 环境变量
- **环境变量覆盖** —— `CUBE_EXTERNAL_MYSQL_PASSWORD` / `CUBE_EXTERNAL_REDIS_PASSWORD` 一行替换

## 如何使用 / 配置

#### 部署脚本强制设定

```bash
export CUBE_EXTERNAL_MYSQL_PASSWORD='$(openssl rand -base64 24)'
export CUBE_EXTERNAL_REDIS_PASSWORD='$(openssl rand -base64 24)'

./install.sh
```

#### 验证默认值

```bash
grep -E '"cube_pass"|"ceuhvu123"' /etc/cube/conf.yaml
# 不应出现,出现就需要替换
```

#### 部署时若仍用默认凭据

```text
======================================
⚠️  Default MySQL password 'cube_pass' is in use.
   Please override via CUBE_EXTERNAL_MYSQL_PASSWORD.
======================================
```

#### 凭据轮换流程

```bash
# 1. 生成新密码
NEW_PWD=$(openssl rand -base64 24)

# 2. mysql 端先
mysql -uroot -e "ALTER USER 'cube'@'%' IDENTIFIED BY '$NEW_PWD';"

# 3. 滚动重启 CubeMaster / Cubelet
systemctl restart cubemaster
for node in node1 node2 node3; do
    ssh $node "systemctl restart cubelet; systemctl restart cube-shim"
done

# 4. 老密码从 vault 删除
```

#### 各服务默认端口

| 组件 | 端口 | 协议 |
|---|---|---|
| CubeAPI | 3000 | HTTP/HTTPS |
| CubeMaster | 8089 | HTTP/HTTPS |
| MySQL | 3306 | TCP |
| Redis | 6379 | TCP |

**注意**:

- **不要把默认凭据 commit 到公共 git 仓库** —— `cube_pass` / `ceuhvu123` 是 dev-env 的产物,生产务必重写
- mysql 密码生产至少 32 字符随机,定期 90 天轮换
- 凭据应**经由 secret manager**(Vault / AWS SM / 阿里云 KMS)分发,而不是环境变量明文挂在 systemd unit
- `warn_default_external_credentials` 只有 WARNING,不强制阻断;**生产部署流程应自己添 fail-closed 检查**
- 不要让不同组件共用同一个密码 —— mysql / redis / 各服务都有独立密码
- mysql 端开启 TLS,redis 端开启 `requirepass` + `tls-port`
- mysql user 应当仅可在必要 IP 上授权,避免 0.0.0.0
