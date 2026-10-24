import asyncio
import random
from pathlib import Path

import aiohttp
import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request

# 插件页可编辑的配置项
EDITABLE_KEYS = {
    "enabled",
    "leave_enabled",
    "global_welcome",
    "global_image",
    "global_leave",
    "welcome_delay_sec",
    "blacklist",
    "whitelist",
    "group_welcomes",
    "group_leaves",
}

# 插件页 Web API 路由需要以插件名开头（面板会以 /api/v1/plugins/extensions/<插件名>/... 转发）
PLUGIN_NAME = "astrbot_plugin_group_welcome"
API_CONFIG_ROUTE = f"/{PLUGIN_NAME}/welcome/config"


class GroupWelcomePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        context.register_web_api(
            API_CONFIG_ROUTE,
            self.api_get_config,
            ["GET"],
            "获取入群欢迎插件配置",
        )
        context.register_web_api(
            API_CONFIG_ROUTE,
            self.api_post_config,
            ["POST"],
            "保存入群欢迎插件配置",
        )

    # ---------- 插件页 Web API ----------

    def _public_config(self) -> dict:
        """返回给插件页的配置快照（已做类型规整）"""
        def _str_list(value) -> list[str]:
            return [str(item) for item in (value or []) if str(item).strip()]

        def _group_rows(value) -> list[dict]:
            rows = []
            for item in value or []:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    {
                        "group_id": str(item.get("group_id", "")),
                        "welcome": str(item.get("welcome", "")),
                        "image": str(item.get("image", "")),
                        "delay": str(item.get("delay", "")),
                        "leave": str(item.get("leave", "")),
                    }
                )
            return rows

        try:
            delay = int(self.config.get("welcome_delay_sec", 0) or 0)
        except (TypeError, ValueError):
            delay = 0

        return {
            "enabled": bool(self.config.get("enabled", True)),
            "leave_enabled": bool(self.config.get("leave_enabled", True)),
            "global_welcome": str(self.config.get("global_welcome", "")),
            "global_image": str(self.config.get("global_image", "")),
            "global_leave": str(self.config.get("global_leave", "")),
            "welcome_delay_sec": max(delay, 0),
            "blacklist": _str_list(self.config.get("blacklist")),
            "whitelist": _str_list(self.config.get("whitelist")),
            "group_welcomes": _group_rows(self.config.get("group_welcomes")),
            "group_leaves": _group_rows(self.config.get("group_leaves")),
        }

    async def api_get_config(self):
        """GET /welcome/config：读取配置"""
        return json_response({"status": "ok", "data": self._public_config()})

    async def api_post_config(self):
        """POST /welcome/config：保存配置"""
        data = await request.json(default=None)
        if not isinstance(data, dict):
            return error_response("请求体格式错误")

        updated: dict = {}

        for key in ("enabled", "leave_enabled"):
            if key in data:
                updated[key] = bool(data[key])

        for key in ("global_welcome", "global_image", "global_leave"):
            if key in data:
                updated[key] = str(data[key] or "")

        if "welcome_delay_sec" in data:
            try:
                updated["welcome_delay_sec"] = max(int(data["welcome_delay_sec"] or 0), 0)
            except (TypeError, ValueError):
                return error_response("延迟秒数必须是整数")

        for key in ("blacklist", "whitelist"):
            if key in data:
                if not isinstance(data[key], list):
                    return error_response(f"{key} 必须是列表")
                updated[key] = [
                    str(item).strip() for item in data[key] if str(item).strip()
                ]

        if "group_welcomes" in data:
            rows = data["group_welcomes"]
            if not isinstance(rows, list):
                return error_response("group_welcomes 必须是列表")
            entries = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                group_id = str(row.get("group_id", "")).strip()
                if not group_id:
                    continue
                entries.append(
                    {
                        "__template_key": "group_welcome",
                        "group_id": group_id,
                        "welcome": str(row.get("welcome", "") or ""),
                        "image": str(row.get("image", "") or ""),
                        "delay": str(row.get("delay", "") or ""),
                    }
                )
            updated["group_welcomes"] = entries

        if "group_leaves" in data:
            rows = data["group_leaves"]
            if not isinstance(rows, list):
                return error_response("group_leaves 必须是列表")
            entries = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                group_id = str(row.get("group_id", "")).strip()
                if not group_id:
                    continue
                entries.append(
                    {
                        "__template_key": "group_leave",
                        "group_id": group_id,
                        "leave": str(row.get("leave", "") or ""),
                    }
                )
            updated["group_leaves"] = entries

        try:
            self.config.update(updated)
            self.config.save_config()
        except Exception as e:
            logger.error(f"保存插件配置失败: {e}")
            return error_response("保存配置失败，请查看日志")

        return json_response({"status": "ok", "data": self._public_config()})

    # ---------- 配置读写 ----------

    def _parse_group_welcomes(self) -> dict[str, dict[str, str]]:
        """把配置中的 template_list 条目解析为 {群号: {"welcome": 文本, "image": 图片}}

        兼容两种条目：template_list 的 dict 条目（含 group_id/welcome/image 字段）
        和旧版字符串条目（"群号|欢迎语"）。
        """
        result: dict[str, dict[str, str]] = {}
        for item in self.config.get("group_welcomes") or []:
            if isinstance(item, dict):
                group_id = str(item.get("group_id", "")).strip()
                welcome = str(item.get("welcome", "")).strip()
                image = str(item.get("image", "")).strip()
            else:
                item = str(item)
                if "|" not in item:
                    continue
                group_id, _, welcome = item.partition("|")
                group_id, welcome = group_id.strip(), welcome.strip()
                image = ""
            if group_id and (welcome or image):
                result[group_id] = {
                    "welcome": welcome,
                    "image": image,
                    "delay": str(item.get("delay", "")).strip() if isinstance(item, dict) else "",
                }
        return result

    def _write_group_welcomes(
        self, welcomes: dict[str, dict[str, str]]
    ) -> None:
        """把 {群号: {"welcome": 文本, "image": 图片, "delay": 延迟}} 写回 template_list 配置并保存"""
        self.config["group_welcomes"] = [
            {
                "__template_key": "group_welcome",
                "group_id": group_id,
                "welcome": rule.get("welcome", ""),
                "image": rule.get("image", ""),
                "delay": rule.get("delay", ""),
            }
            for group_id, rule in welcomes.items()
        ]
        self.config.save_config()

    def _parse_group_leaves(self) -> dict[str, str]:
        """把配置中的 group_leaves 条目解析为 {群号: 退群提示语}"""
        result: dict[str, str] = {}
        for item in self.config.get("group_leaves") or []:
            if not isinstance(item, dict):
                continue
            group_id = str(item.get("group_id", "")).strip()
            text = str(item.get("leave", "")).strip()
            if group_id and text:
                result[group_id] = text
        return result

    # ---------- 入群检测与欢迎 ----------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=5)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群消息，检测入群/退群通知并发送欢迎语或退群提示"""
        raw = event.message_obj.raw_message
        # aiocqhttp 适配器会把 OneBot 的 notice 事件原样放在 raw_message 中
        if not isinstance(raw, dict) or raw.get("post_type") != "notice":
            return

        if raw.get("notice_type") == "group_increase":
            await self._handle_join(event, raw)
        elif raw.get("notice_type") == "group_decrease":
            await self._handle_leave(event, raw)

    async def _handle_join(self, event: AstrMessageEvent, raw: dict):
        """处理入群通知，发送欢迎语"""
        if not self.config.get("enabled", True):
            return

        group_id = str(event.message_obj.group_id)
        new_user_id = str(raw.get("user_id", ""))
        logger.info(f"检测到新人入群: 群 {group_id}, 用户 {new_user_id}")

        if not self._group_allowed(group_id):
            return

        welcomes = self._parse_group_welcomes()
        rule = welcomes.get(group_id) or {}
        welcome = rule.get("welcome") or self.config.get(
            "global_welcome", "欢迎 {at} 加入本群！"
        )
        image = rule.get("image") or self.config.get("global_image", "")
        if not welcome and not image:
            return

        # 延迟欢迎：群配置优先，回退全局延迟
        try:
            delay = int(rule.get("delay") or self.config.get("welcome_delay_sec", 0) or 0)
        except (TypeError, ValueError):
            delay = 0
        if delay > 0:
            logger.info(f"群 {group_id} 延迟 {delay} 秒后发送欢迎")
            await asyncio.sleep(delay)
            if not self.config.get("enabled", True):
                return

        member_name = await self._fetch_member_name(event, group_id, new_user_id)
        chain = self._build_chain(welcome, event, new_user_id, member_name)
        if image:
            chain.append(Comp.Image(file=image))
        try:
            await event.send(event.chain_result(chain))
        except Exception as e:
            logger.error(f"发送入群欢迎失败: {e}")

    async def _handle_leave(self, event: AstrMessageEvent, raw: dict):
        """处理退群通知，发送退群提示"""
        if not self.config.get("leave_enabled", True):
            return

        # kick_me 是机器人自己被踢，不需要提示
        if raw.get("sub_type") == "kick_me":
            return

        group_id = str(event.message_obj.group_id)
        leave_user_id = str(raw.get("user_id", ""))
        logger.info(f"检测到成员退群: 群 {group_id}, 用户 {leave_user_id}")

        leave_text = (
            self._parse_group_leaves().get(group_id)
            or self.config.get("global_leave", "{user} 退出了本群")
        ).strip()
        if not leave_text:
            return

        text = (
            leave_text.replace("{user}", leave_user_id)
            .replace("{group}", group_id)
            .replace("{name}", await self._fetch_member_name(event, group_id, leave_user_id))
        )
        try:
            await event.send(event.plain_result(text))
        except Exception as e:
            logger.error(f"发送退群提示失败: {e}")

    def _group_allowed(self, group_id: str) -> bool:
        """根据黑白名单判断该群是否启用欢迎

        黑白名单共存：白名单非空时仅白名单中的群欢迎；黑名单始终生效且优先级更高。
        """
        def _norm(items) -> list[str]:
            return [str(i) for i in (items or [])]

        if group_id in _norm(self.config.get("blacklist")):
            return False
        whitelist = _norm(self.config.get("whitelist"))
        if whitelist:
            return group_id in whitelist
        return True

    def _build_chain(
        self,
        welcome: str,
        event: AstrMessageEvent,
        new_user_id: str,
        member_name: str = "",
    ) -> list:
        """把欢迎语文本解析为消息链，支持 {at}、{name}、{user}、{group} 占位符

        欢迎语可包含多行，多条用换行分隔，发送时随机抽取一条。
        """
        lines = [line.strip() for line in welcome.splitlines() if line.strip()]
        if len(lines) > 1:
            welcome = random.choice(lines)

        parts = welcome.split("{at}")
        chain: list = []
        sender_name = member_name or "新朋友"
        if not member_name:
            try:
                sender_name = event.message_obj.sender.nickname or sender_name
            except Exception:
                pass

        for i, part in enumerate(parts):
            if part:
                chain.append(
                    Comp.Plain(
                        text=part.replace("{name}", sender_name)
                        .replace("{user}", new_user_id)
                        .replace("{group}", str(event.message_obj.group_id))
                    )
                )
            if i < len(parts) - 1:
                chain.append(Comp.At(qq=new_user_id))
        return chain

    async def _fetch_member_name(
        self, event: AstrMessageEvent, group_id: str, user_id: str
    ) -> str:
        """通过机器人 API 获取成员群昵称/昵称，失败时返回空字符串"""
        bot = getattr(event, "bot", None)
        if not bot or not user_id:
            return ""
        try:
            info = await bot.call_action(
                action="get_group_member_info",
                group_id=int(group_id),
                user_id=int(user_id),
                no_cache=False,
            )
            name = (info or {}).get("card") or (info or {}).get("nickname") or ""
            if not name:
                info = await bot.call_action(
                    action="get_stranger_info", user_id=int(user_id), no_cache=False
                )
                name = (info or {}).get("nick") or (info or {}).get("nickname") or ""
            return str(name)
        except Exception as e:
            logger.debug(f"获取成员昵称失败: {e}")
            return ""

    # ---------- 管理指令 ----------
    # 单独注册指令（不使用 command_group），管理员校验在各指令函数内部进行。

    def _is_admin(self, event: AstrMessageEvent) -> bool:
        try:
            return bool(event.is_admin())
        except Exception:
            return False

    def _check_admin(self, event: AstrMessageEvent) -> str | None:
        """非管理员时返回提示语，否则返回 None"""
        if not self._is_admin(event):
            return "该指令仅管理员可用。"
        return None

    @filter.command("欢迎设置")
    async def welcome_set(self, event: AstrMessageEvent, text: str):
        """为当前群设置欢迎语，例如：/欢迎设置 欢迎 {at} 加入本群！"""
        if msg := self._check_admin(event):
            yield event.plain_result(msg)
            return
        if not event.message_obj.group_id:
            yield event.plain_result("请在群聊中使用该指令。")
            return
        group_id = str(event.message_obj.group_id)
        welcomes = self._parse_group_welcomes()
        rule = dict(welcomes.get(group_id) or {"welcome": "", "image": ""})
        rule["welcome"] = text
        welcomes[group_id] = rule
        self._write_group_welcomes(welcomes)
        yield event.plain_result(f"已设置群 {group_id} 的欢迎语。")

    @filter.command("欢迎图片")
    async def welcome_image(self, event: AstrMessageEvent, image: str = ""):
        """为当前群设置欢迎图片：回复一条图片消息使用该图，或填图片 URL/本地路径，填「无/清除/删除」清除"""
        if msg := self._check_admin(event):
            yield event.plain_result(msg)
            return
        if not event.message_obj.group_id:
            yield event.plain_result("请在群聊中使用该指令。")
            return
        group_id = str(event.message_obj.group_id)
        welcomes = self._parse_group_welcomes()
        rule = dict(welcomes.get(group_id) or {"welcome": "", "image": ""})
        yields = ""

        if not image:
            # 未填参数：尝试从引用（回复）的消息中提取图片并保存到本地
            local_path = await self._save_reply_image(event, group_id)
            if local_path:
                rule["image"] = local_path
                yields = "已将回复的图片保存到本地，并设为群 {g} 的欢迎图片。"
            else:
                yields = "未找到图片。请回复一条图片消息后使用该指令，或直接填图片 URL/本地路径。"
        elif image in ("无", "清除", "删除"):
            rule["image"] = ""
            yields = "已清除群 {g} 的欢迎图片。"
        else:
            rule["image"] = image
            yields = "已设置群 {g} 的欢迎图片。"

        welcomes[group_id] = rule
        self._write_group_welcomes(welcomes)
        yield event.plain_result(yields.format(g=group_id))

    async def _save_reply_image(self, event: AstrMessageEvent, group_id: str) -> str:
        """从引用（回复）的消息中提取图片，下载保存到本地 data 目录，返回本地路径"""
        img_url = ""
        for seg in event.message_obj.message:
            if type(seg).__name__ == "Reply" and getattr(seg, "chain", None):
                for sub in seg.chain:
                    if type(sub).__name__ == "Image":
                        img_url = getattr(sub, "url", "") or getattr(sub, "file", "")
                        break
            if img_url:
                break
        if not img_url:
            return ""
        try:
            import aiohttp

            img_dir = Path("data/plugin_data/astrbot_plugin_group_welcome/images")
            img_dir.mkdir(parents=True, exist_ok=True)
            ext = ".jpg"
            if ".png" in img_url.lower():
                ext = ".png"
            elif ".gif" in img_url.lower():
                ext = ".gif"
            local_path = img_dir / f"group_{group_id}{ext}"
            async with aiohttp.ClientSession() as session:
                async with session.get(img_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        logger.error(f"下载图片失败: HTTP {resp.status}")
                        return ""
                    local_path.write_bytes(await resp.read())
            return str(local_path)
        except Exception as e:
            logger.error(f"保存引用图片失败: {e}")
            return ""

    @filter.command("欢迎查看")
    async def welcome_get(self, event: AstrMessageEvent):
        """查看当前群的欢迎语"""
        if msg := self._check_admin(event):
            yield event.plain_result(msg)
            return
        group_id = str(event.message_obj.group_id)
        rule = self._parse_group_welcomes().get(group_id) or {}
        if rule.get("welcome") or rule.get("image"):
            yield event.plain_result(f"当前群欢迎语：{rule.get('welcome', '（无文本）')}")
            if rule.get("image"):
                yield event.plain_result(f"当前群欢迎图片：{rule['image']}")
        else:
            yield event.plain_result("当前群未单独设置欢迎语，将使用全局默认欢迎语。")

    @filter.command("欢迎删除")
    async def welcome_del(self, event: AstrMessageEvent):
        """删除当前群的欢迎语，恢复使用全局默认"""
        if msg := self._check_admin(event):
            yield event.plain_result(msg)
            return
        group_id = str(event.message_obj.group_id)
        welcomes = self._parse_group_welcomes()
        if group_id in welcomes:
            del welcomes[group_id]
            self._write_group_welcomes(welcomes)
            yield event.plain_result("已删除当前群的欢迎语，将使用全局默认欢迎语。")
        else:
            yield event.plain_result("当前群未设置欢迎语。")

    async def terminate(self):
        """插件被卸载/停用时调用"""
        pass
