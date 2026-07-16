# -*- coding: utf-8 -*-
"""用户头像获取：兼容 QQ CDN + WeChatBridge 协议端真实头像。

参考 muqing-kg/astrbot_plugin_qq_group_daily_analysis 的 OneBot 适配逻辑。
"""
from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
try:
    from astrbot.api import logger
    from astrbot.core.platform.astr_message_event import AstrMessageEvent
except Exception:  # pragma: no cover
    class _L:
        def debug(self, *a, **k): pass
        def warning(self, *a, **k): pass
        def info(self, *a, **k): pass
    logger = _L()
    AstrMessageEvent = object  # type: ignore

# QQ 官方头像模板
_USER_AVATAR_TEMPLATE = "https://q1.qlogo.cn/g?b=qq&nk={user_id}&s={size}"
_USER_AVATAR_HD_TEMPLATE = (
    "https://q.qlogo.cn/headimg_dl?dst_uin={user_id}&spec={size}&img_type=jpg"
)
_USER_AVATAR_Q4 = "https://q4.qlogo.cn/headimg_dl?dst_uin={user_id}&spec={size}"


def is_qq_cdn_avatar_url(url: str) -> bool:
    """QQ 官方 CDN 在微信映射 ID 上通常只会得到默认企鹅头像。"""
    lowered = str(url or "").lower()
    return (
        "q1.qlogo.cn" in lowered
        or "q2.qlogo.cn" in lowered
        or "q4.qlogo.cn" in lowered
        or "q.qlogo.cn/headimg" in lowered
        or "p.qlogo.cn/gh/" in lowered
    )


def extract_avatar_url(payload: Any) -> str | None:
    """从 OneBot 用户/成员资料里提取可用头像 URL。"""
    if not isinstance(payload, dict):
        return None
    for key in (
        "avatar_url",
        "avatar",
        "wx_avatar_url",
        "headimgurl",
        "head_img",
        "headimg",
        "user_avatar",
        "head_url",
        "icon",
    ):
        value = payload.get(key)
        if not isinstance(value, str):
            continue
        text = value.strip()
        if text.startswith(("http://", "https://", "data:")):
            return text
    return None


def pick_display_name(data: dict[str, Any] | None, fallback: str = "") -> str:
    """群名片优先，其次昵称。"""
    data = data or {}
    for key in (
        "card",
        "group_card",
        "group_nickname",
        "display_name",
        "displayName",
        "remark",
        "nickname",
        "nick_name",
        "name",
    ):
        val = str(data.get(key) or "").strip()
        if val:
            return val
    return str(fallback or "").strip()


