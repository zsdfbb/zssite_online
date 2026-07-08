---
title: "SDK (Python + Go) 架构与使用指南"
date: 2026-07-08T00:00:00+08:00
draft: false
tags: ["cubesandbox"]
categories: ["cubesandbox"]
comments: true
showToc: true
weight: 100
---


> 调研时间: 2026/07/08
> 调研范围: `/home/zs/Develop/TraceCubeSandbox/sdk/` 全量 Python + Go 源码
> 目的: 系统性梳理 CubeSandbox 的客户端 SDK 的架构与使用方法

---

## 1. 概述

CubeSandbox 提供 **Python** 和 **Go** 两个官方 SDK，封装了与 CubeAPI 的所有交互，包括沙箱创建、代码执行、文件操作、快照管理、模板管理等。

| 属性 | Python SDK | Go SDK |
|------|-----------|--------|
| **包名** | cubesandbox v0.3.0 | github.com/tencentcloud/CubeSandbox/sdk/go |
| **语言版本** | Python >= 3.9 | Go 1.22 |
| **HTTP 客户端** | httpx >= 0.27 + requests | net/http (标准库) |
| **API 兼容** | E2B 兼容 | E2B 兼容 |
| **关键依赖** | httpx, requests | 标准库 (零外部依赖) |
| **配置** | 环境变量 (CUBE_*) | 环境变量 (CUBE_*/E2B_*) |
| **许可协议** | Apache-2.0 | Apache-2.0 |

**核心职责**:
- 沙箱生命周期管理 (create / connect / kill / pause / resume)
- 代码执行 (Code execution via Jupyter kernel) + 流式回调
- Shell 命令执行 (envd Connect-JSON RPC over HTTP)
- 文件系统操作 (read / write / list / stat / remove / rename / mkdir / watch)
- 伪终端 (PTY) 交互 (create / connect / send_stdin / resize / kill)
- 模板管理 (build / rebuild / list / get / delete)
- 快照管理 (create / list / delete / rollback / clone)
- 网络策略配置 (L3/L4 CIDR + L7 host/path/SNI 规则)
- 生命周期策略配置 (on_timeout / auto_resume)

---

## 2. Python SDK 架构

### 2.1 目录结构

```
sdk/python/
├── pyproject.toml                    # 包配置 (ruff/mypy/pytest/coverage)
├── cubesandbox/
│   ├── __init__.py                   # 公开 API 导出 (__version__ = "0.3.0")
│   ├── sandbox.py                    # Sandbox 核心类 (create/connect/run_code/clone)
│   ├── _config.py                    # Config 数据类 (环境变量读取)
│   ├── _commands.py                  # 命令执行 (Connect-JSON RPC + E2B proto 回退)
│   ├── _filesystem.py                # 文件操作 (read/write/list/stat/watch)
│   ├── _pty.py                       # 伪终端 (create/connect/send_stdin/resize/kill)
│   ├── _stream.py                    # NDJSON 流解析 (result/stdout/stderr/error)
│   ├── _transport.py                 # HTTP 传输层 (IPOverrideTransport + build_client)
│   ├── _policy.py                    # 网络策略类型 (Rule/Match/Action/Inject + E2B 兼容)
│   ├── _template.py                  # 模板管理 (list/get/build/rebuild/delete)
│   ├── _models.py                    # 数据模型 (Execution/Result/Logs/SnapshotInfo)
│   └── _exceptions.py               # 异常层次 (CubeSandboxError 基类)
└── tests/                            # 单元测试 + 集成测试
    ├── test_sandbox.py
    ├── test_template_e2e.py
    ├── integration_test_sdk.py
    └── conftest.py
```

### 2.2 核心使用模式

