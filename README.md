# AstrBot 插件：Windows MCP 电脑控制

## 简介

通过 MCP 协议控制远程个人 Windows 电脑。AstrBot 运行环境是 Linux，本插件不会控制 Linux 宿主机，只会把明确的 Windows 桌面操作转发给配置好的 windows-mcp 服务器。严格权限管控：仅 AstrBot 管理员或插件白名单用户可调用，且必须明确表示 Windows/个人电脑操作时才会执行。

## 功能特性

- **双 LLM 工具注册**：
  - `windows_mcp_control`：调用 windows-mcp 服务器的指定工具来控制电脑
  - `windows_mcp_list_tools`：列出 windows-mcp 服务器上所有可用工具

- **三重权限管控**：
  1. 管理员身份验证：非管理员直接拒绝，不执行任何操作
  2. 控制意图校验：用户消息必须包含"控制电脑""操作电脑"等明确关键词
  3. 严格模式开关：可在配置中关闭意图校验（不推荐）

- **手动指令支持**：
  - `/wmcp_status`（电脑控制状态）：查看插件运行状态和 MCP 连接情况
  - `/wmcp_call <工具名> [JSON参数]`（电脑控制）：直接调用 MCP 工具

## 前置条件

1. 已在 AstrBot 中配置名为 `windows-mcp` 的 MCP 服务器
2. windows-mcp 服务器已启动并可连接
3. 调用者已设置为 AstrBot 管理员

## 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `windows_mcp_server_name` | string | `windows-mcp` | AstrBot 中已配置的 MCP 服务器名称 |
| `control_keywords` | string | `控制电脑,控制个人电脑,...` | 触发控制必须包含的关键词 |
| `strict_mode` | bool | `true` | 严格模式，开启后必须是明确的远程 Windows/个人电脑操作 |
| `tool_allowed_sender_ids` | string | 空 | 额外允许调用 LLM 工具的发送者 ID，逗号分隔，通常留空优先使用 AstrBot 全局 `admins_id` |
| `allowed_commands_hint` | string | `文件管理、进程管理,...` | 允许执行的命令类型提示 |

## 使用方式

### 自然语言方式（推荐）

管理员通过自然语言对话，当明确表示要控制个人电脑时，LLM 会自动调用 `windows_mcp_control` 工具：

```
用户: 打开计算器
LLM: [调用 windows_mcp_control 工具，执行远程 Windows 应用启动]
```

### 手动指令方式

```
/wmcp_status                    # 查看状态
/wmcp_call list_files {"path":"C:\\"}  # 直接调用工具
```

## 安全说明

- 非管理员调用 LLM 工具会被直接拒绝，不会执行任何操作
- 严格模式下，即使用户是管理员，消息中没有明确的控制意图也不会执行
- 所有调用都会记录日志，包括调用者 ID、工具名和参数
- 建议在生产环境中保持严格模式开启

## 安装

将插件文件夹放入 AstrBot 的 `data/plugins/` 目录，重启或热重载即可。