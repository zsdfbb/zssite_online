---
title: "5.2 日志与诊断"
date: 2026-07-04T14:34:50+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/34-logs-diagnostics/"
summary: "CubeSandbox 把日志分成:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 5.2 日志与诊断

## 机制原理

CubeSandbox 把日志分成:

1. **服务运行日志** —— 写到 `/data/log/<service>/`,例:`/data/log/CubeMaster-dev/`
2. **诊断 bundle** —— `cube-diag` 脚本一键打包 host 上 `/dev/kvm`、`/dev/pvm`、服务状态等
3. **guest 内日志** —— vsock + ttrpc 输出;guest kernel printk 通过 virtio-console

#### 落点

| 组件 | 日志路径 | 证据 |
|------|----------|------|
| CubeMaster | `/data/log/CubeMaster-dev/` | `CubeMaster/conf.yaml:9-14` |
| CubeAPI | `/data/log/CubeAPI/` | 默认 path |
| Cubelet | `/data/log/Cubelet/` | 默认 path |
| CubeShim | `/data/log/CubeShim/` | 默认 path |
| cloud-hypervisor | `/tmp/cube-vm-<id>.log` | shim 内部指定 |
| cube-egress | `/var/log/openresty/cube-egress-audit.log` | OpenResty |

#### diag 脚本

```bash
# 完整诊断
./cube-diag collect

# 输出文件
cube-diag-<hostname>-<timestamp>.tar.gz
#   ├─ kvm-pvm-dev.txt
#   ├─ services-status.txt
#   ├─ dmesg.txt
#   ├─ cubelet.conf
#   ├─ ...
```

## 为什么 CubeSandbox 这样设计

- **出问题必能 root cause** —— 涉及 KVM + eBPF + OCI + seccomp 多个栈层,任何一个缺日志都会让 debug 拖很久
- **所有日志集中在 `/data/log`** —— 不必穿透各 service 的 binary path
- **host 信息从 `/dev/kvm` / `/dev/pvm` 都存档** —— 排查时不必重跑

## 如何使用 / 配置

#### 启用

服务在 systemd unit 中通常已经指向 `/data/log/<service>/`,**自动启用**。

#### 调 log level

CubeSandbox 支持分级日志,通常通过 `RUST_LOG`:

```bash
# CubeAPI
RUST_LOG=cube_api=debug,tower_http=info ./cube-api

# CubeShim
RUST_LOG=cube_shim=debug ./containerd-shim-cube-rs
```

#### 收集诊断

```bash
./deploy/one-click/scripts/cube-diag/collect-logs.sh

# 输出 /tmp/cube-diag-<host>-<timestamp>.tar.gz
# 包含:
#   /dev/kvm, /dev/pvm  节点状态
#   systemctl status cube-*
#   dmesg.txt
#   journalctl -u cube-* --since "1 hour ago"
#   各服务的 config(去敏感)
```

#### 关键日志示例

sandbox 创建失败:

```text
2026-07-04T10:30:45 ERROR cube-shim: failed to start VM: timeout waiting vsock connect
2026-07-04T10:30:45 ERROR cube-shim: snapshot_type=Diff not supported with memory_vol_url
2026-07-04T10:30:45 ERROR cube-master: create_sandbox returned: VM create timeout (30s)
```

guest 内 kernel 事件:

```bash
journalctl -k --since '5 minutes ago'
# 查 kvm exit event
# audit log
ausearch -m SECCOMP -ts today
```

#### logrotate

```bash
cat > /etc/logrotate.d/cubesandbox <<EOF
/data/log/Cube*/**/*.log {
    daily
    missingok
    rotate 7
    compress
    delaycompress
    copytruncate
    sharedscripts
    postrotate
        systemctl reload cube-master > /dev/null 2>&1 || true
        systemctl reload cube-api > /dev/null 2>&1 || true
    endscript
}
EOF
```

#### 推送到日志聚合系统

通常用 fluentd / promtail / otel-collector:

```yaml
# otel-collector config
receivers:
  filelog/cube:
    include:
      - /data/log/Cube*/*/*.log
    operators:
      - type: regex_parser
        regex: '^(?P<time>\S+) (?P<level>\S+) (?P<component>\S+): (?P<msg>.*)$'
exporters:
  otlp:
    endpoint: logs.example.com:4317
```

**注意**:

- **敏感信息过滤**:auth token、admin 密码等不应写入日志;CubeAPI 中 `extract_credential` 已经在 middleware 上做了过滤,但 audit log 必须脱敏
- logrotate 必须 `copytruncate`,否则 sandbox 进程写不进截断后的 log
- `/data/log/Cube*` 盘空间监控告警要 70% / 85% / 95% 三档
- 定期(每周)清理 cube-diag tar 包,否则 `/tmp` 会膨胀
- guest 内日志默认从 vsock + console 双通道上抛;debug 时可以打开 `agent.debug_console` 在 guest 启动时往 console 打
- 生产一般禁 `RUST_LOG=trace`,会把日志写到 10+ GB/分钟
- 高频日志的 kubelet api 流量(创建 sandbox)应**保留结构化 trace_id**,便于追踪