```python
from cubesandbox import Sandbox

# 创建沙箱 (上下文管理器自动销毁)
with Sandbox.create(template="my-template") as sb:
    # 执行代码
    result = sb.run_code("print('hello')")
    print(result.text)

    # 文件操作
    sb.files.write("/path/to/file", b"data")
    content = sb.files.read("/path/to/file")

    # Shell 命令
    cmd = sb.commands.run("ls -la")
    print(cmd.stdout)

    # 快照
    snap = sb.create_snapshot("snapshot-name")
    sb.rollback(snap.snapshot_id)

# 连接到已有沙箱
sb = Sandbox.connect("sandbox-id-here")

# 流式执行 (带回调)
from cubesandbox import OutputMessage, Result, ExecutionError
execution = sb.run_code(
    "for i in range(5): print(i)",
    on_stdout=lambda msg: print(f"[stdout] {msg.text}"),
    on_result=lambda r: print(f"[result] {r.text}"),
)

# 网络策略
from cubesandbox import Rule, Match, Action, Inject
sb = Sandbox.create(template="tpl-xxx", network={
    "allow_public_traffic": False,
    "rules": [
        Rule(
            name="allow-api",
            match=Match(host="api.example.com"),
            action=Action(allow=True),
        ),
    ],
})
```

### 2.3 模块职责

| 模块 | 类/函数 | 用途 | 关键位置 |
|------|---------|------|----------|
| `sandbox.py` | `Sandbox` | 核心类: create/connect/kill/pause/resume/rollback/clone | `Sandbox.create:141-244` |
| `_config.py` | `Config` | 从环境变量读取配置 (api_url/proxy_node_ip/...) | `Config` 数据类 |
| `_commands.py` | `Commands` | shell 命令执行 (Connect-JSON RPC + E2B proto 回退) | `Commands.run:37-77` |
| `_filesystem.py` | `Filesystem` | 文件读写 (read/write/write_files/list/stat/watch_dir) | `Filesystem.read:68-88` |
| `_pty.py` | `Pty` | 伪终端交互 (create/connect/send_stdin/resize/kill) | `Pty.create:350-384` |
| `_transport.py` | `IPOverrideTransport` | HTTP 传输 (支持代理节点 IP 覆盖,绕过 DNS) | `IPOverrideTransport:13-40` |
| `_stream.py` | `_parse_line` | NDJSON 流式响应解析 (result/stdout/stderr/error) | `_parse_line:15-64` |
| `_policy.py` | `Rule/Match/Action/Inject` | 网络策略类型 + E2B 兼容转换 | `Rule.to_wire:111-116` |
| `_template.py` | `Template` | 模板管理 (list/get/build/rebuild/delete) | `Template.build:209-339` |
| `_models.py` | `Execution/Result/Logs` | 数据模型 | `Execution:166-189` |
| `_exceptions.py` | `CubeSandboxError` | 异常层次 | 基类 + 5 个子类 |

### 2.4 关键设计细节

**流式执行机制** (`sandbox.py:352-389`):
```
Sandbox.run_code()
  ↓
httpx.Client.stream("POST", "http://{port}-{sandboxID}.cube.app/execute")
  ↓ NDJSON stream
_parse_line() → 逐个解析 event type:
  - "result" → Result (is_main_result → Execution.text)
  - "stdout" → OutputMessage (回调 on_stdout)
  - "stderr" → OutputMessage (回调 on_stderr)
  - "error"  → ExecutionError (回调 on_error)
  - "number_of_executions" → ExecutionCount
```

**shell 命令双重实现** (`_commands.py:37-77`):
1. **E2B proto 路径**: 尝试导入 `e2b.envd.process`,使用生成的 protobuf ProcessClient
2. **Connect-JSON 回退**: 使用自定义的 `_run_with_connect_fallback`,通过 Connect 协议帧与 envd 通信
   - 帧格式: 1 byte flags + 4 byte big-endian length + JSON body
   - 端点: `POST /process.Process/Start` (envd 端口 49983)

**写入文件回退机制** (`_filesystem.py:90-121`):
1. 尝试 `application/octet-stream` 直写
2. 如果 envd 版本拒绝,回退到 `multipart/form-data` 上传

**IP 覆盖传输** (`_transport.py:13-40`):
- `CUBE_PROXY_NODE_IP` 设置时,所有数据面连接绕过 DNS 解析
- `IPOverrideTransport` 将 TCP 连接路由到固定 IP:port
- 保留原始 `Host` header 以便 CubeProxy 路由到正确的沙箱

