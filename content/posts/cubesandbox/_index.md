---
title: "CubeSandbox 安全研究"
description: "CubeSandbox (腾讯开源 microVM 沙箱) 安全机制的完整研究档案"
date: 2026-07-07T00:00:00+08:00
draft: false
---

## 概述

本目录是 [CubeSandbox](https://github.com/tencent/cubesandbox) 安全机制完整研究档案,**包含 12 篇组件架构文档、5 真边界系列 (T1-T5) 与 35 篇机制专题**。

![系统安全边界总览](/images/系统安全边界总览.svg)

## 组件架构与安全 (12 篇)

| # | 文档 | 说明 |
|---|------|------|
| 1 | [CubeAPI 架构与安全](CubeAPI架构与安全.md) | HTTP API 网关 (Rust/axum) |
| 2 | [CubeMaster 架构与安全](CubeMaster架构与安全.md) | 控制面与调度器 (Go) |
| 3 | [Cubelet 架构与安全](Cubelet架构与安全.md) | 节点代理 - containerd fork (Go) |
| 4 | [CubeShim 架构与安全](CubeShim架构与安全.md) | containerd Shim v2 (Rust) |
| 5 | [CubeEgress 架构与安全](CubeEgress架构与安全.md) | 出站透明代理 (Lua/OpenResty) |
| 6 | [CubeNet 架构与安全](CubeNet架构与安全.md) | eBPF 网络虚拟化 (Go + C) |
| 7 | [CubeProxy 架构与安全](CubeProxy架构与安全.md) | 数据面反向代理 (Lua + Go) |
| 8 | [Hypervisor 架构与安全](Hypervisor架构与安全.md) | KVM VMM (Rust) |
| 9 | [Agent 架构与安全](Agent架构与安全.md) | in-VM Guest Agent (Rust) |
| 10 | [Network-Agent 架构与安全](Network-Agent架构与安全.md) | 节点网络编排 (Go) |
| 11 | [WebUI 架构与安全](WebUI架构与安全.md) | React 管理控制台 (TypeScript) |
| 12 | [SDK 架构与安全](SDK架构与安全.md) | Python + Go SDK |
