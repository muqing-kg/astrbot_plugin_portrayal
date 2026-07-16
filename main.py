# -*- coding: utf-8 -*-
"""astrbot_plugin_portrayal - 人物画像插件

Handler 签名遵循 AstrBot 规范：
- command: (self, event) 仅前两个固定参数，其后只能是带默认值的指令参数
- on_llm_request: (self, event, req)
- event_message_type: (self, event)
禁止在 command handler 使用 *args/**kwargs，否则会触发 _empty() takes no arguments。
"""
from __future__ import annotations

import asyncio
import re
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
from .core.avatar import AvatarService, pick_display_name
from .core.text_clean import clean_message_text


class PortrayalPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.cfg = PluginConfig(config, context)
        self.db = UserProfileDB(self.cfg)
        self.msg = MessageManager(self.cfg)
        self.entry_service = EntryService(self.cfg)
        self.llm = LLMService(self.cfg)
        self.avatars = AvatarService()
        self._cleanup_tasks: set[asyncio.Task] = set()
        logger.info("astrbot_plugin_portrayal 已初始化 (v1.3.3)")

    async def initialize(self):
        pass

    async def terminate(self):
        try:
            self.msg.save_cache()
        except Exception:
            pass
        for task in list(self._cleanup_tasks):
            task.cancel()
        self._cleanup_tasks.clear()

    # ---------------------------------------------------------------- utils
    def _platform_kind(self, event: AstrMessageEvent) -> str:
        hints: list[str] = []
        try:
            hints.append(str(event.get_platform_name() or ""))
        except Exception:
            pass
        try:
            hints.append(str(event.get_platform_id() or ""))
        except Exception:
            pass
        try:
            umo = str(event.unified_msg_origin or "")
            hints.append(umo)
        except Exception:
            pass
        joined = " ".join(hints)
        low = joined.lower()
        if "微信" in joined or any(
            k in low for k in ("wechat", "weixin", "gewe", "wxid")
        ):
            return "wechat"
        return normalize_platform(hints[0] if hints else "qq")

    def _extract_at_ids(self, event: AstrMessageEvent) -> list[str]:
        ids: list[str] = []
        try:
            for seg in event.get_messages():
                if isinstance(seg, At):
                    qq = getattr(seg, "qq", None) or getattr(seg, "target", None)
                    if qq is not None and str(qq) not in {"", "0", "all"}:
                        ids.append(str(qq))
                    continue
                seg_type = str(
                    getattr(seg, "type", None)
                    or (seg.get("type") if isinstance(seg, dict) else "")
                    or ""
                ).lower()
                if seg_type not in {"at", "mention"}:
                    continue
                if isinstance(seg, dict):
                    data = seg.get("data") or {}
                    uid = (
                        data.get("qq")
                        or data.get("wxid")
                        or data.get("user_id")
                        or data.get("id")
                    )
                else:
                    data = getattr(seg, "data", None) or {}
                    if not isinstance(data, dict):
                        data = {}
                    uid = (
                        data.get("qq")
                        or data.get("wxid")
                        or getattr(seg, "qq", None)
                        or getattr(seg, "target", None)
                    )
                if uid and str(uid) not in {"", "0", "all"}:
                    ids.append(str(uid))
        except Exception:
            pass
        if not ids:
            text = event.message_str or ""
            for m in re.finditer(r"\[At[：:]\s*(\d+)\]", text, flags=re.I):
                ids.append(m.group(1))
            for m in re.finditer(r"@(\d{5,})", text):
                ids.append(m.group(1))
        out: list[str] = []
        seen: set[str] = set()
        for i in ids:
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out

    async def _fetch_avatar_bytes(
        self, event: AstrMessageEvent, profile: UserProfile
    ) -> bytes | None:
        """群画像头像：优先协议端（微信 wx.qlogo），QQ 回退 CDN。"""
        try:
            group_id = str(event.get_group_id() or "")
        except Exception:
            group_id = ""
        return await self.avatars.get_user_avatar_bytes(
            event,
            str(profile.user_id or ""),
            platform=getattr(profile, "platform", "qq") or "qq",
            group_id=group_id or None,
        )


    def _schedule_card_cleanup(self, path: Path, delay: float = 30.0) -> None:
        async def _cleanup() -> None:
            try:
                await asyncio.sleep(delay)
                if path.exists():
                    path.unlink(missing_ok=True)
            except Exception as e:
                logger.debug(f"清理画像卡片失败：{e}")

        try:
            task = asyncio.create_task(_cleanup())
            self._cleanup_tasks.add(task)
            task.add_done_callback(self._cleanup_tasks.discard)
        except RuntimeError:
            pass

    async def _build_portrait_image_result(
        self,
        event: AstrMessageEvent,
        *,
        profile: UserProfile,
        content: str,
        command: str = "画像",
        message_count: int | None = None,
        query_rounds: int | None = None,
        from_cache: bool = False,
        activity=None,
    ):
        avatar_bytes = await self._fetch_avatar_bytes(event, profile)
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
            c if c.isalnum() or c in "-_" else "_"
            for c in str(profile.user_id or "user")
        )
        out_path = cards_dir / f"portrait_{safe_id}_{int(time.time())}.png"
        await asyncio.to_thread(out_path.write_bytes, png_bytes)
        self._schedule_card_cleanup(out_path, delay=30.0)
        return event.chain_result([Comp.Image.fromFileSystem(str(out_path))])

    async def _resolve_profile(
        self, event: AstrMessageEvent, target_id: str, kind: str
    ) -> UserProfile:
        member_data: dict = {}
        try:
            bot = event.bot
            gid = event.get_group_id()
            if hasattr(bot, "get_group_member_info"):
                try:
                    raw = await bot.get_group_member_info(
                        group_id=int(gid) if str(gid).isdigit() else gid,
                        user_id=int(target_id) if str(target_id).isdigit() else target_id,
                    )
                    if isinstance(raw, dict):
                        member_data = dict(raw.get("data") or raw)
                except Exception:
                    pass
            if not member_data and hasattr(bot, "api") and hasattr(bot.api, "call_action"):
                try:
                    raw = await bot.api.call_action(
                        "get_group_member_info",
                        group_id=gid,
                        user_id=target_id if not str(target_id).isdigit() else int(target_id),
                    )
                    if isinstance(raw, dict):
                        member_data = dict(raw.get("data") or raw)
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"member info: {e}")

        if kind == "wechat":
            data = {"platform": "wechat", **member_data}
            try:
                if str(event.get_sender_id()) == str(target_id):
                    nick = event.get_sender_name() or ""
                    if nick and not data.get("nickname"):
                        data["nickname"] = nick
            except Exception:
                pass
            # stranger/user info may bring avatar_url
            try:
                bot = event.bot
                if hasattr(bot, "api") and hasattr(bot.api, "call_action"):
                    for act in ("get_stranger_info", "get_user_info"):
                        try:
                            raw = await bot.api.call_action(
                                act,
                                user_id=int(target_id) if str(target_id).isdigit() else target_id,
                            )
                            if isinstance(raw, dict):
                                payload = raw.get("data") if isinstance(raw.get("data"), dict) else raw
                                data.update(payload)
                                break
                        except Exception:
                            continue
            except Exception:
                pass
            profile = UserProfile.from_wechat_data(target_id, data=data)
            display = pick_display_name(data, fallback=profile.nickname or target_id)
        else:
            info: dict = dict(member_data)
            try:
                info2 = dict(
                    await event.bot.get_stranger_info(
                        user_id=int(target_id), no_cache=True
                    )
                    or {}
                )
                info.update(info2)
            except Exception:
                pass
            profile = UserProfile.from_qq_data(target_id, data=info)
            display = pick_display_name(info, fallback=profile.nickname or target_id)

        if display:
            profile.nickname = display

        if old := self.db.get(target_id):
            profile.portrait = old.portrait
            profile.timestamp = old.timestamp
            profile.last_command = old.last_command
            profile.last_message_count = old.last_message_count
            profile.last_query_rounds = old.last_query_rounds
            profile.last_stats = dict(old.last_stats or {})
        profile.platform = kind if kind in {"qq", "wechat"} else profile.platform
        return profile

    # ---------------------------------------------------------------- handlers
    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def collect_group_messages(self, event: AstrMessageEvent):
        """实时采集群消息（关系网 / 微信缓存）。"""
        try:
            self.msg.ingest_event_message(event)
        except Exception as e:
            logger.debug(f"collect_group_messages: {e}")

    @filter.command("画像")
    async def get_portrayal(self, event: AstrMessageEvent):
        """
        画像 @群友 <查询轮数>
        """
        # 解析命令：AstrBot command 过滤器已去掉指令名，message 可能只剩参数
        # 兼容完整 message_str 仍含「画像」的情况
        raw = (event.message_str or "").strip()
        # 去掉 wake 前缀与指令名
        text = re.sub(r"^[/／!！#＃.。]+", "", raw).strip()
        if text.startswith("画像"):
            text = text[len("画像") :].strip()

        prompt = self.entry_service.get_entry("画像")
        if not prompt:
            yield event.plain_result("未配置画像提示词")
            return
        if prompt.need_admin and not event.is_admin():
            return

        kind = self._platform_kind(event)
        ats = self._extract_at_ids(event)
        if not ats:
            tip = "命令格式：画像 @群友 <查询轮数>"
            if kind == "wechat":
                tip += "\n（请 @ 具体成员）"
            yield event.plain_result(tip)
            return

        target_id = ats[0]
        if self.cfg.message.is_protected_user(target_id):
            yield event.plain_result("该用户在保护名单中，不允许查询")
            return

        # 轮数：取最后一个 token
        end_param = text.split()[-1] if text.split() else ""
        query_rounds = self.cfg.message.get_query_rounds(end_param)

        profile = await self._resolve_profile(event, target_id, kind)

        yield event.plain_result(
            f"正在查询{profile.nickname or target_id}的聊天记录（最多 {query_rounds} 轮）..."
        )

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

        nick = profile.nickname or target_id
        if result.from_cache and result.scanned_messages <= 0:
            yield event.plain_result(
                f"命中缓存，已提取到 {result.count} 条{nick}的聊天记录，正在生成画像..."
            )
        else:
            yield event.plain_result(
                f"已从 {result.scanned_messages} 条群消息中提取到 "
                f"{result.count} 条{nick}的聊天记录，正在生成画像..."
            )

        try:
            content = await self.llm.generate_portrait(
                result.texts,
                profile,
                prompt.content,
                umo=event.unified_msg_origin,
                stats=result.stats,
                samples=result.samples,
                relations=result.relations,
            )
        except Exception as e:
            logger.error(f"LLM 调用失败：{e}")
            yield event.plain_result(f"分析失败：{e}")
            return

        profile.portrait = content
        profile.timestamp = int(time.time())
        profile.attach_generation_meta(
            command="画像",
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
                command="画像",
                message_count=result.count,
                query_rounds=query_rounds,
                from_cache=result.from_cache,
                activity=result.stats,
            )
        except Exception as e:
            logger.error(f"画像图片渲染失败：{e}")
            yield event.plain_result(f"图片渲染失败：{e}")