**生命周期策略** (`sandbox.py:47-66`):
- `lifecycle.on_timeout`: `"kill"` (默认) 或 `"pause"` — 超时时销毁或暂停
- `lifecycle.auto_resume`: `bool` — 暂停后是否自动恢复
- 蛇形命名自动转为驼峰 (`on_timeout` → `onTimeout`)

---

## 3. Go SDK 架构

### 3.1 目录结构

```
sdk/go/
├── go.mod                          # module: github.com/tencentcloud/CubeSandbox/sdk/go (Go 1.22)
├── client.go                       # Client 结构体 + NewClient/Create/Connect/List/ListV2/Health
├── config.go                       # Config + NewConfigFromEnv() (CUBE_*/E2B_* 环境变量)
├── sandbox.go                      # Sandbox 方法 (GetHost/GetInfo/Pause/Resume/Kill/RunCode/Clone)
├── models.go                       # 共享类型 (Sandbox/SandboxInfo/CreateOptions/Execution/Result/...)
├── files.go                        # 文件操作 (Read/Write/WriteFiles/List/Stat/Exists/Remove/Rename/MakeDir/WatchDir)
├── commands.go                     # 命令执行 (Run)
├── envd.go                         # Envd 连接管理 (process start/file RPC/read/write/Watcher)
├── stream.go                       # NDJSON 流式解析 (parseStream/parseLine)
├── transport.go                    # HTTP 传输 (newControlHTTPClient/newDataHTTPClient IP 覆盖)
├── errors.go                       # 错误类型 (APIError/ErrAuthentication/ErrSandboxNotFound)
├── policy.go                       # 网络策略 (Rule/Match/Action/Inject + 域名校验)
├── template.go                     # 模板操作 (ListTemplates/GetTemplate/BuildTemplate/RebuildTemplate/...)
├── snapshot.go                     # 快照操作 (CreateSnapshot/ListSnapshots/DeleteSnapshot/Rollback/Clone)
├── sdk_test.go                     # SDK 单元测试
├── aligned_test.go                 # 对齐测试 (与 Python SDK 行为一致)
├── integration_test.go             # 集成测试
└── coverage_test.go                # 覆盖率测试
```

### 3.2 核心使用模式

```go
import cubesandbox "github.com/tencentcloud/CubeSandbox/sdk/go"

// 从环境变量加载配置
cfg := cubesandbox.NewConfigFromEnv()

// 创建 Client
client := cubesandbox.NewClient(cfg)
defer client.Close()

// 创建沙箱
ctx := context.Background()
sb, err := client.Create(ctx, cubesandbox.CreateOptions{
    TemplateID: "my-template",
})
defer sb.Kill(ctx)

// 执行代码
result, err := sb.RunCode(ctx, "print('hello')", cubesandbox.RunCodeOptions{})
fmt.Println(result.Text)

// 文件操作
sb.Files().Write(ctx, "/path/to/file", []byte("data"))
content, _ := sb.Files().Read(ctx, "/path/to/file")

// Shell 命令
cmdResult, _ := sb.Commands().Run(ctx, "ls -la", cubesandbox.CommandOptions{})
fmt.Println(cmdResult.Stdout)

// 连接到已有沙箱
sb2, _ := client.Connect(ctx, "sandbox-id-here")
```

### 3.3 模块职责

