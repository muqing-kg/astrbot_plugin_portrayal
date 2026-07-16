# -*- coding: utf-8 -*-
"""清洗聊天占位文本，避免 [图片]/[引用] 污染统计与画像。"""
from __future__ import annotations

import re
from typing import Any

# 常见 OneBot / 客户端占位
_PLACEHOLDER_PATTERNS = [
    re.compile(r"\[图片\]", re.I),
    re.compile(r"\[image\]", re.I),
    re.compile(r"\[photo\]", re.I),
    re.compile(r"\[表情\]"),
    re.compile(r"\[动画表情\]"),
    re.compile(r"\[face\]", re.I),
    re.compile(r"\[视频\]"),
    re.compile(r"\[语音\]"),
    re.compile(r"\[文件\]"),
    re.compile(r"\[分享\]"),
    re.compile(r"\[小程序\]"),
    re.compile(r"\[位置\]"),
    re.compile(r"\[红包\]"),
    re.compile(r"\[卡片消息\]"),
    re.compile(r"\[CQ:[^\]]+\]", re.I),
    # 引用占位
    re.compile(r"\[引用\][^\s\[\]，。！？]*"),
    re.compile(r"\[引用消息[^\]]*\]"),
    re.compile(r"引用消息\([^)]*\)"),
    re.compile(r"\[Reply[^\]]*\]", re.I),
]

_TOKEN_BAN = {
    "图片",
    "表情",
    "动画表情",
    "视频",
    "语音",
    "文件",
    "分享",
    "引用",
    "image",
    "photo",
    "face",
    "reply",
}


def clean_message_text(text: str) -> str:
    """去掉占位符，保留真实文字。"""
    s = str(text or "")
    for pat in _PLACEHOLDER_PATTERNS:
        s = pat.sub(" ", s)
    # 残留方括号短标签
    s = re.sub(r"\[[^\[\]]{1,12}\]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def is_noise_token(token: str) -> bool:
    t = (token or "").strip().lower()
    if not t:
        return True
    if t in _TOKEN_BAN:
        return True
    if "[" in t or "]" in t:
        return True
    if t.startswith("引用"):
        return True
    return False


def clean_samples(samples: list[Any]) -> list[dict[str, Any]]:
    """清洗 sample 列表中的 text，丢弃空文本样本（保留有 mentions 的可另议）。"""
    out: list[dict[str, Any]] = []
    for item in samples or []:
        if isinstance(item, str):
            text = clean_message_text(item)
            if text:
                out.append({"text": text, "timestamp": 0, "mentions": [], "reply_to": ""})
            continue
        if not isinstance(item, dict):
            continue
        text = clean_message_text(str(item.get("text") or ""))
        if not text:
            continue
        new_item = dict(item)
        new_item["text"] = text
        out.append(new_item)
    return out


def clean_texts(texts: list[str]) -> list[str]:
    out: list[str] = []
    for t in texts or []:
        c = clean_message_text(t)
        if c:
            out.append(c)
    return out