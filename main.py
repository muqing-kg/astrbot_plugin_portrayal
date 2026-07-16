# -*- coding: utf-8 -*-
import asyncio
import re
import time
from pathlib import Path
from typing import Any

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

# AstrBot 部分版本调用 handler 时 self 可能为 None，用模块级实例兜底
_PLUGIN: "PortrayalPlugin | None" = None


def _plugin() -> "PortrayalPlugin":
    if _PLUGIN is None:
        raise RuntimeError("PortrayalPlugin 尚未初始化")
    return _PLUGIN


def _as_event(*args, **kwargs) -> AstrMessageEvent | None:
    for a in args:
        if a is not None and hasattr(a, "message_str") and hasattr(a, "get_sender_id"):
            return a  # type: ignore[return-value]
    for k in ("event", "evt", "message_event"):
        a = kwargs.get(k)
        if a is not None and hasattr(a, "message_str"):
            return a  # type: ignore[return-value]
    return None


class PortrayalPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        global _PLUGIN
        self.context = context
        self.cfg = PluginConfig(config, context)
        self.db = UserProfileDB(self.cfg)
        self.msg = MessageManager(self.cfg)
        self.entry_service = EntryService(self.cfg)
        self.llm = LLMService(self.cfg)
        self._cleanup_tasks: set[asyncio.Task] = set()
        _PLUGIN = self
        logger.info("astrbot_plugin_portrayal 已初始化 (v1.2.0)")

    async def initialize(self):
        global _PLUGIN
        _PLUGIN = self

    async def terminate(self):
        global _PLUGIN
        try:
            self.msg.save_cache()
        except Exception:
            pass
        for task in list(self._cleanup_tasks):
            task.cancel()
        self._cleanup_tasks.clear()
        if _PLUGIN is self:
            _PLUGIN = None

    # ------------------------------------------------------------------ utils
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
            if ":" in umo:
                hints.append(umo.split(":", 1)[0])
        except Exception:
            pass
        try:
            hints.append(str(getattr(event, "session_id", "") or ""))
        except Exception:
            pass
        joined = " ".join(hints)
        joined_l = joined.lower()
        if any(k in joined for k in ("微信",)) or any(
            k in joined_l for k in ("wechat", "weixin", "gewe", "wxid")
        ):
            return "wechat"
        return normalize_platform(hints[0] if hints else "qq")

    def _parse_command(self, message_str: str) -> tuple[str, str]:
        text = (message_str or "").strip()
        if not text:
            return "", ""
        text = re.sub(r"^[/／!！#＃.。]+", "", text).strip()
        first, _, rest = text.partition(" ")
        return first.strip(), rest.strip()

    def _extract_at_ids(self, event: AstrMessageEvent) -> list[str]:
        ids: list[str] = []
        try:
            for seg in event.get_messages():
                if isinstance(seg, At):
                    qq = getattr(seg, "qq", None) or getattr(seg, "target", None)
                    if qq is not None and str(qq) not in {"", "0", "all"}:
                        ids.append(str(qq))
                    continue
                seg_type = getattr(seg, "type", None) or (
                    seg.get("type") if isinstance(seg, dict) else None
                )
                if str(seg_type).lower() in {"at", "mention"}:
                    if isinstance(seg, dict):
                        data = seg.get("data") or {}
                        uid = (
                            data.get("qq")
                            or data.get("wxid")
                            or data.get("user_id")
                            or data.get("id")
                            or seg.get("qq")
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
        seen: set[str] = set()
        out: list[str] = []
        for i in ids:
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out

    async def _fetch_avatar_bytes(self, profile: UserProfile) -> bytes | None:
        user_id = str(profile.user_id or "")
        if not user_id or profile.platform == "wechat" or not user_id.isdigit():
            return None
        timeout = aiohttp.ClientTimeout(total=15)
        url = f"https://q4.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640"
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    return await resp.read()
        except Exception as e:
            logger.warning(f"下载用户头像失败：{e}")
            return None

    def _schedule_card_cleanup(self, path: Path, delay: float = 30.0) -> None:
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
        self._schedule_card_cleanup(out_path, delay=30.0)
        return event.chain_result([Comp.Image.fromFileSystem(str(out_path))])

    async def _resolve_profile(
        self, event: AstrMessageEvent, target_id: str, kind: str
    ) -> UserProfile:
        if kind == "wechat":
            data: dict[str, Any] = {"platform": "wechat"}
            try:
                if str(event.get_sender_id()) == str(target_id):
                    nick = event.get_sender_name() or ""
                    if nick:
                        data["nickname"] = nick
            except Exception:
                pass
            bot = event.bot
            try:
                if hasattr(bot, "get_group_member_info"):
                    info = await bot.get_group_member_info(
                        group_id=event.get_group_id(), user_id=target_id
                    )
                    if isinstance(info, dict):
                        data.update(info)
                elif hasattr(bot, "api") and hasattr(bot.api, "call_action"):
                    try:
                        info = await bot.api.call_action(
                            "get_group_member_info",
                            group_id=event.get_group_id(),
                            user_id=target_id,
                        )
                        if isinstance(info, dict):
                            data.update(info)
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"wechat member info fallback: {e}")
            profile = UserProfile.from_wechat_data(target_id, data=data)
        else:
            info: dict[str, Any] = {}
            try:
                info = dict(
                    await event.bot.get_stranger_info(
                        user_id=int(target_id), no_cache=True
                    )
                    or {}
                )
            except Exception:
                try:
                    info = dict(
                        await event.bot.get_group_member_info(
                            group_id=int(event.get_group_id()),
                            user_id=int(target_id),
                        )
                        or {}
                    )
                except Exception as e:
                    logger.warning(f"获取用户资料失败：{e}")
            profile = UserProfile.from_qq_data(target_id, data=info)

        if old_profile := self.db.get(target_id):
            profile.portrait = old_profile.portrait
            profile.timestamp = old_profile.timestamp
            profile.last_command = old_profile.last_command
            profile.last_message_count = old_profile.last_message_count
            profile.last_query_rounds = old_profile.last_query_rounds
            profile.last_stats = dict(old_profile.last_stats or {})
        profile.platform = kind if kind in {"qq", "wechat"} else profile.platform
        return profile

    async def _run_portrayal(self, event: AstrMessageEvent):
        """画像主流程（async generator）。"""
        cmd, rest = self._parse_command(event.message_str or "")
        # 兼容 filter.command 触发时 message 可能只有参数
        if not self.entry_service.get_entry(cmd):
            # 可能是 /画像 被 command 过滤器截断，强制视为 画像
            if "画像" in (event.message_str or "") or cmd == "":
                cmd = "画像"
            else:
                # 也接受纯参数触发（@xx 30）
                cmd = "画像"
        prompt = self.entry_service.get_entry(cmd) or self.entry_service.get_entry("画像")
        if not prompt:
            yield event.plain_result("未配置画像提示词")
            return
        if prompt.need_admin and not event.is_admin():
            return

        try:
            event.stop_event()
        except Exception:
            pass

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

        end_param = ""
        if rest:
            end_param = rest.split()[-1]
        else:
            parts = (event.message_str or "").split()
            if parts:
                end_param = parts[-1]
        query_rounds = self.cfg.message.get_query_rounds(end_param)

        profile = await self._resolve_profile(event, target_id, kind)
        result = await self.msg.get_user_texts(
            event, profile.user_id, max_rounds=query_rounds
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
                f"命中缓存，已提取到{result.count}条{nick}的聊天记录，正在画像..."
            )
        else:
            yield event.plain_result(
                f"已从{result.scanned_messages}条群消息中提取到"
                f"{result.count}条{nick}的聊天记录，正在画像..."
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

    # ------------------------------------------------------------------ handlers
    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, *args, **kwargs):
        plug = self if isinstance(self, PortrayalPlugin) else _plugin()
        try:
            event = event if hasattr(event, "message_str") else _as_event(event, *args, **kwargs)
            if event is None:
                return
            req = None
            for a in args:
                if a is not None and (
                    isinstance(a, ProviderRequest) or hasattr(a, "system_prompt")
                ):
                    req = a
                    break
            req = req or kwargs.get("req") or kwargs.get("request")
            if req is None or not plug.cfg.inject_prompt:
                return
            if not event.message_str:
                return
            profile = plug.db.get(str(event.get_sender_id()))
            if not profile:
                return
            info = profile.to_text()
            portrait = (profile.portrait or "").strip()
            if portrait:
                if len(portrait) > 1200:
                    portrait = portrait[:1200] + "…"
                info = (info + "\n\n用户画像：\n" + portrait) if info else (
                    "用户画像：\n" + portrait
                )
            if info:
                req.system_prompt = (getattr(req, "system_prompt", None) or "") + (
                    "\n\n### 当前对话用户的背景信息\n" + info + "\n\n"
                )
        except Exception as e:
            logger.debug(f"on_llm_request inject skip: {e}")

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def collect_group_messages(self, event: AstrMessageEvent, *args, **kwargs):
        try:
            plug = self if isinstance(self, PortrayalPlugin) else _plugin()
            event = event if hasattr(event, "message_str") else _as_event(event, *args, **kwargs)
            if event is None:
                return
            plug.msg.ingest_event_message(event)
        except Exception as e:
            logger.debug(f"collect_group_messages: {e}")

    @filter.command("画像")
    async def cmd_portrayal(self, event: AstrMessageEvent, *args, **kwargs):
        """画像 @群友 <查询轮数>"""
        try:
            plug = self if isinstance(self, PortrayalPlugin) else _plugin()
            event = event if hasattr(event, "message_str") else _as_event(event, *args, **kwargs)
            if event is None:
                return
            async for ret in plug._run_portrayal(event):
                yield ret
        except Exception as e:
            logger.error(f"cmd_portrayal error: {e}", exc_info=True)
            try:
                if event is not None:
                    yield event.plain_result(f"画像处理异常：{e}")
            except Exception:
                pass

    @filter.event_message_type(filter.EventMessageType.GROUP_MESSAGE)
    async def get_portrayal(self, event: AstrMessageEvent, *args, **kwargs):
        """兼容 /画像 前缀与未注册到 command 列表的触发。"""
        try:
            plug = self if isinstance(self, PortrayalPlugin) else _plugin()
            # 关键：AstrBot 可能把 (None, event) 或 (event, ctx) 传进来
            if not isinstance(self, PortrayalPlugin):
                # self 可能是 event
                maybe_event = _as_event(self, event, *args, **kwargs)
                if maybe_event is not None:
                    event = maybe_event
            else:
                if not hasattr(event, "message_str"):
                    event = _as_event(event, *args, **kwargs)  # type: ignore
            if event is None:
                return

            cmd, _ = plug._parse_command(event.message_str or "")
            if cmd != "画像":
                # 也匹配“/画像xxx”被空格切开失败的情况
                raw = (event.message_str or "").strip()
                raw2 = re.sub(r"^[/／!！#＃.。]+", "", raw)
                if not raw2.startswith("画像"):
                    return
            async for ret in plug._run_portrayal(event):
                yield ret
        except Exception as e:
            logger.error(f"get_portrayal error: {e}", exc_info=True)