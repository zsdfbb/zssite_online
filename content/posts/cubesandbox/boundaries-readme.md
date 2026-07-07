---
title: "安全边界系列"
description: "按 SVG 5 真边界 (T1-T5) 组织,每篇展开涉及的所有 L1-L7 机制"
date: 2026-07-07T00:00:00+08:00
draft: false
url: "/cubesandbox/boundaries/"
---

## 概述

本系列与 [系统安全边界总览.svg](/images/系统安全边界总览.svg) 配套,**按真边界**深入展开: SVG 给出一图速览,本系列逐边界讲清楚"这条边界由哪些层的哪些机制共同保护"。

## 5 个真边界 (T1-T5)

| 边界 | 名称 | 文档 |
|------|------|------|
| **T1** | CubeAPI ingress | [T1-cubeapi-ingress.md]({{< relref "T1-cubeapi-ingress.md" >}}) |
| **T2** | Operator Trust | [T2-operator-trust.md]({{< relref "T2-operator-trust.md" >}}) |
| **T3** | KVM CORE BOUNDARY ★核心 | [T3-kvm-core-boundary.md]({{< relref "T3-kvm-core-boundary.md" >}}) |
| **T4** | Egress | [T4-egress.md]({{< relref "T4-egress.md" >}}) |
| **T5** | CubeProxy inbound | [T5-cubeproxy-inbound.md]({{< relref "T5-cubeproxy-inbound.md" >}}) |