| 模块 | 类型/函数 | 用途 | 关键位置 |
|------|----------|------|----------|
| `client.go` | `Client` | 客户端: Create/Connect/List/ListV2/Health | `Client.Create:66-78` |
| `config.go` | `Config` / `NewConfigFromEnv` | 环境变量配置 (CUBE_* 优先于 E2B_*) | `NewConfigFromEnv:38-51` |
| `sandbox.go` | `Sandbox` | 沙箱方法: RunCode/Pause/Resume/Kill/GetInfo | `Sandbox.RunCode:126-175` |
| `envd.go` | `Watcher` / process 实现 | Envd RPC 通信 + 文件操作 + WatchDir 流式 | `startProcess:89-129` |
| `files.go` | `Files` | 文件操作代理: Read/Write/List/Stat/WatchDir | `Files.Write:43-48` |
| `commands.go` | `Commands` | Shell 命令执行 | `Commands.Run:19-47` |
| `stream.go` | `parseStream`/`parseLine` | NDJSON 流解析 (result/stdout/stderr/error) | `parseLine:21-103` |
| `transport.go` | `newDataHTTPClient` | HTTP 传输 (IP 覆盖 via DialContext) | `newDataHTTPClient:19-38` |
| `errors.go` | `APIError` | 错误类型 (Kind: api/authentication/sandbox_not_found) | `apiErrorFromStatus:66-98` |
| `policy.go` | `Rule`/`Match`/`Action`/`Inject` | 网络策略 + 域名校验 | `validateAllowOutDomainsRequireDenyAll:70-87` |
| `template.go` | `Client` 方法 | 模板 CRUD (Build/Rebuild/Delete/List/Get) | `Client.BuildTemplate:89-99` |
| `snapshot.go` | `Sandbox`/`Client` 方法 | 快照管理 (Create/List/Delete/Rollback/Clone) | `Sandbox.CreateSnapshot:41-55` |

### 3.4 关键设计细节

**Go Client 设计模式** (`client.go:32-49`):
- `Client` 结构体包含两个 HTTP 客户端: `controlHTTP` (控制面 → CubeAPI) 和 `dataHTTP` (数据面 → sandbox envd)
- `ClientOption` 函数式配置 (`WithHTTPClient` 用于注入自定义 HTTP 客户端)
- API Key 自动附加 `Authorization: Bearer` header (`client.go:222-224`)

**IP 覆盖机制** (`transport.go:27-36`):
```go
// 当 CUBE_PROXY_NODE_IP 设置时, DialContext 绕过 DNS 直接连接到代理 IP
if cfg.ProxyNodeIP != "" {
    target := net.JoinHostPort(cfg.ProxyNodeIP, strconv.Itoa(cfg.ProxyPortHTTP))
    transport.DialContext = func(ctx context.Context, network, _ string) (net.Conn, error) {
        return dialer.DialContext(ctx, network, target)
    }
}
```
等价于 Python 的 `IPOverrideTransport`。

**配置优先级** (`config.go:40-48`):
- `CUBE_API_URL` / `CUBE_API_KEY` 优先于 `E2B_API_URL` / `E2B_API_KEY`
- Go 特有的 `CUBE_TIMEOUT` (支持 `300s` / `5m` 格式) 和 `CUBE_REQUEST_TIMEOUT`
- Go 特有的 `CUBE_PROXY_SCHEME` (http/https, 默认为 `http` 或根据端口 443 推断)

**流式 NDJSON 解析** (`stream.go:12-18`):
- `parseStream` 使用 `bufio.Scanner` (最大行 16MB) 逐行扫描
- 每行 JSON 解析 `type` 字段分发到 result/stdout/stderr/error/number_of_executions

**错误分类** (`errors.go:66-98`):
- HTTP 401/403 → `ErrAuthentication`
- HTTP 404 + 含 "template" → `ErrTemplateNotFound`
- HTTP 404 + 其余 → `ErrSandboxNotFound`
- `APIError.Is()` 方法支持 `errors.Is(err, ErrSandboxNotFound)` 模式

---

## 4. 配置项

### 4.1 环境变量

| 变量 | SDK | 默认值 | 说明 |
|------|-----|--------|------|
| `CUBE_API_URL` | Python + Go | `http://127.0.0.1:3000` | CubeAPI 端点 (控制面) |
| `CUBE_API_KEY` | Python + Go | `""` | API 密钥 (Bearer token) |
| `CUBE_TEMPLATE_ID` | Python + Go | `""` | 默认模板 ID |
| `CUBE_PROXY_NODE_IP` | Python + Go | `""` | 数据面代理 IP 覆盖 (绕过 DNS) |
| `CUBE_PROXY_PORT_HTTP` | Python + Go | `80` | 代理 HTTP 端口 |
| `CUBE_SANDBOX_DOMAIN` | Python + Go | `cube.app` | 沙箱域名后缀 |
| `CUBE_DEBUG` | Python + Go | `false` | 调试模式 |
| `CUBE_TIMEOUT` | Go | `300s` | 沙箱 TTL (默认 300 秒) |
| `CUBE_REQUEST_TIMEOUT` | Go | `30s` | HTTP 请求超时 |
| `CUBE_PROXY_SCHEME` | Go | `http` | 代理协议 (http/https) |
| `E2B_API_URL` | Go | — | E2B 兼容端点 (CUBE_API_URL 优先) |
| `E2B_API_KEY` | Go | — | E2B 兼容密钥 (CUBE_API_KEY 优先) |

