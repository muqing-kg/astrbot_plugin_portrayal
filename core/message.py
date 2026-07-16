# -*- coding: utf-8 -*-
"""message.py - platform-aware message manager + relation graph"""
from __future__ import annotations

import asyncio
import re
from collections import Counter
from dataclasses import dataclass, field
from time import time
from typing import Any

from astrbot.api import logger
from astrbot.core.platform.astr_message_event import AstrMessageEvent

from .config import PluginConfig
from .message_cache import CachedMessages, MessageCacheStorage
from .model import normalize_platform
from .stats import ActivityStats, compute_activity_stats
from .text_clean import clean_message_text


@dataclass
class RelationEdge:
    user_id: str
    nickname: str
    weight: int
    kinds: list[str] = field(default_factory=list)  # at / reply

    def label(self) -> str:
        name = self.nickname or self.user_id
        return f"{name}×{self.weight}"


@dataclass
class RelationGraph:
    """目标用户在群内的互动关系摘要。"""

    top_out: list[RelationEdge] = field(default_factory=list)  # 最常 @/回复 谁
    top_in: list[RelationEdge] = field(default_factory=list)  # 最常被谁 @/回复
    summary_lines: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "top_out": [
                {
                    "user_id": e.user_id,
                    "nickname": e.nickname,
                    "weight": e.weight,
                    "kinds": e.kinds,
                }
                for e in self.top_out
            ],
            "top_in": [
                {
                    "user_id": e.user_id,
                    "nickname": e.nickname,
                    "weight": e.weight,
                    "kinds": e.kinds,
                }
                for e in self.top_in
            ],
            "summary_lines": self.summary_lines,
        }

    def summary_text(self) -> str:
        if self.summary_lines:
            return "\n".join(self.summary_lines)
        return "暂无足够互动关系数据（需要消息中含 @ 或引用回复）"


@dataclass
class MessageQueryResult:
    texts: list[str]
    scanned_messages: int
    from_cache: bool
    samples: list[dict[str, Any]] = field(default_factory=list)
    stats: ActivityStats | None = None
    relations: RelationGraph | None = None

    @property
    def count(self) -> int:
        return len(self.texts)

    @property
    def is_empty(self) -> bool:
        return not self.texts


def _extract_mentions_from_text(text: str) -> list[str]:
    ids: list[str] = []
    for m in re.finditer(r"\[At[：:]\s*([^\]]+)\]", text or "", flags=re.I):
        ids.append(m.group(1).strip())
    for m in re.finditer(r"@(\d{5,})", text or ""):
        ids.append(m.group(1))
    return [x for x in ids if x and x not in {"0", "all"}]


def _extract_mentions_from_segments(segments: Any) -> list[str]:
    ids: list[str] = []
    if not isinstance(segments, list):
        return ids
    for seg in segments:
        if not isinstance(seg, dict):
            continue
        t = str(seg.get("type") or "").lower()
        if t not in {"at", "mention"}:
            continue
        data = seg.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        uid = (
            data.get("qq")
            or data.get("wxid")
            or data.get("user_id")
            or data.get("id")
            or seg.get("qq")
        )
        if uid and str(uid) not in {"", "0", "all"}:
            ids.append(str(uid))
    return ids


def _extract_reply_to(msg: dict[str, Any]) -> str:
    # onebot reply
    for key in ("reply", "reply_to", "source"):
        val = msg.get(key)
        if isinstance(val, dict):
            uid = (
                val.get("user_id")
                or val.get("sender_id")
                or (val.get("sender") or {}).get("user_id")
            )
            if uid:
                return str(uid)
        if isinstance(val, (str, int)) and str(val).strip():
            # sometimes only message id; skip pure message id
            s = str(val)
            if s.isdigit() and len(s) > 12:
                continue
    # segments reply
    body = msg.get("message")
    if isinstance(body, list):
        for seg in body:
            if not isinstance(seg, dict):
                continue
            if str(seg.get("type") or "").lower() != "reply":
                continue
            data = seg.get("data") or {}
            if not isinstance(data, dict):
                continue
            uid = data.get("user_id") or data.get("qq") or data.get("wxid")
            if uid:
                return str(uid)
    # text quote pattern: [引用消息(...)] or 引用
    text = ""
    if isinstance(body, str):
        text = body
    m = re.search(r"引用消息\(([^:：\)]+)", text)
    if m:
        return m.group(1).strip()
    return ""