class AvatarService:
    """带缓存的头像下载服务。"""

    def __init__(self) -> None:
        self._url_cache: dict[str, str] = {}
        self._bytes_cache: dict[str, bytes] = {}

    async def _call_action(self, bot: Any, action: str, **kwargs: Any) -> Any:
        if hasattr(bot, "api") and hasattr(bot.api, "call_action"):
            return await asyncio.wait_for(bot.api.call_action(action, **kwargs), timeout=2.0)
        if hasattr(bot, "call_action"):
            return await asyncio.wait_for(bot.call_action(action, **kwargs), timeout=2.0)
        return None

    async def fetch_avatar_url_from_protocol(
        self, bot: Any, user_id: str, *, prefer_wechat: bool = False
    ) -> str | None:
        """优先从协议端资料接口取真实头像（WeChatBridge 返回 wx.qlogo）。"""
        if bot is None:
            return None
        uid_text = str(user_id).strip()
        if not uid_text:
            return None
        if uid_text.lstrip("-").isdigit():
            try:
                uid_param: int | str = int(uid_text)
            except ValueError:
                uid_param = uid_text
        else:
            uid_param = uid_text

        for action_name in ("get_stranger_info", "get_user_info", "get_group_member_info"):
            try:
                kwargs: dict[str, Any] = {"user_id": uid_param}
                if action_name == "get_group_member_info":
                    # 需要 group_id 时由上层传入；此处仅在 kwargs 已有时调用
                    if "group_id" not in kwargs:
                        # try without - some bridges accept user_id only
                        pass
                result = await self._call_action(bot, action_name, **kwargs)
            except Exception as exc:
                logger.debug("[Avatar] %s 失败 user_id=%s: %s", action_name, user_id, exc)
                continue
            if not isinstance(result, dict):
                continue
            # nested data
            payload = result.get("data") if isinstance(result.get("data"), dict) else result
            url = extract_avatar_url(payload)
            if not url:
                continue
            if prefer_wechat and is_qq_cdn_avatar_url(url):
                # 微信侧若只给到 QQ CDN，大概率是默认企鹅图，继续找
                logger.debug("[Avatar] 忽略 QQ CDN（微信优先） user_id=%s", user_id)
                continue
            return url
        return None

    async def get_user_avatar_url(
        self,
        event: AstrMessageEvent,
        user_id: str,
        *,
        platform: str = "qq",
        size: int = 640,
        group_id: str | None = None,
    ) -> str | None:
        uid = str(user_id or "").strip()
        if not uid:
            return None
        cache_key = f"{platform}:{uid}"
        if cache_key in self._url_cache:
            return self._url_cache[cache_key]

        bot = getattr(event, "bot", None)
        prefer_wechat = platform == "wechat"

        # 1) 协议端
        protocol_url = await self.fetch_avatar_url_from_protocol(
            bot, uid, prefer_wechat=prefer_wechat
        )
        # group member info often has avatar on wechat bridges
        if not protocol_url and bot is not None and group_id:
            try:
                gid: int | str = int(group_id) if str(group_id).lstrip("-").isdigit() else group_id
                uid_param: int | str = int(uid) if uid.lstrip("-").isdigit() else uid
                result = await self._call_action(
                    bot, "get_group_member_info", group_id=gid, user_id=uid_param
                )
                payload = result.get("data") if isinstance(result, dict) and isinstance(result.get("data"), dict) else result
                url = extract_avatar_url(payload if isinstance(payload, dict) else None)
                if url and not (prefer_wechat and is_qq_cdn_avatar_url(url)):
                    protocol_url = url
            except Exception as e:
                logger.debug("[Avatar] get_group_member_info avatar: %s", e)

        if protocol_url:
            self._url_cache[cache_key] = protocol_url
            return protocol_url

        # 2) QQ CDN（仅非微信或明确 QQ 数字号）
        if platform != "wechat" and uid.isdigit():
            url = _USER_AVATAR_HD_TEMPLATE.format(user_id=uid, size=size)
            self._url_cache[cache_key] = url
            return url

        return None

    async def download_bytes(self, url: str) -> bytes | None:
        if not url:
            return None
        if url in self._bytes_cache:
            return self._bytes_cache[url]
        try:
            timeout = aiohttp.ClientTimeout(total=12)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    data = await resp.read()
                    if data and len(data) > 100:
                        self._bytes_cache[url] = data
                        return data
        except Exception as e:
            logger.debug("[Avatar] download failed %s: %s", url[:80], e)
        return None

    async def get_user_avatar_bytes(
        self,
        event: AstrMessageEvent,
        user_id: str,
        *,
        platform: str = "qq",
        group_id: str | None = None,
    ) -> bytes | None:
        url = await self.get_user_avatar_url(
            event, user_id, platform=platform, size=640, group_id=group_id
        )
        if not url:
            # 最后尝试 QQ CDN（即使 wechat 也试一次纯数字，但前面 prefer 已过滤协议 CDN）
            if platform == "qq" and str(user_id).isdigit():
                for tpl in (_USER_AVATAR_Q4, _USER_AVATAR_HD_TEMPLATE, _USER_AVATAR_TEMPLATE):
                    u = tpl.format(user_id=user_id, size=640)
                    data = await self.download_bytes(u)
                    if data:
                        return data
            return None
        data = await self.download_bytes(url)
        if data:
            return data
        # CDN 回退
        if str(user_id).isdigit() and platform != "wechat":
            for tpl in (_USER_AVATAR_Q4, _USER_AVATAR_HD_TEMPLATE):
                u = tpl.format(user_id=user_id, size=640)
                data = await self.download_bytes(u)
                if data:
                    return data
        return None