Python SDK 额外环境变量 (在 `Config` 数据类中处理):
| `CUBE_SANDBOX_DOMAIN` | Python | `cube.app` | 沙箱域名后缀 |
| `CUBE_PROXY_PORT_HTTP` | Python | `80` | 代理 HTTP 端口 |

### 4.2 配置加载

**Python** (`_config.py:11-33`):
```python
@dataclass
class Config:
    api_url: str = field(
        default_factory=lambda: os.environ.get("CUBE_API_URL", "http://127.0.0.1:3000")
    )
    template_id: str | None = field(
        default_factory=lambda: os.environ.get("CUBE_TEMPLATE_ID")
    )
    proxy_node_ip: str | None = field(
        default_factory=lambda: os.environ.get("CUBE_PROXY_NODE_IP")
    )
    proxy_port: int = field(
        default_factory=lambda: int(os.environ.get("CUBE_PROXY_PORT_HTTP", "80"))
    )
    sandbox_domain: str = field(
        default_factory=lambda: os.environ.get("CUBE_SANDBOX_DOMAIN", "cube.app")
    )
    timeout: int = 300
    request_timeout: float = 30.0
```

**Go** (`config.go:38-51`):
```go
func NewConfigFromEnv() Config {
    cfg := Config{
        APIURL:         firstEnv("CUBE_API_URL", "E2B_API_URL"),
        APIKey:         firstEnv("CUBE_API_KEY", "E2B_API_KEY"),
        TemplateID:     os.Getenv("CUBE_TEMPLATE_ID"),
        ProxyNodeIP:    os.Getenv("CUBE_PROXY_NODE_IP"),
        ProxyPortHTTP:  parseIntEnv("CUBE_PROXY_PORT_HTTP", 80),
        ProxyScheme:    os.Getenv("CUBE_PROXY_SCHEME"),
        SandboxDomain:  os.Getenv("CUBE_SANDBOX_DOMAIN"),
        Timeout:        parseDurationEnv("CUBE_TIMEOUT", 300*time.Second),
        RequestTimeout: parseDurationEnv("CUBE_REQUEST_TIMEOUT", 30*time.Second),
    }
    return normalizeConfig(cfg)
}
```

---

## 5. 安全注意事项

| # | 注意 | 等级 | 说明 |
|---|------|------|------|
| S1 | API Key 环境变量 | 🟢 低 | 不要硬编码 API Key;使用环境变量或外部密钥管理 |
| S2 | HTTPS 加密 | 🟢 低 | 生产环境应启用 HTTPS (Go 支持 `CUBE_PROXY_SCHEME=https`) |
| S3 | IP 覆盖用于调试 | 🟢 低 | `CUBE_PROXY_NODE_IP` 用于开发调试,生产环境建议使用 CubeAPI 地址 |
| S4 | traffic access token | 🟢 低 | 沙箱创建时 `network.allow_public_traffic=False` 会返回令牌,客户端需持久化 |
| S5 | envdAccessToken 管理 | 🟢 低 | 每个沙箱的独立访问令牌,仅用于数据面通信,客户端自动在 header 中携带 |

### 5.1 API Key 管理

- Python SDK: 通过 `CUBE_API_KEY` 环境变量读取,`Sandbox.create()` 时通过 `requests.Session` 携带
- Go SDK: 通过 `CUBE_API_KEY` 或 `E2B_API_KEY` 环境变量读取,`Client.newRequest()` 自动设置 `Authorization: Bearer <key>` header
- 两个 SDK 均无内置密钥轮换机制

