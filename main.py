# -*- coding: utf-8 -*-
import asyncio
import time
from pathlib import Path

import aiohttp
import astrbot.api.message_components as Comp

from astrbot.api import logger
from astrbot.api.event import filter
from astrbot.api.star import Context, Star
from astrbot.core.config.astrbot_config import AstrBotConfig
from astrbot.core.message.components import At
from astrbot.core.platform.astr_message_event import AstrMessageEvent
from astrbot.core.provider.entities import ProviderRequest

from .core.config import PluginConfig
from .core.db import UserProfileDB
from .core.entry import EntryService
from .core.image_template import render_portrait_image
from .core.llm import LLMService
from .core.message import MessageManager
from .core.model import UserProfile, normalize_platform


class PortrayalPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.cfg = PluginConfig(config, context)
        self.db = UserProfileDB(self.cfg)
        self.msg = MessageManager(self.cfg)
        self.entry_service = EntryService(self.cfg)
        self.llm = LLMService(self.cfg)
        self._cleanup_tasks: set[asyncio.Task] = set()

    async def initialize(self):
        pass

    async def terminate(self):
        self.msg.save_cache()
        for task in list(self._cleanup_tasks):
            task.cancel()
        self._cleanup_tasks.clear()

    def _platform_kind(self, event: AstrMessageEvent) -> str:
        plat = ""
        try:
            plat = event.get_platform_name() or ""
        except Exception:
            plat = ""
        if not plat:
            try:
                plat = str(event.get_platform_id() or "")
            except Exception:
                plat = ""
        # unified_msg_origin 里常含平台前缀
        try:
            umo = str(event.unified_msg_origin or "")
            if ":" in umo:
                plat = plat or umo.split(":", 1)[0]
        except Exception:
            pass
        return normalize_platform(plat)

    def _extract_at_ids(self, event: AstrMessageEvent) -> list[str]:
        """兼容 QQ At 组件与微信 @ 文本。"""
        ids: list[str] = []
        try:
            for seg in event.get_messages():
                if isinstance(seg, At):
                    qq = getattr(seg, "qq", None) or getattr(seg, "target", None)
                    if qq is not None and str(qq) not in {"", "0", "all"}:
                        ids.append(str(qq))
                # 部分微信适配把 at 做成 dict-like
                elif isinstance(seg, dict) and str(seg.get("type") or "") in {"at", "mention"}:
                    data = seg.get("data") or {}
                    uid = data.get("qq") or data.get("wxid") or data.get("user_id") or data.get("id")
                    if uid:
                        ids.append(str(uid))
        except Exception:
            pass

        if ids:
            return ids

        # 文本兜底：@昵称（微信常见），无法可靠映射 id 时返回空
        text = (event.message_str or "").strip()
        if "@" in text:
            # 尝试 message 链里找 mention name 映射失败则放弃
            return []
        return []

    async def _fetch_avatar_bytes(self, profile: UserProfile) -> bytes | None:
        user_id = str(profile.user_id or "")
        if not user_id:
            return None
        timeout = aiohttp.ClientTimeout(total=15)
        urls: list[str] = []
        if profile.platform != "wechat":
            urls.append(f"https://q4.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640")
        # 微信头像无稳定公开 CDN，尝试空；后续可接适配器字段
        for url in urls:
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(url) as resp:
                        resp.raise_for_status()
                        return await resp.read()
            except Exception as e:
                logger.warning(f"下载用户头像失败：{e}")
        return None

    def _schedule_card_cleanup(self, path: Path, delay: float = 30.0) -> None:
        """发送完成后延迟删除卡片文件，避免磁盘无限增长。"""

        async def _cleanup() -> None:
            try:
                await asyncio.sleep(delay)
                if path.exists():
                    path.unlink(missing_ok=True)
                    logger.debug(f"已清理画像卡片：{path}")
            except Exception as e:
                logger.debug(f"清理画像卡片失败：{e}")

        try:
            task = asyncio.create_task(_cleanup())
            self._cleanup_tasks.add(task)
            task.add_done_callback(self._cleanup_tasks.discard)
        except RuntimeError:
            # 无 running loop 时忽略
            pass

    async def _build_portrait_image_result(
        self,
        event: AstrMessageEvent,
        *,
        profile: UserProfile,
        content: str,
        command: str,
        message_count: int | None = None,
        query_rounds: int | None = None,
        from_cache: bool = False,
        activity=None,
    ):
        avatar_bytes = await self._fetch_avatar_bytes(profile)
        png_bytes = await asyncio.to_thread(
            render_portrait_image,
            profile=profile,
            content=content,
            command=command,
            avatar_bytes=avatar_bytes,
            platform=getattr(profile, "platform", None),
            message_count=message_count,
            query_rounds=query_rounds,
            from_cache=from_cache,
            activity=activity,
        )

        cards_dir: Path = self.cfg.data_dir / "cards"
        cards_dir.mkdir(parents=True, exist_ok=True)
        safe_id = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in str(profile.user_id or "user")
        )
        out_path = cards_dir / f"portrait_{safe_id}_{int(time.time())}.png"
        await asyncio.to_thread(out_path.write_bytes, png_bytes)
        # 发完约 30 秒后删除临时卡片
        self._schedule_card_cleanup(out_path, delay=30.0)
        return event.chain_result([Comp.Image.fromFileSystem(str(out_path))])

    async def _resolve_profile(
        self, event: AstrMessageEvent, target_id: str, kind: str
    ) -> UserProfile:
        if kind == "wechat":
            data: dict = {"platform": "wechat"}
            # 尽量从事件侧取昵称
            try:
                nick = event.get_sender_name() if str(event.get_sender_id()) == str(target_id) else ""
            except Exception:
                nick = ""
            # 群成员信息（不同适配字段不一）
            bot = event.bot
            try:
                if hasattr(bot, "get_group_member_info"):
                    info = await bot.get_group_member_info(
                        group_id=event.get_group_id(), user_id=target_id
                    )
                    if isinstance(info, dict):
                        data.update(info)
                elif hasattr(bot, "api") and hasattr(bot.api, "call_action"):
                    info = await bot.api.call_action(
                        "get_group_member_info",
                        group_id=event.get_group_id(),
                        user_id=target_id,
                    )
                    if isinstance(info, dict):
                        data.update(info)
            except Exception as e:
                logger.debug(f"wechat member info fallback: {e}")
            if nick and not data.get("nickname"):
                data["nickname"] = nick
            profile = UserProfile.from_wechat_data(target_id, data=data)
        else:
            info = {}
            try:
                info = await event.bot.get_stranger_info(user_id=int(target_id), no_cache=True)
                info = dict(info or {})
            except Exception:
                try:
                    info = await event.bot.get_group_member_info(
                        group_id=int(event.get_group_id()), user_id=int(target_id)
                    )
                    info = dict(info or {})
                except Exception as e:
                    logger.warning(f"获取用户资料失败：{e}")
                    info = {}
            profile = UserProfile.from_qq_data(target_id, data=info)

        if old_profile := self.db.get(target_id):
            profile.portrait = old_profile.portrait
            profile.timestamp = old_profile.timestamp
            profile.last_command = old_profile.last_command
            profile.last_message_count = old_profile.last_message_count
            profile.last_query_rounds = old_profile.last_query_rounds
            profile.last_stats = dict(old_profile.last_stats or {})
        return profile

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self.cfg.inject_prompt:
            return
        if not event.message_str:
            return
        sender_id = event.get_sender_id()
        profile = self.db.get(sender_id)
        if not profile:
            return
        info = profile.to_text()
        portrait = (profile.portrait or "").strip()
        if portrait:
            if len(portrait) > 1200:
                portrait = portrait[:1200] + "…"
            if info:
                info = info + "\n\n用户画像：\n" + portrait
            else:
                info = "用户画像：\n" + portrait
        if info:
            req.system_prompt += "\n\n### 当前对话用户的背景信息\n" + info + "\n\n"

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def collect_group_messages(self, event: AstrMessageEvent):
        """全平台群消息实时采集（微信主缓存来源）。"""
        self.msg.ingest_event_message(event)

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def get_portrayal(self, event: AstrMessageEvent):
        """画像 @群友 <查询轮数> — 支持 QQ / 微信群。"""
        cmd = (event.message_str or "").partition(" ")[0]
        prompt = self.entry_service.get_entry(cmd)
        if not prompt:
            return
        if prompt.need_admin and not event.is_admin():
            return

        kind = self._platform_kind(event)
        ats = self._extract_at_ids(event)
        if not ats:
            tip = "命令格式：画像 @群友 <查询轮数>"
            if kind == "wechat":
                tip += "\n（微信请使用可识别的 @成员；并依赖平时群聊采集缓存）"
            yield event.plain_result(tip)
            return

        target_id = ats[0]
        if self.cfg.message.is_protected_user(target_id):
            yield event.plain_result("该用户在保护名单中，不允许查询")
            return

        end_param = (event.message_str or "").split(" ")[-1]
        query_rounds = self.cfg.message.get_query_rounds(end_param)

        profile = await self._resolve_profile(event, target_id, kind)

        result = await self.msg.get_user_texts(
            event,
            profile.user_id,
            max_rounds=query_rounds,
        )
        if result.is_empty:
            if kind == "wechat":
                yield event.plain_result(
                    "本地暂无该群友的聊天缓存。请先让机器人在本群正常接收一段时间消息后再试。"
                )
            else:
                yield event.plain_result("没有查询到该群友的任何消息")
            return

        if result.from_cache and result.scanned_messages <= 0:
            yield event.plain_result(
                f"命中缓存，已提取到{result.count}条{profile.nickname or target_id}的聊天记录，"
                f"正在{cmd}..."
            )
        else:
            yield event.plain_result(
                f"已从{result.scanned_messages}条群消息中提取到"
                f"{result.count}条{profile.nickname or target_id}的聊天记录，正在{cmd}..."
            )

        try:
            content = await self.llm.generate_portrait(
                result.texts,
                profile,
                prompt.content,
                umo=event.unified_msg_origin,
                stats=result.stats,
                samples=result.samples,
            )
        except Exception as e:
            logger.error(f"LLM 调用失败：{e}")
            yield event.plain_result(f"分析失败：{e}")
            return

        profile.portrait = content
        profile.timestamp = int(time.time())
        profile.attach_generation_meta(
            command=cmd,
            message_count=result.count,
            query_rounds=query_rounds,
            stats=result.stats,
        )
        self.db.set(profile)
        try:
            yield await self._build_portrait_image_result(
                event,
                profile=profile,
                content=content,
                command=cmd,
                message_count=result.count,
                query_rounds=query_rounds,
                from_cache=result.from_cache,
                activity=result.stats,
            )
        except Exception as e:
            logger.error(f"画像图片渲染失败：{e}")
            yield event.plain_result(f"图片渲染失败：{e}")