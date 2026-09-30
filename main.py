import asyncio
import random

import aiohttp
import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star


class GroupWelcomePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

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

    # ---------- 入群检测与欢迎 ----------

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE, priority=5)
    async def on_group_message(self, event: AstrMessageEvent):
        """监听群消息，检测入群通知并发送欢迎语"""
        if not self.config.get("enabled", True):
            return

        raw = event.message_obj.raw_message
        # aiocqhttp 适配器会把 OneBot 的 notice 事件原样放在 raw_message 中
        if not isinstance(raw, dict):
            return
        if raw.get("post_type") != "notice" or raw.get("notice_type") != "group_increase":
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

        chain = self._build_chain(welcome, event, new_user_id)
        if image:
            chain.append(Comp.Image(file=image))
        try:
            await event.send(event.chain_result(chain))
        except Exception as e:
            logger.error(f"发送入群欢迎失败: {e}")

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
        self, welcome: str, event: AstrMessageEvent, new_user_id: str
    ) -> list:
        """把欢迎语文本解析为消息链，支持 {at}、{name}、{group} 占位符

        欢迎语可包含多行，多条用换行分隔，发送时随机抽取一条。
        """
        lines = [line.strip() for line in welcome.splitlines() if line.strip()]
        if len(lines) > 1:
            welcome = random.choice(lines)

        parts = welcome.split("{at}")
        chain: list = []
        sender_name = "新朋友"
        try:
            sender_name = event.message_obj.sender.nickname or sender_name
        except Exception:
            pass

        for i, part in enumerate(parts):
            if part:
                chain.append(
                    Comp.Plain(
                        text=part.replace("{name}", sender_name).replace(
                            "{group}", str(event.message_obj.group_id)
                        )
                    )
                )
            if i < len(parts) - 1:
                chain.append(Comp.At(qq=new_user_id))
        return chain

    # ---------- 管理指令 ----------
    # 注意：permission_type 装饰器不支持叠加在 command_group 上（v4.28.1 会报错），
    # 因此管理员校验在各指令函数内部进行。

    @filter.command_group("欢迎")
    def welcome_group(self):
        """入群欢迎管理指令组（仅管理员）"""

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

    @welcome_group.command("设置")
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

    @welcome_group.command("图片")
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

    @welcome_group.command("查看")
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

    @welcome_group.command("删除")
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