### 5.2 流量访问令牌

当沙箱以 `network.allow_public_traffic=False` 创建时:
- CubeAPI 返回 `trafficAccessToken` (仅在 create 响应中)
- Python SDK: 自动附加 `e2b-traffic-access-token` header 到数据面请求 (`sandbox.py:756-765`)
- Go SDK: 通过 `Sandbox.TrafficAccessToken` 字段暴露,SDK 内自动附加到 `X-Access-Token` / `e2b-traffic-access-token` header
- 建议客户端在创建时持久化此令牌,因为 `connect` 和 `resume` 不会再次返回

### 5.3 网络策略验证

- 两个 SDK 均在客户端侧进行域名/CIDR 校验 (`_policy.py:332-344` / `policy.go:70-87`)
- 当指定 `allow_out` 域名时,必须同时禁用公网访问或添加 `0.0.0.0/0` 到 `deny_out`
- Go SDK 额外执行域名格式校验 (`isValidDNSDomainName`) 和 dotted-decimal 检测

---

## 6. 关键文件清单

### Python SDK

| 模块 | 路径 | 关键内容 |
|------|------|----------|
| API 导出 | `sdk/python/cubesandbox/__init__.py:1-42` | 公开 API + `__version__ = "0.3.0"` |
| 核心类 | `sdk/python/cubesandbox/sandbox.py:69-766` | Sandbox.create/connect/kill/pause/resume/run_code/clone |
| 配置 | `sdk/python/cubesandbox/_config.py:10-33` | Config 数据类 (环境变量) |
| 命令 | `sdk/python/cubesandbox/_commands.py:33-356` | Commands.run (Connect-JSON + E2B proto) |
| 文件 | `sdk/python/cubesandbox/_filesystem.py:25-299` | Filesystem 文件操作 + WatchDir 流式 |
| PTY | `sdk/python/cubesandbox/_pty.py:1-554` | Pty 交互 (create/connect/send_stdin/resize) |
| 传输 | `sdk/python/cubesandbox/_transport.py:13-65` | IPOverrideTransport + build_client |
| 流式 | `sdk/python/cubesandbox/_stream.py:15-64` | NDJSON _parse_line |
| 策略 | `sdk/python/cubesandbox/_policy.py:29-385` | Rule/Match/Action/Inject + E2B 兼容转换 |
| 模板 | `sdk/python/cubesandbox/_template.py:118-440` | Template.list/get/build/rebuild/delete |
| 模型 | `sdk/python/cubesandbox/_models.py:11-251` | Execution/Result/Logs/SnapshotInfo |
| 异常 | `sdk/python/cubesandbox/_exceptions.py:7-30` | CubeSandboxError 层次 (6 种异常) |
| 包配置 | `sdk/python/pyproject.toml:1-49` | 依赖/ruff/mypy/pytest 配置 |

### Go SDK

| 模块 | 路径 | 关键内容 |
|------|------|----------|
| 客户端 | `sdk/go/client.go:1-236` | Client 结构体 + NewClient/Create/Connect/List |
| 配置 | `sdk/go/config.go:1-133` | Config + NewConfigFromEnv (CUBE_*/E2B_*) |
| 沙箱 | `sdk/go/sandbox.go:1-195` | Sandbox.RunCode/Pause/Resume/Kill/GetInfo |
| 模型 | `sdk/go/models.go:1-190` | Sandbox/CreateOptions/Execution/Result/FileEntry |
| 文件 | `sdk/go/files.go:1-125` | Files.Read/Write/WriteFiles/List/Stat/WatchDir |
| 命令 | `sdk/go/commands.go:1-48` | Commands.Run |
| Envd | `sdk/go/envd.go:1-649` | startProcess/readFile/writeFile/filesystemRPC/Watcher |
| 流式 | `sdk/go/stream.go:1-125` | parseStream/parseLine (NDJSON) |
| 传输 | `sdk/go/transport.go:1-40` | newDataHTTPClient (IP 覆盖) |
| 错误 | `sdk/go/errors.go:1-123` | APIError 分类 (auth/sandbox/template) |
| 策略 | `sdk/go/policy.go:1-146` | Rule/Match/Action/Inject + 域名校验 |
| 模板 | `sdk/go/template.go:1-220` | ListTemplates/BuildTemplate/RebuildTemplate |
| 快照 | `sdk/go/snapshot.go:1-194` | CreateSnapshot/ListSnapshots/Rollback/Clone |
| 模块定义 | `sdk/go/go.mod:1-3` | Go 1.22, 零外部依赖 |

