"""
AstrBot plugin: remote Windows MCP personal computer control.

AstrBot itself runs on Linux. This plugin never controls the Linux host directly;
it routes explicit personal Windows operations to a configured remote windows-mcp
server.
"""

import asyncio
import json
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star


class WindowsMcpControlPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self.mcp_server_name: str = "windows-mcp"
        self.control_keywords: list[str] = [
            "控制电脑",
            "控制个人电脑",
            "操作电脑",
            "远程控制",
            "电脑操作",
            "管理电脑",
        ]
        self.action_keywords: list[str] = [
            "关机",
            "关闭电脑",
            "关闭主机",
            "重启",
            "重启电脑",
            "锁屏",
            "注销",
            "睡眠",
            "休眠",
            "截图",
            "截屏",
            "屏幕截图",
            "打开",
            "启动",
            "运行",
            "关闭程序",
            "结束进程",
            "杀进程",
            "查看进程",
            "文件",
            "文件夹",
            "目录",
            "复制",
            "移动",
            "删除",
            "新建",
            "重命名",
            "下载",
            "上传",
            "查看系统",
            "系统信息",
            "磁盘",
            "内存",
            "cpu",
            "网络",
            "音量",
            "静音",
            "键盘",
            "鼠标",
            "点击",
            "输入",
        ]
        self.server_keywords: list[str] = ["服务器", "容器", "docker", "linux", "ssh", "vps", "云主机"]
        self.strict_mode: bool = True
        self.allowed_sender_ids: list[str] = []
        self._cached_admins: set[str] = set()
        self._load_config()

    def _load_config(self) -> None:
        """Load plugin configuration."""
        try:
            config = self.context.get_config()
            if hasattr(config, "get"):
                mcp_server_name = config.get("windows_mcp_server_name", self.mcp_server_name)
                if isinstance(mcp_server_name, str) and mcp_server_name.strip():
                    self.mcp_server_name = mcp_server_name.strip()
                kw_str = config.get("control_keywords", "")
                if kw_str:
                    self.control_keywords = [k.strip() for k in kw_str.split(",") if k.strip()]
                action_kw_str = config.get("action_keywords", "")
                if action_kw_str:
                    self.action_keywords = [k.strip() for k in action_kw_str.split(",") if k.strip()]
                self.strict_mode = config.get("strict_mode", True)
                allowlist_str = config.get("tool_allowed_sender_ids", "")
                if allowlist_str:
                    self.allowed_sender_ids = [item.strip() for item in allowlist_str.split(",") if item.strip()]
                admins = config.get("admins_id", []) or config.get("admins", []) or []
                self._cached_admins = {str(a) for a in admins}
        except Exception as e:
            logger.warning(f"[WindowsMcpControl] 配置加载失败，使用默认值: {e}")

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        """Check whether sender may use remote Windows control tools."""
        sender_id = str(event.get_sender_id())
        if sender_id in self._cached_admins or sender_id in self.allowed_sender_ids:
            return True
        try:
            is_admin = getattr(event, "is_admin", None)
            if callable(is_admin) and is_admin():
                return True
        except:
            pass
        return False

    def _looks_like_server_operation(self, text: str) -> bool:
        text_lower = text.lower()
        return any(keyword in text_lower for keyword in self.server_keywords)

    def _has_control_intent(self, text: str) -> bool:
        """Detect explicit remote personal Windows PC operation intent.

        AstrBot runs on Linux, so desktop actions such as opening Calculator,
        taking screenshots, clicking, typing, and app/process/file operations are
        interpreted as remote Windows operations by default. Server/container/Linux
        targets are excluded unless the user explicitly says they mean the PC.
        """
        if not self.strict_mode:
            return True

        text_lower = text.lower().strip()
        if not text_lower:
            return False

        if self._looks_like_server_operation(text_lower):
            return any(keyword in text_lower for keyword in self.control_keywords)

        keywords = tuple(dict.fromkeys(self.control_keywords + self.action_keywords))
        return any(keyword in text_lower for keyword in keywords)

    async def _call_mcp_tool(self, tool_name: str, arguments: dict[str, Any]) -> str:
        """Call a tool on the configured windows-mcp server."""
        try:
            tool_mgr = self.context.get_llm_tool_manager()
            if tool_mgr is None:
                return "错误：LLM 工具管理器不可用。"

            # 访问 mcp_server_runtime_view
            runtime = tool_mgr.mcp_server_runtime_view
            if self.mcp_server_name not in runtime:
                return f"错误：MCP 服务器 '{self.mcp_server_name}' 未运行或未连接。可用: {list(runtime.keys())}"

            server_rt = runtime[self.mcp_server_name]
            
            # 校验工具是否存在
            tools = [t.name for t in server_rt.client.tools]
            if tool_name not in tools:
                return f"错误：MCP 工具 '{tool_name}' 不存在。可用: {tools}"

            # 交互式工具校验
            interactive_tools = {"Type", "Click", "Move", "Scroll"}
            if tool_name in interactive_tools and not any(key in arguments for key in ("loc", "label")):
                return f"错误：调用 {tool_name} 必须提供 loc 或 label 参数。"

            # 调用工具
            if not hasattr(self, "_mcp_call_lock"):
                self._mcp_call_lock = asyncio.Lock()
            async with self._mcp_call_lock:
                client = server_rt.client
                from datetime import timedelta
                result = await client.call_tool_with_reconnect(
                    tool_name=tool_name,
                    arguments=arguments,
                    read_timeout_seconds=timedelta(seconds=60),
                )
            return str(result)

        except Exception as e:
            logger.error(f"[WindowsMcpControl] MCP 调用失败: {e}")
            return f"MCP 调用异常: {e}"

    @filter.llm_tool(name="windows_mcp_control")
    async def windows_mcp_control(
        self,
        event: AstrMessageEvent,
        user_message: str,
        mcp_tool_name: str,
        mcp_arguments: str,
    ) -> str:
        """Control the user's remote personal Windows computer through windows-mcp.

        Runtime boundary: AstrBot is running on Linux. This tool does not operate
        on the Linux host. It forwards explicit desktop/computer actions to the
        configured remote Windows MCP server.

        Use this tool for explicit remote Windows PC operations, including direct
        actions such as opening Calculator or other Windows apps, shutdown, restart,
        lock screen, screenshot, file/process management, mouse clicks, keyboard
        input, and volume changes. If the user says "打开计算器" or similar desktop
        app commands, treat that as a Windows PC operation. Mentions of server,
        container, Docker, Linux, SSH, VPS, or cloud host are not personal-PC
        targets unless the user clearly says so.

        Args:
            user_message(string): User original message.
            mcp_tool_name(string): windows-mcp tool name.
            mcp_arguments(string): JSON object string arguments, or {}.
        """
        if not self._is_admin(event):
            logger.warning(f"[WindowsMcpControl] 非管理员用户 {event.get_sender_id()} 尝试调用电脑控制工具")
            return "权限拒绝：此功能仅限管理员使用。"

        if mcp_tool_name in {"Type", "Click", "Move", "Scroll"}:
            try:
                preview_arguments = json.loads(mcp_arguments) if mcp_arguments else {}
            except json.JSONDecodeError:
                preview_arguments = {}
            if not isinstance(preview_arguments, dict):
                preview_arguments = {}
            if not any(key in preview_arguments for key in ("loc", "label")):
                return f"参数预检失败：调用 {mcp_tool_name} 必须提供 loc 或 label，请先用 Snapshot 定位目标。"

        try:
            arguments = json.loads(mcp_arguments) if mcp_arguments else {}
            if not isinstance(arguments, dict):
                return "参数解析失败：mcp_arguments 必须是 JSON 对象字符串。"
        except json.JSONDecodeError as e:
            return f"参数解析失败：mcp_arguments 不是有效的 JSON。错误: {e}"

        logger.info(f"[WindowsMcpControl] 管理员 {event.get_sender_id()} 调用 MCP 工具: {mcp_tool_name}, 参数: {arguments}")
        result = await self._call_mcp_tool(mcp_tool_name, arguments)
        # 只返回结果内容，不暴露工具调用细节
        try:
            if hasattr(result, 'content') and result.content:
                texts = []
                for item in result.content:
                    if hasattr(item, 'text'):
                        texts.append(item.text)
                    else:
                        texts.append(str(item))
                return "\n".join(texts) if texts else "操作完成。"
            return str(result) if result else "操作完成。"
        except Exception:
            return str(result) if result else "操作完成。"

    @filter.llm_tool(name="windows_mcp_list_tools")
    async def windows_mcp_list_tools(self, **kwargs) -> str:
        """List all available windows-mcp tools."""
        try:
            import json
            tool_mgr = self.context.get_llm_tool_manager()
            runtime = tool_mgr.mcp_server_runtime_view
            if self.mcp_server_name not in runtime:
                return f"MCP 服务器 '{self.mcp_server_name}' 未运行。可用: {list(runtime.keys())}"
            server_rt = runtime[self.mcp_server_name]
            tool_info = []
            for tool in server_rt.client.tools:
                info = f"- {tool.name}: {tool.description or '无描述'}"
                if tool.inputSchema:
                    schema_str = json.dumps(tool.inputSchema, ensure_ascii=False, indent=2)
                    info += f"\n  参数: {schema_str}"
                tool_info.append(info)
            header = f"Windows MCP 可用工具列表 ({self.mcp_server_name}):\n"
            return header + "\n".join(tool_info)

        except Exception as e:
            return f"获取工具列表失败: {e}"

    @filter.command("wmcp_status", alias=["电脑控制状态"], description="检查 Windows MCP 控制插件状态")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def wmcp_status(self, event: AstrMessageEvent):
        """Check plugin status and MCP connectivity."""
        status_lines = [
            "Windows MCP 控制插件状态",
            f"MCP 服务器名称: {self.mcp_server_name}",
            "运行边界: AstrBot 在 Linux 上运行，目标是远程 Windows MCP 电脑",
            f"严格模式: {'开启' if self.strict_mode else '关闭'}",
            f"工具白名单: {', '.join(self.allowed_sender_ids) if self.allowed_sender_ids else '未配置'}",
            f"触发关键词: {', '.join(self.control_keywords)}",
            f"动作关键词: {', '.join(self.action_keywords)}",
            f"管理员ID: {event.get_sender_id()}",
        ]

        try:
            tool_mgr = self.context.get_llm_tool_manager()
            if tool_mgr is None:
                status_lines.append("LLM 工具管理器: 不可用")
            else:
                status_lines.append("LLM 工具管理器: 可用")
                runtime = tool_mgr.mcp_server_runtime_view
                status_lines.append(f"运行中 MCP 服务器: {list(runtime.keys())}")
                if self.mcp_server_name in runtime:
                    server_rt = runtime[self.mcp_server_name]
                    status_lines.append(f"目标服务器状态: 已运行")
                    tools = server_rt.client.tools
                    status_lines.append(f"可用工具数: {len(tools)}")
                    status_lines.append(f"工具预览: {[t.name for t in tools[:5]]}...")
                else:
                    status_lines.append(f"目标服务器 '{self.mcp_server_name}': 未在运行中")

        except Exception as e:
            status_lines.append(f"检查状态时发生异常: {e}")

        yield event.plain_result("\n".join(status_lines))

    @filter.command("wmcp_call", alias=["电脑控制"], description="直接调用 windows-mcp 工具（仅管理员）")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def wmcp_call(self, event: AstrMessageEvent, tool_name: str = "", arguments: str = "{}"):
        """Manually call a windows-mcp tool."""
        if not tool_name:
            yield event.plain_result("用法: /wmcp_call <工具名> [JSON参数]")
            return

        try:
            args = json.loads(arguments) if arguments else {}
            if not isinstance(args, dict):
                yield event.plain_result("JSON 参数必须是对象，例如 {}")
                return
        except json.JSONDecodeError as e:
            yield event.plain_result(f"JSON 参数解析失败: {e}")
            return

        result = await self._call_mcp_tool(tool_name, args)
        yield event.plain_result(f"工具: {tool_name}\n参数: {args}\n结果: {result}")

    async def terminate(self) -> None:
        """Clean up resources when plugin unloads."""
        logger.info("[WindowsMcpControl] 插件已卸载")
