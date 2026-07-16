# -*- coding: utf-8 -*-
"""message.py - platform-aware message manager"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from time import time
from typing import Any

from astrbot.api import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .config import PluginConfig
from .message_cache import CachedMessages, MessageCacheStorage
from .model import normalize_platform
from .stats import ActivityStats, compute_activity_stats


@dataclass
class MessageQueryResult:
    texts: list[str]
    scanned_messages: int
    from_cache: bool
    samples: list[dict[str, Any]] = field(default_factory=list)
    stats: ActivityStats | None = None

    @property
    def count(self) -> int:
        return len(self.texts)

    @property
    def is_empty(self) -> bool:
        return not self.texts


class MessageManager:
    """按群缓存用户消息；QQ 可回溯历史，微信主要靠实时采集。"""

    def __init__(self, config: PluginConfig):
        self.cfg = config.message
        self._storage = MessageCacheStorage(config.cache_dir)
        self._user_cache, self._group_cursor = self._storage.load()
        self._group_locks: dict[str, asyncio.Lock] = {}

    def _user_key(self, group_id: str, user_id: str) -> str:
        return f"{group_id}:{user_id}"

    def _get_user_cache(self, group_id: str, user_id: str) -> CachedMessages | None:
        key = self._user_key(group_id, user_id)
        cached = self._user_cache.get(key)
        if not cached:
            return None
        if time() - cached.timestamp > self.cfg.cache_ttl:
            self._group_cursor.pop(group_id, None)
            for group_user_key in tuple(self._user_cache):
                if group_user_key.startswith(f"{group_id}:"):
                    del self._user_cache[group_user_key]
            self.save_cache()
            return None
        return cached

    def clear_cache(self):
        self._user_cache.clear()
        self._group_cursor.clear()
        self._storage.clear()

    def save_cache(self) -> None:
        self._storage.save(self._user_cache, self._group_cursor)

    def ingest_event_message(self, event: AstrMessageEvent) -> None:
        """实时采集群消息（微信主路径，QQ 也可补充缓存）。"""
        try:
            if not event.get_group_id():
                return
            group_id = str(event.get_group_id())
            user_id = str(event.get_sender_id() or "")
            if not user_id:
                return
            text = (event.message_str or "").strip()
            if not text:
                return
            # 跳过命令触发本身
            if text.startswith("画像"):
                return
            msg_ts = int(time())
            try:
                # 部分平台 message 有 time
                raw = getattr(event, "message_obj", None)
                if raw is not None and getattr(raw, "time", None):
                    msg_ts = int(raw.time)
            except Exception:
                pass
            key = self._user_key(group_id, user_id)
            now = time()
            cached = self._user_cache.get(key)
            if not cached:
                self._user_cache[key] = CachedMessages(
                    texts=[text],
                    timestamp=now,
                    samples=[{"text": text, "timestamp": msg_ts}],
                )
            else:
                cached.append_message(text, msg_ts)
                cached.timestamp = now
            # 控制单用户缓存膨胀
            if len(self._user_cache[key].texts) > self.cfg.max_msg_count * 2:
                self._user_cache[key].texts = self._user_cache[key].texts[-self.cfg.max_msg_count :]
                self._user_cache[key].samples = self._user_cache[key].ensure_samples()[
                    -self.cfg.max_msg_count :
                ]
        except Exception as e:
            logger.debug(f"ingest message failed: {e}")

    def _collect_messages(self, group_id: str, messages: list[dict[str, Any]]):
        now = time()
        for msg in messages:
            sender = msg.get("sender") or {}
            user_id = str(sender.get("user_id") or sender.get("wxid") or sender.get("id") or "")
            if not user_id:
                continue
            text = ""
            body = msg.get("message") or msg.get("content") or []
            if isinstance(body, str):
                text = body.strip()
            elif isinstance(body, list):
                parts = []
                for seg in body:
                    if not isinstance(seg, dict):
                        continue
                    if seg.get("type") == "text":
                        parts.append(str((seg.get("data") or {}).get("text") or ""))
                text = "".join(parts).strip()
            if not text:
                continue
            msg_ts = msg.get("time") or msg.get("message_time") or 0
            try:
                msg_ts_i = int(msg_ts)
            except (TypeError, ValueError):
                msg_ts_i = 0
            key = self._user_key(group_id, user_id)
            cached = self._user_cache.get(key)
            if not cached:
                self._user_cache[key] = CachedMessages(
                    texts=[text],
                    timestamp=now,
                    samples=[{"text": text, "timestamp": msg_ts_i}],
                )
            else:
                cached.append_message(text, msg_ts_i)
                cached.timestamp = now

    def _result_from_cache(
        self,
        cached: CachedMessages,
        *,
        scanned_messages: int,
        from_cache: bool,
    ) -> MessageQueryResult:
        samples = cached.ensure_samples()[: self.cfg.max_msg_count]
        texts = [str(item.get("text") or "") for item in samples if item.get("text")]
        if not texts:
            texts = cached.texts[: self.cfg.max_msg_count]
            samples = [{"text": t, "timestamp": 0} for t in texts]
        stats = compute_activity_stats(samples)
        return MessageQueryResult(
            texts=texts,
            scanned_messages=scanned_messages,
            from_cache=from_cache,
            samples=samples,
            stats=stats,
        )

    def _platform_kind(self, event: AstrMessageEvent) -> str:
        plat = ""
        try:
            plat = event.get_platform_name() or ""
        except Exception:
            plat = ""
        if not plat:
            plat = str(getattr(event, "get_platform_id", lambda: "")() or "")
        return normalize_platform(plat)

    async def _fetch_history_qq(
        self, event: AstrMessageEvent, group_id: str, message_seq: int
    ) -> list[dict[str, Any]]:
        bot = event.bot
        # aiocqhttp / onebot
        if hasattr(bot, "api") and hasattr(bot.api, "call_action"):
            result: dict[str, Any] = await bot.api.call_action(
                "get_group_msg_history",
                group_id=group_id,
                message_seq=message_seq,
                count=self.cfg.per_query_count,
                reverseOrder=True,
            )
            return list(result.get("messages") or [])
        if hasattr(bot, "call_action"):
            result = await bot.call_action(
                "get_group_msg_history",
                group_id=group_id,
                message_seq=message_seq,
                count=self.cfg.per_query_count,
                reverseOrder=True,
            )
            return list((result or {}).get("messages") or [])
        return []

    async def get_user_texts(
        self,
        event: AstrMessageEvent,
        target_id: str,
        *,
        max_rounds: int,
    ) -> MessageQueryResult:
        group_id = str(event.get_group_id())
        target_id = str(target_id)
        kind = self._platform_kind(event)

        cached = self._get_user_cache(group_id, target_id)
        if cached and len(cached.texts) >= self.cfg.max_msg_count:
            return self._result_from_cache(
                cached,
                scanned_messages=0,
                from_cache=True,
            )

        # 微信原生协议往往无历史；若经 aiocqhttp 桥接则下面会尝试拉历史，失败再回退缓存
        texts = cached.texts[:] if cached else []
        rounds = 0
        cache_changed = False
        group_lock = self._group_locks.setdefault(group_id, asyncio.Lock())

        while rounds < max_rounds and len(texts) < self.cfg.max_msg_count:
            try:
                async with group_lock:
                    cached = self._get_user_cache(group_id, target_id)
                    if cached and len(cached.texts) >= self.cfg.max_msg_count:
                        texts = cached.texts[:]
                        break
                    message_seq = self._group_cursor.get(group_id, 0)
                    messages = await self._fetch_history_qq(event, group_id, message_seq)
                    if messages:
                        mid = (
                            messages[0].get("message_id")
                            or messages[0].get("id")
                            or message_seq
                        )
                        try:
                            self._group_cursor[group_id] = int(mid)
                        except (TypeError, ValueError):
                            self._group_cursor[group_id] = message_seq
                        self._collect_messages(group_id, messages)
                        cache_changed = True
                    else:
                        break

                cached = self._get_user_cache(group_id, target_id)
                if cached:
                    texts = cached.texts[:]
            except Exception as e:
                logger.error(e)
                break
            rounds += 1

        if cache_changed:
            self.save_cache()

        cached = self._get_user_cache(group_id, target_id)
        if cached:
            return self._result_from_cache(
                cached,
                scanned_messages=rounds * self.cfg.per_query_count,
                from_cache=(rounds == 0),
            )

        stats = compute_activity_stats(texts)
        return MessageQueryResult(
            texts=texts[: self.cfg.max_msg_count],
            scanned_messages=rounds * self.cfg.per_query_count,
            from_cache=False,
            samples=[{"text": t, "timestamp": 0} for t in texts[: self.cfg.max_msg_count]],
            stats=stats,
        )