---

## 7. 数据面协议

### 7.1 端口约定

| 端口 | Python 常量 | Go 常量 | 用途 |
|------|-------------|---------|------|
| 49999 | `JUPYTER_PORT` | `JupyterPort` | Jupyter 内核 / 代码执行 |
| 49983 | `ENVD_PORT` | (同上) | Envd 进程管理 + 文件系统 RPC |

### 7.2 虚拟主机名

两者使用相同模式:
```
<port>-<sandboxID>.<domain>
# 示例: 49999-sb-xxxxxx.cube.app
```

Python: `sandbox.py:126` (`f"{port}-{self.sandbox_id}.{self.domain}"`)
Go: `sandbox.go:23` (`fmt.Sprintf("%d-%s.%s", port, s.SandboxID, domain)`)

### 7.3 Connect-JSON 帧格式

用于 envd 进程启动和文件系统 WatchDir 的流式 RPC:
```
┌─────────┬───────────────────────────┬──────────────┐
│ 1 byte  │    4 byte (big-endian)    │   variable   │
│  flags  │        payload length     │   payload    │
├─────────┼───────────────────────────┼──────────────┤
│ 0x01    │ compressed flag           │              │
│ 0x02    │ end-stream flag           │ (error body) │
└─────────┴───────────────────────────┴──────────────┘
```

### 7.4 NDJSON 流格式 (代码执行)

每行独立 JSON,由 `type` 字段分发:
```json
{"type": "stdout", "text": "hello\n", "timestamp": "..."}
{"type": "stderr", "text": "warning\n", "timestamp": "..."}
{"type": "result", "text": "42", "is_main_result": true, ...}
{"type": "error", "name": "ValueError", "value": "...", "traceback": [...]}
{"type": "number_of_executions", "execution_count": 1}
```

---

## 8. 与 CubeAPI 的交互映射

| SDK 方法 | HTTP 端点 | 说明 |
|----------|-----------|------|
| `Sandbox.create()` / `Client.Create()` | `POST /sandboxes` | 创建沙箱 |
| `Sandbox.connect()` / `Client.Connect()` | `POST /sandboxes/:id/connect` | 连接已有沙箱 |
| `Sandbox.list()` / `Client.List()` | `GET /sandboxes` | 列出沙箱 (v1) |
| `Sandbox.list_v2()` / `Client.ListV2()` | `GET /v2/sandboxes` | 列出沙箱 (v2) |
| `Sandbox.get_info()` / `Sandbox.GetInfo()` | `GET /sandboxes/:id` | 沙箱详情 |
| `Sandbox.kill()` / `Sandbox.Kill()` | `DELETE /sandboxes/:id` | 销毁沙箱 |
| `Sandbox.pause()` / `Sandbox.Pause()` | `POST /sandboxes/:id/pause` | 暂停沙箱 |
| `Sandbox.resume()` / `Sandbox.Resume()` | `POST /sandboxes/:id/resume` | 恢复沙箱 |
| `Sandbox.run_code()` / `Sandbox.RunCode()` | `POST <sandbox-host>/execute` | 代码执行 (数据面) |
| `sb.commands.run()` / `sb.Commands().Run()` | `POST <sandbox-host>/process.Process/Start` | Shell 命令 (数据面) |
| `sb.files.read()` / `sb.Files().Read()` | `GET <sandbox-host>/files` | 文件读取 (数据面) |
| `sb.files.write()` / `sb.Files().Write()` | `POST <sandbox-host>/files` | 文件写入 (数据面) |
| `sb.create_snapshot()` / `sb.CreateSnapshot()` | `POST /sandboxes/:id/snapshots` | 创建快照 |
| `sb.rollback()` / `sb.Rollback()` | `POST /sandboxes/:id/rollback` | 回滚快照 |
| `Sandbox.list_snapshots()` / `Client.ListSnapshots()` | `GET /snapshots` | 列出快照 |
| `Sandbox.delete_snapshot()` / `Client.DeleteSnapshot()` | `DELETE /templates/:id` | 删除快照 |
| `sb.clone()` / `sb.Clone()` | 组合: snapshot + create × N + delete | 克隆沙箱 |
| `Template.list()` / `Client.ListTemplates()` | `GET /templates` | 列出模板 |
| `Template.get()` / `Client.GetTemplate()` | `GET /templates/:id` | 模板详情 |
| `Template.build()` / `Client.BuildTemplate()` | `POST /templates` | 构建模板 |
| `Template.rebuild()` / `Client.RebuildTemplate()` | `POST /templates/:id` | 重建模板 |
| `Template.delete()` / `Client.DeleteTemplate()` | `DELETE /templates/:id` | 删除模板 |
| `Sandbox.health()` / `Client.Health()` | `GET /health` | 健康检查 |

