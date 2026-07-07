---
title: "3.6 OCI 容器兼容 (containerd-shim v2)"
date: 2026-07-04T14:30:43+08:00
draft: false
tags: ["cubesandbox", "security", "mechanism"]
categories: ["cubesandbox"]
comments: true
showToc: true
url: "/cubesandbox/docs/22-oci-shim-v2/"
summary: "CubeShim 是 containerd shim v2 的实现,注册到 containerd 时 runtime type 是:"
cover:
  image: ""
  alt: ""
  caption: ""
---
# 3.6 OCI 容器兼容 (containerd-shim v2)

## 机制原理

CubeShim 是 **containerd shim v2** 的实现,注册到 containerd 时 runtime type 是:

```text
runtime_type = "io.containerd.cube.v2"
```

完整 Shim API 由容器化行业标准决定(`containerd-shim=0.9.0`、`containerd-shim-protos=0.9.0`、`ttrpc=0.5.8`)。

```toml
# CubeShim/shim/Cargo.toml:17-25
containerd-shim = "0.9.0"
containerd-shim-protos = "0.9.0"
ttrpc = "0.5.8"
```

它**完整支持 OCI spec、CRI 安全上下文**:

- `privileged` —— 是否以特权模式启动
- `readonly` —— rootfs 是否只读
- `NoNewPrivs` —— 进程不能再获得新特权(`no_new_privs: true`)
- `capabilities` —— 5 个 set(见 9 号文章)
- `seccomp` —— OCI LinuxSeccomp 解析,下发到 guest agent
- `apparmor` —— profile name(本项目不用,但 spec 接受)

`CubeMaster/conf.yaml:32-37` 提供默认 OCI spec:

```yaml
runtime_security:
  privileged: true          # 注: 当前默认值,生产可改为 false
  readonly_rootfs: false
  no_new_privs: false
```

注:**`privileged: true` 是 dev-env 默认**,生产应改为 false。这点在 CubeMaster 配置审计中很重要。

## 为什么 CubeSandbox 这样设计

- **与 Kubernetes 兼容** —— 直接被 K8s 通过 containerd 调用,业务迁移平滑
- **CRI 透传安全上下文** —— K8s pod spec 上写 `securityContext`,CRI 透传到 OCI spec,shim 下发
- **shim 简化** —— 所有 OCI 解析都在 shim 里完成(2.1.2、9、11、13 章节),guest agent 收到的是**已编译**的策略

## 如何使用 / 配置

#### CRI / containerd 配置

```toml
# /etc/containerd/config.toml
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.cube]
  runtime_type = "io.containerd.cube.v2"
  runtime_path = "/usr/local/services/cubetoolbox/cube-shim/bin/containerd-shim-cube-rs"
```

```toml
[plugins."io.containerd.grpc.v1.cri".containerd.runtimes.cube.options]
  # 任意自定 options,会作为 opts.json 注入 shim
  BaseSize = "10G"
  Snapshotter = "overlay"
```

#### K8s pod spec

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: cubesandbox-agent
spec:
  runtimeClassName: cube
  containers:
    - name: agent
      image: my-llm-agent:latest
      securityContext:
        privileged: false
        runAsUser: 1000
        capabilities:
          add:
            - NET_BIND_SERVICE
          drop:
            - ALL
        seccompProfile:
          type: RuntimeDefault
        noNewPrivileges: true
```

#### CubeMaster conf.yaml 默认

`CubeMaster/conf.yaml:32-37`:

```yaml
runtime_security:
  privileged: true
  readonly_rootfs: false
  no_new_privs: false
```

**生产调优**:

```yaml
runtime_security:
  privileged: false     # ← 改
  readonly_rootfs: true # rootfs 只读
  no_new_privs: true    # 禁止再获权
```

#### 测试

`Cubelet/services/cubebox/cube_container_create_test.go` 中给出对 `NoNewPrivs: true` 的测试示例。

**注意**:

- **`privileged: true` 是高风险默认**——若你的 shim 跑的是 OpenAI Agents SDK,务必把这一项调成 `false`,否则 sandbox 中任何程序能直接访问 host 的 /dev 等
- `no_new_privs: true` 让 suid 二进制**不再**赋予权限,更安全
- OCI spec 默认传 `apparmor` profile 时,即使没有 apparmor 引擎,shim 也不能 fail-fast——它会以"无 LSM"姿态通过,**业务侧必须主动验证 sandbox 真实 seccomp/cap 应用情况**
- 调试时禁止跑 `kubelet --root` —— containerd 一旦以 root 起,而你的 pod `runAsUser: 1000`,会失败,先确认 base fs 是 non-root safe
- 多 vCPU / 大内存 spec,通常一并对齐 1.3 的 vCPU/内存隔离配置
