"""
AstrBot plugin: remote Windows MCP personal computer control.

AstrBot itself runs on Linux. This plugin never controls the Linux host directly;
it routes explicit personal Windows operations to a configured remote windows-mcp
server.
"""

import asyncio
import json
import os
import time
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

try:
    from aiohttp import web
except Exception:  # pragma: no cover - aiohttp is expected in AstrBot runtime
    web = None


class WindowsMcpControlPlugin(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        self.config = config
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
        self.status_report_enabled: bool = True
        self.status_report_host: str = "0.0.0.0"
        self.status_report_port: int = 8765
        self.status_report_token: str = ""
        self.status_report_timeout_seconds: int = 300
        self.status_report_stale_message: str = "好像网络不好，我这边没收到电脑的最新状态。"
        self.status_storage_path: str = os.path.join(os.path.dirname(__file__), "windows_status_cache.json")
        self.status_query_keywords: list[str] = [
            "电脑在干嘛",
            "电脑在做什么",
            "电脑现在干嘛",
            "电脑现在状态",
            "当前电脑状态",
            "前台是什么",
            "后台有什么",
        ]
        self._status_cache: dict[str, Any] = {}
        self._status_lock = asyncio.Lock()
        self._status_app = None
        self._status_runner = None
        self._status_site = None
        self._status_server_task = None
        self._load_config()
        self._load_status_cache()
        if self.status_report_enabled:
            try:
                self._status_server_task = asyncio.create_task(self._start_status_server())
            except RuntimeError:
                logger.warning("[WindowsMcpControl] 事件循环未就绪，状态上报服务未启动")

    def _load_config(self) -> None:
        """Load plugin configuration."""
        try:
            config = self.config if self.config is not None else self.context.get_config()
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
                self.status_report_enabled = bool(config.get("status_report_enabled", self.status_report_enabled))
                host = config.get("status_report_host", self.status_report_host)
                if isinstance(host, str) and host.strip():
                    self.status_report_host = host.strip()
                try:
                    self.status_report_port = int(config.get("status_report_port", self.status_report_port))
                except (TypeError, ValueError):
                    pass
                token = config.get("status_report_token", self.status_report_token)
                if isinstance(token, str):
                    self.status_report_token = token.strip()
                try:
                    self.status_report_timeout_seconds = int(
                        config.get("status_report_timeout_seconds", self.status_report_timeout_seconds)
                    )
                except (TypeError, ValueError):
                    pass
                stale_message = config.get("status_report_stale_message", self.status_report_stale_message)
                if isinstance(stale_message, str) and stale_message.strip():
                    self.status_report_stale_message = stale_message.strip()
                storage_path = config.get("status_storage_path", self.status_storage_path)
                if isinstance(storage_path, str) and storage_path.strip():
                    self.status_storage_path = storage_path.strip()
                query_kw_str = config.get("status_query_keywords", "")
                if query_kw_str:
                    self.status_query_keywords = [k.strip() for k in query_kw_str.split(",") if k.strip()]
        except Exception as e:
            logger.warning(f"[WindowsMcpControl] 配置加载失败，使用默认值: {e}")

    def _load_status_cache(self) -> None:
        try:
            if os.path.exists(self.status_storage_path):
                with open(self.status_storage_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    self._status_cache = data
        except Exception as e:
            logger.warning(f"[WindowsMcpControl] 状态缓存读取失败: {e}")

    def _save_status_cache_sync(self) -> None:
        try:
            os.makedirs(os.path.dirname(self.status_storage_path), exist_ok=True)
            with open(self.status_storage_path, "w", encoding="utf-8") as f:
                json.dump(self._status_cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[WindowsMcpControl] 状态缓存写入失败: {e}")

    async def _start_status_server(self) -> None:
        if web is None:
            logger.warning("[WindowsMcpControl] aiohttp 不可用，状态上报服务未启动")
            return
        try:
            app = web.Application(client_max_size=256 * 1024)
            app.router.add_get("/health", self._handle_status_health)
            app.router.add_post("/windows-status/report", self._handle_status_report)
            self._status_app = app
            self._status_runner = web.AppRunner(app)
            await self._status_runner.setup()
            self._status_site = web.TCPSite(self._status_runner, self.status_report_host, self.status_report_port)
            await self._status_site.start()
            logger.info(
                f"[WindowsMcpControl] Windows 状态上报服务已启动: "
                f"{self.status_report_host}:{self.status_report_port}"
            )
        except Exception as e:
            logger.error(f"[WindowsMcpControl] Windows 状态上报服务启动失败: {e}")

    async def _stop_status_server(self) -> None:
        if self._status_server_task and not self._status_server_task.done():
            self._status_server_task.cancel()
            try:
                await self._status_server_task
            except asyncio.CancelledError:
                pass
        self._status_server_task = None
        if self._status_runner is not None:
            try:
                await self._status_runner.cleanup()
            except Exception as e:
                logger.warning(f"[WindowsMcpControl] 状态上报服务关闭失败: {e}")
        self._status_app = None
        self._status_runner = None
        self._status_site = None

    async def _handle_status_health(self, request):
        return web.json_response({"ok": True, "service": "windows-mcp-status"})

    def _check_report_token(self, request, payload: dict[str, Any]) -> bool:
        if not self.status_report_token:
            return True
        auth = request.headers.get("Authorization", "")
        if auth == f"Bearer {self.status_report_token}":
            return True
        return payload.get("token") == self.status_report_token

    async def _handle_status_report(self, request):
        try:
            payload = await request.json()
        except Exception:
            return web.json_response({"ok": False, "error": "invalid json"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "json object required"}, status=400)
        if not self._check_report_token(request, payload):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)

        payload.pop("token", None)
        now = time.time()
        payload["received_at"] = now
        payload["received_at_text"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
        async with self._status_lock:
            self._status_cache = payload
            self._save_status_cache_sync()
        return web.json_response({"ok": True, "received_at": now})

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

    def _status_age_seconds(self) -> float | None:
        received_at = self._status_cache.get("received_at")
        if not isinstance(received_at, (int, float)):
            return None
        return max(0.0, time.time() - float(received_at))

    def _format_duration(self, seconds: float | int | None) -> str:
        if seconds is None:
            return "未知"
        seconds = int(max(0, seconds))
        if seconds < 60:
            return f"{seconds}秒"
        minutes = seconds // 60
        if minutes < 60:
            return f"{minutes}分钟"
        hours = minutes // 60
        rest = minutes % 60
        return f"{hours}小时{rest}分钟" if rest else f"{hours}小时"

    def _shorten(self, value: Any, limit: int = 120) -> str:
        text = str(value or "").strip()
        if len(text) <= limit:
            return text
        return text[: limit - 1] + "…"

    def _format_windows_cached_status(self) -> str:
        if not self._status_cache:
            return self.status_report_stale_message

        age = self._status_age_seconds()
        if age is None or age > self.status_report_timeout_seconds:
            return self.status_report_stale_message

        data = self._status_cache
        foreground = data.get("foreground") if isinstance(data.get("foreground"), dict) else {}
        system = data.get("system") if isinstance(data.get("system"), dict) else {}
        background_windows = data.get("background_windows") or []
        top_processes = data.get("top_processes") or []
        idle_seconds = data.get("idle_seconds")

        lines = [f"电脑状态刚刚更新过，约{self._format_duration(age)}前。"]
        if foreground:
            process = self._shorten(foreground.get("process") or foreground.get("process_name") or "未知进程", 48)
            title = self._shorten(foreground.get("title") or "无标题窗口", 96)
            lines.append(f"前台在用 {process}：{title}")
        if idle_seconds is not None:
            lines.append(f"最近空闲约 {self._format_duration(idle_seconds)}。")
        if isinstance(system, dict):
            bits = []
            if system.get("cpu_percent") is not None:
                bits.append(f"CPU {system.get('cpu_percent')}%")
            if system.get("memory_percent") is not None:
                bits.append(f"内存 {system.get('memory_percent')}%")
            if bits:
                lines.append("系统占用：" + "，".join(bits))
        if isinstance(background_windows, list) and background_windows:
            names = []
            for item in background_windows[:8]:
                if isinstance(item, dict):
                    names.append(self._shorten(item.get("process") or item.get("title") or "未知", 24))
                else:
                    names.append(self._shorten(item, 24))
            if names:
                lines.append("后台窗口有：" + "、".join(names))
        if isinstance(top_processes, list) and top_processes:
            names = []
            for item in top_processes[:6]:
                if isinstance(item, dict):
                    cpu = item.get("cpu_percent")
                    mem = item.get("memory_mb")
                    name = self._shorten(item.get("name") or item.get("process") or "未知", 24)
                    suffix = []
                    if cpu is not None:
                        suffix.append(f"CPU {cpu}%")
                    if mem is not None:
                        suffix.append(f"{mem}MB")
                    names.append(f"{name}({'/'.join(suffix)})" if suffix else name)
                else:
                    names.append(self._shorten(item, 24))
            if names:
                lines.append("高占用进程：" + "、".join(names))
        return "\n".join(lines)

    def _has_status_query_intent(self, text: str) -> bool:
        lowered = text.lower().strip()
        if not lowered:
            return False
        return any(keyword.lower() in lowered for keyword in self.status_query_keywords)

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


    @filter.llm_tool(name="get_windows_cached_status")
    async def get_windows_cached_status(self, event: AstrMessageEvent) -> str:
        """Check what the user's Windows computer is currently doing.

        Use this when the user asks what their computer is doing, what is open in
        front, or what is running in the background. Reply naturally as if you took
        a quick look. Do not mention tools, MCP, cache, reports, reporters,
        webhooks, interfaces, screenshots, or any implementation details.
        """
        if not self._is_admin(event):
            return "权限拒绝：此功能仅限管理员使用。"
        return self._format_windows_cached_status()

    async def windows_mcp_list_tools(self, **kwargs) -> str:
        """List all available windows-mcp tools for manual diagnostics."""
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

    @filter.command("wmcp_tools", alias=["电脑控制工具"], description="列出 windows-mcp 可用工具（仅管理员）")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def wmcp_tools(self, event: AstrMessageEvent):
        """List windows-mcp tools without exposing this as an LLM tool."""
        yield event.plain_result(await self.windows_mcp_list_tools())

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
            f"状态上报服务: {'开启' if self.status_report_enabled else '关闭'}",
            f"状态上报地址: {self.status_report_host}:{self.status_report_port}",
            f"状态超时: {self.status_report_timeout_seconds}秒",
        ]
        age = self._status_age_seconds()
        status_lines.append(
            f"最近上报: {self._format_duration(age)}前" if age is not None else "最近上报: 无"
        )

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


    @filter.command("wmcp_cached_status", alias=["电脑缓存状态"], description="查看 Windows 主动上报的缓存状态")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def wmcp_cached_status(self, event: AstrMessageEvent):
        yield event.plain_result(self._format_windows_cached_status())

    @filter.command("wmcp_reload_status", alias=["重载电脑状态服务"], description="重新加载配置并重启 Windows 状态上报服务")
    @filter.permission_type(filter.PermissionType.ADMIN)
    async def wmcp_reload_status(self, event: AstrMessageEvent):
        await self._stop_status_server()
        self._load_config()
        self._load_status_cache()
        if self.status_report_enabled:
            await self._start_status_server()
            yield event.plain_result(f"状态上报服务已重载: {self.status_report_host}:{self.status_report_port}")
        else:
            yield event.plain_result("状态上报服务已关闭。")

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
        await self._stop_status_server()
        logger.info("[WindowsMcpControl] 插件已卸载")