---

## 9. 跨语言差异

| 特性 | Python SDK | Go SDK |
|------|-----------|--------|
| 沙箱创建 | 类方法 `Sandbox.create()` | `Client.Create(ctx, opts)` |
| 无上下文管理器 | `with Sandbox.create() as sb:` | `sb, _ := client.Create(...); defer sb.Kill(ctx)` |
| 配置方式 | `Config` 数据类 (直接构造或环境变量) | `NewConfigFromEnv()` + `normalizeConfig()` |
| API Key | 通过 `requests.Session` 传递 | 自动 Bearer header via `newRequest()` |
| HTTP 客户端 | `httpx.Client` (数据面) + `requests.Session` (控制面) | `controlHTTP` + `dataHTTP` (均基于 `net/http`) |
| IP 覆盖 | `IPOverrideTransport` (httpx transport) | `DialContext` 覆盖 (net/http transport) |
| 流式执行 | `httpx.stream()` + `iter_lines()` | `bufio.Scanner` + `parseLine()` |
| 协议帧解析 | `struct.unpack(">I", ...)` | `binary.BigEndian.Uint32(...)` |
| 快照管理 | `Sandbox` 类方法 + 实例方法 | `Sandbox` 实例方法 + `Client` 方法 |
| 模板删除 | `Template.delete()` (classmethod) | `Client.DeleteTemplate()` |
| 环境变量 | 全局 `os.environ` 读取 | `os.Getenv` + `firstEnv` 优先级逻辑 |
| 错误处理 | 异常层次 (6 个子类) | `APIError` + sentinel errors + `errors.Is()` |
| E2B 兼容 | 类型转换在 `_policy.py` | 直接使用相同结构体,通过 `json.Marshal` 序列化 |

---

## 10. 总结

1. **双语言覆盖**: Python + Go 两个官方 SDK,覆盖主要开发者群体,核心功能对等
2. **E2B 兼容**: 设计时考虑与 E2B API 兼容,包括 Bearer 鉴权、虚拟主机名模式、per-host transform 规则转换
3. **环境变量配置**: 12-factor 应用风格,Go SDK 同时支持 `CUBE_*` 和 `E2B_*` 前缀
4. **IP 覆盖机制**: 两个 SDK 均支持通过 `CUBE_PROXY_NODE_IP` 绕过 DNS 直连数据面代理
5. **流式执行**: 使用 NDJSON 流实时输出 stdout/stderr/result,同时支持 callback 和 await
6. **完整生命周期**: 创建 → 执行 → 快照 → 回滚 → 克隆 → 销毁
7. **网络策略**: L3/L4 CIDR 过滤 + L7 host/path/SNI 匹配 + 凭据注入 (CubeEgress 集成)
8. **零外部依赖 (Go)**: Go SDK 仅使用标准库,无第三方依赖