class MessageManager:
    """按群缓存用户消息；QQ 可回溯历史，微信主要靠实时采集。"""

    def __init__(self, config: PluginConfig):
        self.cfg = config.message
        self._storage = MessageCacheStorage(config.cache_dir)
        loaded = self._storage.load()
        if len(loaded) == 3:
            self._user_cache, self._group_cursor, self._nicknames = loaded
        else:
            self._user_cache, self._group_cursor = loaded  # type: ignore
            self._nicknames = {}
        self._group_locks: dict[str, asyncio.Lock] = {}

    def _user_key(self, group_id: str, user_id: str) -> str:
        return f"{group_id}:{user_id}"

    def _remember_nickname(self, user_id: str, nickname: str) -> None:
        uid = str(user_id or "").strip()
        nick = str(nickname or "").strip()
        if uid and nick and nick != uid:
            self._nicknames[uid] = nick

    def _display_name(self, user_id: str) -> str:
        uid = str(user_id or "")
        return self._nicknames.get(uid) or uid

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
        self._nicknames.clear()
        self._storage.clear()

    def save_cache(self) -> None:
        self._storage.save(self._user_cache, self._group_cursor, self._nicknames)

    def _event_mentions(self, event: AstrMessageEvent) -> list[str]:
        ids: list[str] = []
        try:
            for seg in event.get_messages():
                # At component
                qq = getattr(seg, "qq", None) or getattr(seg, "target", None)
                seg_type = str(getattr(seg, "type", "") or "").lower()
                if qq is not None and (seg_type in {"at", "mention", ""}):
                    # only if looks like at; avoid false positive from random components
                    if hasattr(seg, "qq") or hasattr(seg, "target") or seg_type in {
                        "at",
                        "mention",
                    }:
                        if str(qq) not in {"", "0", "all"} and (
                            seg_type in {"at", "mention"} or type(seg).__name__ in {"At", "AtAll"}
                        ):
                            if type(seg).__name__ != "AtAll":
                                ids.append(str(qq))
                if isinstance(seg, dict) and str(seg.get("type") or "").lower() in {
                    "at",
                    "mention",
                }:
                    data = seg.get("data") or {}
                    uid = (
                        data.get("qq")
                        or data.get("wxid")
                        or data.get("user_id")
                        or data.get("id")
                    )
                    if uid and str(uid) not in {"", "0", "all"}:
                        ids.append(str(uid))
        except Exception:
            pass
        ids.extend(_extract_mentions_from_text(event.message_str or ""))
        # de-dup
        out: list[str] = []
        seen: set[str] = set()
        for i in ids:
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out

    def ingest_event_message(self, event: AstrMessageEvent) -> None:
        """实时采集群消息（微信主路径，QQ 也可补充缓存）。"""
        try:
            if not event.get_group_id():
                return
            group_id = str(event.get_group_id())
            user_id = str(event.get_sender_id() or "")
            if not user_id:
                return
            text = clean_message_text(event.message_str or "")
            if not text:
                return
            # 跳过命令触发本身
            if re.match(r"^[/／!！#＃.。]*画像\b", text):
                return
            try:
                self._remember_nickname(user_id, event.get_sender_name() or "")
            except Exception:
                pass

            mentions = self._event_mentions(event)
            reply_to = ""
            # reply chain if available
            try:
                for seg in event.get_messages():
                    st = str(getattr(seg, "type", "") or "").lower()
                    if st == "reply" or type(seg).__name__.lower() == "reply":
                        data = getattr(seg, "data", None) or {}
                        if isinstance(data, dict):
                            reply_to = str(
                                data.get("user_id")
                                or data.get("qq")
                                or data.get("wxid")
                                or ""
                            )
            except Exception:
                pass
            if not reply_to:
                m = re.search(r"引用消息\(([^:：\)]+)", text)
                if m:
                    # may be nickname; keep as display key
                    reply_to = m.group(1).strip()

            msg_ts = int(time())
            try:
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
                    samples=[
                        {
                            "text": text,
                            "timestamp": msg_ts,
                            "mentions": mentions,
                            "reply_to": reply_to,
                        }
                    ],
                )
            else:
                cached.append_message(
                    text, msg_ts, mentions=mentions, reply_to=reply_to
                )
                cached.timestamp = now
            if len(self._user_cache[key].texts) > self.cfg.max_msg_count * 2:
                self._user_cache[key].texts = self._user_cache[key].texts[
                    -self.cfg.max_msg_count :
                ]
                self._user_cache[key].samples = self._user_cache[key].ensure_samples()[
                    -self.cfg.max_msg_count :
                ]
        except Exception as e:
            logger.debug(f"ingest message failed: {e}")

    def _collect_messages(self, group_id: str, messages: list[dict[str, Any]]):
        now = time()
        for msg in messages:
            sender = msg.get("sender") or {}
            user_id = str(
                sender.get("user_id")
                or sender.get("wxid")
                or sender.get("id")
                or ""
            )
            if not user_id:
                continue
            nick = str(
                sender.get("card")
                or sender.get("nickname")
                or sender.get("name")
                or ""
            )
            self._remember_nickname(user_id, nick)

            text = ""
            body = msg.get("message") or msg.get("content") or []
            mentions: list[str] = []
            if isinstance(body, str):
                text = body.strip()
                mentions = _extract_mentions_from_text(text)
            elif isinstance(body, list):
                parts = []
                for seg in body:
                    if not isinstance(seg, dict):
                        continue
                    if seg.get("type") == "text":
                        parts.append(str((seg.get("data") or {}).get("text") or ""))
                text = "".join(parts).strip()
                mentions = _extract_mentions_from_segments(body)
                mentions.extend(_extract_mentions_from_text(text))
            text = clean_message_text(text)
            if not text:
                continue
            # unique mentions
            mentions = list(dict.fromkeys([m for m in mentions if m and m != user_id]))
            reply_to = _extract_reply_to(msg)
            if reply_to == user_id:
                reply_to = ""

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
                    samples=[
                        {
                            "text": text,
                            "timestamp": msg_ts_i,
                            "mentions": mentions,
                            "reply_to": reply_to,
                        }
                    ],
                )
            else:
                cached.append_message(
                    text, msg_ts_i, mentions=mentions, reply_to=reply_to
                )
                cached.timestamp = now

    def build_relations(
        self, group_id: str, target_id: str, *, top_n: int = 5
    ) -> RelationGraph:
        """从本群缓存构建目标用户的互动关系网。"""
        group_id = str(group_id)
        target_id = str(target_id)
        out_counter: Counter[str] = Counter()
        in_counter: Counter[str] = Counter()
        out_kinds: dict[str, set[str]] = {}
        in_kinds: dict[str, set[str]] = {}

        prefix = f"{group_id}:"
        for key, cached in self._user_cache.items():
            if not key.startswith(prefix):
                continue
            sender = key[len(prefix) :]
            samples = cached.ensure_samples()
            for item in samples:
                mentions = item.get("mentions") or []
                if not isinstance(mentions, list):
                    mentions = []
                reply_to = str(item.get("reply_to") or "").strip()
                # 出边：target 主动
                if sender == target_id:
                    for m in mentions:
                        m = str(m)
                        if not m or m == target_id:
                            continue
                        out_counter[m] += 1
                        out_kinds.setdefault(m, set()).add("at")
                    if reply_to and reply_to != target_id:
                        # reply_to 可能是昵称
                        out_counter[reply_to] += 1
                        out_kinds.setdefault(reply_to, set()).add("reply")
                # 入边：别人指向 target
                else:
                    if target_id in [str(x) for x in mentions]:
                        in_counter[sender] += 1
                        in_kinds.setdefault(sender, set()).add("at")
                    if reply_to == target_id:
                        in_counter[sender] += 1
                        in_kinds.setdefault(sender, set()).add("reply")

        def pack(counter: Counter[str], kinds_map: dict[str, set[str]]) -> list[RelationEdge]:
            edges: list[RelationEdge] = []
            for uid, w in counter.most_common(top_n):
                edges.append(
                    RelationEdge(
                        user_id=uid,
                        nickname=self._display_name(uid),
                        weight=int(w),
                        kinds=sorted(kinds_map.get(uid, set())),
                    )
                )
            return edges

        top_out = pack(out_counter, out_kinds)
        top_in = pack(in_counter, in_kinds)
        lines: list[str] = []
        if top_out:
            lines.append(
                "最常互动对象（TA → 他人）："
                + "、".join(e.label() for e in top_out[:5])
            )
        if top_in:
            lines.append(
                "最常被谁点名（他人 → TA）："
                + "、".join(e.label() for e in top_in[:5])
            )
        if not lines:
            lines.append("暂无足够 @/引用 互动数据，关系网从略")
        return RelationGraph(top_out=top_out, top_in=top_in, summary_lines=lines)

    def _result_from_cache(
        self,
        cached: CachedMessages,
        *,
        scanned_messages: int,
        from_cache: bool,
        group_id: str = "",
        target_id: str = "",
    ) -> MessageQueryResult:
        samples = cached.ensure_samples()[: self.cfg.max_msg_count]
        # 读出时清洗旧缓存中的占位符
        cleaned_samples = []
        for item in samples:
            t = clean_message_text(str(item.get("text") or ""))
            if not t:
                continue
            ni = dict(item)
            ni["text"] = t
            cleaned_samples.append(ni)
        samples = cleaned_samples
        texts = [str(item.get("text") or "") for item in samples if item.get("text")]
        if not texts:
            texts = cached.texts[: self.cfg.max_msg_count]
            samples = [
                {"text": t, "timestamp": 0, "mentions": [], "reply_to": ""}
                for t in texts
            ]
        stats = compute_activity_stats(samples)
        relations = None
        if group_id and target_id:
            relations = self.build_relations(group_id, target_id)
        return MessageQueryResult(
            texts=texts,
            scanned_messages=scanned_messages,
            from_cache=from_cache,
            samples=samples,
            stats=stats,
            relations=relations,
        )

    def _platform_kind(self, event: AstrMessageEvent) -> str:
        hints: list[str] = []
        try:
            hints.append(str(event.get_platform_name() or ""))
        except Exception:
            pass
        try:
            hints.append(str(getattr(event, "get_platform_id", lambda: "")() or ""))
        except Exception:
            pass
        try:
            umo = str(getattr(event, "unified_msg_origin", "") or "")
            hints.append(umo)
        except Exception:
            pass
        joined = " ".join(hints).lower()
        if any(k in joined for k in ("微信", "wechat", "weixin", "gewe", "wxid")):
            return "wechat"
        return normalize_platform(hints[0] if hints else "qq")

    async def _fetch_history_qq(
        self, event: AstrMessageEvent, group_id: str, message_seq: int
    ) -> list[dict[str, Any]]:
        bot = event.bot
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

        cached = self._get_user_cache(group_id, target_id)
        if cached and len(cached.texts) >= self.cfg.max_msg_count:
            return self._result_from_cache(
                cached,
                scanned_messages=0,
                from_cache=True,
                group_id=group_id,
                target_id=target_id,
            )

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
                group_id=group_id,
                target_id=target_id,
            )

        stats = compute_activity_stats(texts)
        relations = self.build_relations(group_id, target_id)
        return MessageQueryResult(
            texts=texts[: self.cfg.max_msg_count],
            scanned_messages=rounds * self.cfg.per_query_count,
            from_cache=False,
            samples=[
                {"text": t, "timestamp": 0, "mentions": [], "reply_to": ""}
                for t in texts[: self.cfg.max_msg_count]
            ],
            stats=stats,
            relations=relations,
        )