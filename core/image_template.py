# -*- coding: utf-8 -*-
"""画像卡片模板：蜜桃苏打磨砂 · 柔和可爱高级感。"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .model import UserProfile
from .stats import ActivityStats

PLUGIN_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STYLE_PATH = PLUGIN_ROOT / "assets" / "templates" / "rainbow_day.json"

# 优先插件内置字体，再回退系统字体
_BUNDLED_FONT_DIR = PLUGIN_ROOT / "assets" / "fonts"
_FONT_CANDIDATES = [
    # 插件内置开源字体（跨平台）
    _BUNDLED_FONT_DIR / "LXGWWenKaiLite-Regular.ttf",
    # 系统中文字体回退
    Path(r"C:\Windows\Fonts\msyh.ttc"),
    Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    Path(r"C:\Windows\Fonts\simhei.ttf"),
    Path("/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/System/Library/Fonts/PingFang.ttc"),
]

_COMMAND_THEMES: dict[str, dict[str, tuple[int, ...]]] = {
    "画像": {
        "badge": (255, 236, 242, 200),
        "badge_text": (176, 110, 148, 255),
        "accent": (255, 168, 188, 255),
        "ribbon": (255, 210, 222, 120),
        "metric": (255, 248, 250, 175),
        "glow": (255, 186, 206, 70),
    },
}

_SECTION_ICON_KIND = {
    "一句话印象": "quote",
    "一句话总结": "quote",
    "身份档案": "role",
    "群内人设": "role",
    "社交姿态": "social",
    "关系网": "social",
    "互动关系": "social",
    "互动姿态": "social",
    "群内姿态": "social",
    "性格标签": "tag",
    "活跃画像": "pulse",
    "语言风格": "chat",
    "兴趣与话题": "spark",
    "兴趣与价值": "spark",
    "价值贡献": "spark",
    "性格特质": "mind",
    "隐藏闪光点": "star",
    "优势分析": "plus",
    "缺点分析": "minus",
    "摩擦触发点": "alert",
    "核心缺陷": "minus",
    "风险与雷区": "alert",
    "名场面与荣誉": "medal",
    "群荣誉": "medal",
    "群荣誉（黑称）": "medal",
    "相处建议": "hint",
    "相处避坑": "hint",
}

_HONOR_TITLES = {"名场面与荣誉", "群荣誉", "群荣誉（黑称）", "群荣誉(黑称)"}
_PERIOD_LIKE = {
    "清晨打卡人", "上午搬砖党", "午间摸鱼人",
    "下午在线员", "晚间话痨王", "深夜修仙党",
}


def _short_period_label(label: str) -> str:
    mapping = {
        "清晨打卡人": "清晨", "上午搬砖党": "上午", "午间摸鱼人": "午间",
        "下午在线员": "下午", "晚间话痨王": "晚间", "深夜修仙党": "深夜",
    }
    return mapping.get(label, (label or "")[:2] or "时段")


def _theme_for(command: str) -> dict[str, tuple[int, ...]]:
    for key, theme in _COMMAND_THEMES.items():
        if key in (command or ""):
            return theme
    return _COMMAND_THEMES["画像"]

def extract_tags(content: str, *, limit: int = 10) -> list[str]:
    text_in = (content or "").strip()
    if not text_in:
        return []
    tags: list[str] = []

    def _push(raw: str) -> None:
        t = re.sub(r"[`*_~#]", "", (raw or "")).strip()
        t = t.strip("【】[]()（）·•-—_ ")
        if not t or not (1 < len(t) <= 14) or t in tags:
            return
        banned = {
            "性格标签", "优势分析", "缺点分析", "相处建议", "相处避坑", "隐藏闪光点",
            "核心性格缺陷", "核心缺陷", "一句话印象", "群内人设", "社交姿态",
                "关系网",
                "互动关系", "互动姿态",
            "活跃画像", "语言风格", "兴趣与话题", "兴趣与价值", "性格特质", "名场面与荣誉",
            "摩擦触发点", "风险与雷区", "群荣誉", "群荣誉（黑称）",
        }
        if t not in banned:
            tags.append(t)

    for m in re.finditer(r"(?:性格标签|关键词|标签|特质|画像标签)[：:]\s*([^\n]+)", text_in):
        for part in re.split(r"[、,，/|·•]+", m.group(1)):
            _push(part)
            if len(tags) >= limit:
                return tags[:limit]
    for m in re.finditer(
        r"(?:【(?:性格标签|关键词|标签|特质|画像标签)】|(?:性格标签|关键词|标签|特质|画像标签)\s*$)\n\s*([^\n]+)",
        text_in, flags=re.M,
    ):
        for part in re.split(r"[、,，/|·•]+", m.group(1)):
            _push(part)
            if len(tags) >= limit:
                return tags[:limit]
    for m in re.finditer(r"【群内人设】\s*\n\s*([^\n]+)", text_in):
        for part in re.split(r"[、,，/|·•+/]+", m.group(1)):
            _push(part)
            if len(tags) >= limit:
                return tags[:limit]
    for m in re.finditer(r"[#＃]([\u4e00-\u9fffA-Za-z0-9]{2,12})", text_in):
        _push(m.group(1))
        if len(tags) >= limit:
            return tags[:limit]
    if not tags:
        for m in re.finditer(r"(?m)^(?:[-*+•]|\d+[\.、])\s*([^\n]{2,12})\s*$", text_in):
            _push(m.group(1))
            if len(tags) >= limit:
                break
    return tags[:limit]


def extract_quote(content: str) -> str:
    text = (content or "").strip()
    if not text:
        return ""
    m = re.search(r"【一句话印象】\s*\n\s*([^\n【]+)", text)
    if m:
        return m.group(1).strip().strip("“”\"'")
    m = re.search(r"(?:一句话印象|总体印象)[：:]\s*([^\n]+)", text)
    if m:
        return m.group(1).strip().strip("“”\"'")
    return ""


def extract_roles(content: str) -> list[str]:
    text = (content or "").strip()
    m = re.search(r"【群内人设】\s*\n\s*([^\n【]+)", text)
    if not m:
        return []
    roles: list[str] = []
    for part in re.split(r"[、,，/|·•+＋]+", m.group(1)):
        t = part.strip().strip("【】[]")
        if 1 < len(t) <= 16 and t not in roles:
            roles.append(t)
    return roles[:3]


@dataclass(slots=True)
class PortraitCardData:
    title: str
    nickname: str
    user_id: str
    content: str
    subtitle: str = "群友性格画像"
    meta_items: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    stats: list[str] = field(default_factory=list)
    metric_cards: list[tuple[str, str]] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    quote: str = ""
    catchphrases: list[str] = field(default_factory=list)
    period_bars: list[tuple[str, float]] = field(default_factory=list)
    avatar_bytes: bytes | None = None
    footer: str = "模版作者 沐沐沐倾丶"
    generated_at: str = ""
    platform: str = "qq"

    @classmethod
    def from_profile(
        cls, *, profile: UserProfile, content: str, command: str = "画像",
        avatar_bytes: bytes | None = None, platform: str | None = None,
        message_count: int | None = None, query_rounds: int | None = None,
        from_cache: bool = False, activity: ActivityStats | None = None,
    ) -> "PortraitCardData":
        if platform:
            profile.platform = platform
        plat = getattr(profile, "platform", "qq") or "qq"
        meta_items = profile.to_chips(max_items=6)
        tags = extract_tags(content, limit=10)
        roles = extract_roles(content)
        quote = extract_quote(content)
        generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
        if plat == "wechat":
            subtitle = "基于微信群聊记录生成"
        elif plat == "qq":
            subtitle = "基于群聊记录生成"
        else:
            subtitle = "基于聊天记录生成"
        if activity and activity.top_words:
            for word in activity.top_words:
                if len(tags) >= 10:
                    break
                if word not in tags:
                    tags.append(word)
        stats: list[str] = []
        metric_cards: list[tuple[str, str]] = []
        catchphrases: list[str] = []
        period_bars: list[tuple[str, float]] = []
        if activity and activity.message_count > 0:
            metric_cards.append(("样本", f"{activity.message_count} 条"))
            metric_cards.append(("均长", f"{activity.avg_chars:.0f} 字"))
            if activity.active_days > 0:
                metric_cards.append(("天数", f"{activity.active_days} 天"))
            if activity.style_labels:
                style = next((s for s in activity.style_labels if s not in _PERIOD_LIKE), "")
                if style:
                    metric_cards.append(("风格", style))
            stats = activity.card_stats()
            catchphrases = list(activity.catchphrases[:5])
            period_bars = [(_short_period_label(label), ratio) for label, ratio in activity.period_bars()]
        else:
            if message_count is not None and message_count > 0:
                stats.append(f"样本 {message_count} 条")
                metric_cards.append(("样本", f"{message_count} 条"))
            if query_rounds is not None and query_rounds > 0:
                stats.append(f"扫描 {query_rounds} 轮")
            if from_cache:
                stats.append("缓存命中")
        if from_cache and "缓存命中" not in stats:
            stats.append("缓存命中")
        if tags and not any(s.startswith("标签") for s in stats):
            stats.append(f"标签 {len(tags)}")
        return cls(
            title=command, nickname=profile.nickname or profile.user_id or "未知昵称",
            user_id=str(profile.user_id or ""), subtitle=subtitle, content=content.strip(),
            meta_items=meta_items, tags=tags, stats=stats, metric_cards=metric_cards[:4],
            roles=roles, quote=quote, catchphrases=catchphrases, period_bars=period_bars,
            avatar_bytes=avatar_bytes, generated_at=generated_at, platform=plat,
        )

class PortraitImageTemplate:
    """蜜桃苏打磨砂风画像卡片。"""

    def __init__(self, style_path: Path | None = None, plugin_root: Path | None = None):
        self.plugin_root = plugin_root or PLUGIN_ROOT
        self.style_path = style_path or DEFAULT_STYLE_PATH
        self.style = self._load_style(self.style_path)
        self._font_cache: dict[tuple[int, bool], ImageFont.FreeTypeFont | ImageFont.ImageFont] = {}

    def render(self, data: PortraitCardData) -> bytes:
        canvas = self.style["canvas"]
        colors = self.style["colors"]
        fonts = self.style["fonts"]
        theme = _theme_for(data.title)
        width = int(canvas["width"])
        pad_l, pad_t, pad_r, pad_b = [int(v) for v in canvas["padding"]]
        content_width = width - pad_l - pad_r
        gap = int(canvas["content_gap"])

        title_font = self._font(int(fonts["title_size"]), bold=True)
        subtitle_font = self._font(int(fonts["subtitle_size"]))
        section_font = self._font(int(fonts["section_size"]), bold=True)
        body_font = self._font(int(fonts["body_size"]))
        meta_font = self._font(int(fonts["meta_size"]))
        footer_font = self._font(int(fonts["footer_size"]))
        tag_font = self._font(max(17, int(fonts["meta_size"]) - 1))
        quote_font = self._font(int(fonts.get("quote_size", fonts["body_size"] + 2)), bold=True)
        metric_value_font = self._font(int(fonts.get("metric_value_size", 24)), bold=True)
        metric_label_font = self._font(int(fonts.get("metric_label_size", 14)))

        measure = Image.new("RGBA", (width, 10), (0, 0, 0, 0))
        measure_draw = ImageDraw.Draw(measure)
        sections = self._split_sections(data.content)
        display_sections: list[dict[str, Any]] = []
        quote = data.quote
        for section in sections:
            title = section["title"]
            if title in {"一句话印象", "总体印象"}:
                if not quote:
                    quote = " ".join(section["paragraphs"]).strip()
                continue
            if title in {"活跃画像"}:
                continue
            if title in {"性格标签"}:
                joined = " ".join(section["paragraphs"])
                if re.fullmatch(r"[\w\u4e00-\u9fff ·•、,，/|+-]+", joined) and len(joined) < 80:
                    continue
            display_sections.append(section)
        # 固定正文顺序（美观 + 阅读逻辑）
        _SECTION_ORDER = [
            "群内人设",
            "性格标签",
            "身份档案",
            "语言风格",
            "社交姿态",
            "关系网",
            "互动关系",
            "兴趣与话题",
            "兴趣与价值",
            "名场面与荣誉",
            "群荣誉",
            "群荣誉（黑称）",
            "性格特质",
            "优势分析",
            "缺点分析",
            "隐藏闪光点",
            "摩擦触发点",
            "核心缺陷",
            "风险与雷区",
            "相处建议",
            "相处避坑",
            "一句话总结",
        ]
        order_index = {name: i for i, name in enumerate(_SECTION_ORDER)}

        def _sec_key(sec: dict) -> tuple:
            title = sec.get("title") or ""
            return (order_index.get(title, 500), title)

        display_sections.sort(key=_sec_key)

        y = pad_t
        header_h = 148
        y += header_h + gap
        chip_lines = self._wrap_chips(measure_draw, data.meta_items, meta_font, content_width)
        if chip_lines:
            y += len(chip_lines) * 40 + 8
        role_items = data.roles[:]
        if role_items:
            role_lines = self._wrap_chips(measure_draw, role_items, tag_font, content_width)
            y += len(role_lines) * 38 + 8
        if quote:
            q_lines = self._wrap_text(measure_draw, f"“{quote}”", quote_font, content_width - 56)
            y += 22 + len(q_lines) * (int(getattr(quote_font, "size", 25)) + 8) + 16
        tag_lines = self._wrap_chips(measure_draw, data.tags, tag_font, content_width)
        if tag_lines:
            y += 26 + len(tag_lines) * 38 + 10
        if data.metric_cards:
            y += 74 + 14
        elif data.stats:
            y += 38 + 10
        if data.catchphrases:
            catch_lines = self._wrap_chips(measure_draw, data.catchphrases, tag_font, content_width)
            y += 26 + len(catch_lines) * 38 + 10
        if data.period_bars and any(r > 0 for _, r in data.period_bars):
            y += 98 + 12
        line_spacing = int(fonts["line_spacing"])
        paragraph_spacing = int(fonts["paragraph_spacing"])
        body_size = int(getattr(body_font, "size", 23))
        for section in display_sections:
            y += 16
            if section["title"]:
                y += self._text_height(measure_draw, section["title"], section_font, content_width - 72)
                y += 12
            for para in section["paragraphs"]:
                lines = self._wrap_text(measure_draw, para, body_font, content_width - 72)
                y += len(lines) * (body_size + line_spacing)
                y += paragraph_spacing
            y += 10 + gap // 2
        y += 8
        footer_text = self._build_footer(data)
        y += self._text_height(measure_draw, footer_text, footer_font, content_width)
        y += pad_b
        height = max(int(canvas["min_height"]), y)
        image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        self._draw_background(image, colors, theme, height)
        self._draw_cute_vectors(image, theme, width, height)
        card_box = (pad_l - 18, pad_t - 18, width - pad_r + 18, height - pad_b + 10)
        self._draw_frosted_card(image, card_box, colors, radius=34)
        ribbon = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        rdraw = ImageDraw.Draw(ribbon)
        rdraw.rounded_rectangle((pad_l - 6, pad_t - 8, width - pad_r + 6, pad_t + 10), radius=12, fill=theme["ribbon"])
        ribbon = ribbon.filter(ImageFilter.GaussianBlur(radius=6))
        image.alpha_composite(ribbon)
        draw = ImageDraw.Draw(image)

        avatar_size = 112
        avatar_x, avatar_y = pad_l, pad_t + 10
        self._draw_avatar(
            image, draw, data.avatar_bytes,
            (avatar_x, avatar_y, avatar_x + avatar_size, avatar_y + avatar_size),
            ring_color=theme["accent"], placeholder_bg=theme["badge"],
            placeholder_fg=theme["badge_text"], nickname=data.nickname, font=title_font,
        )
        text_x = avatar_x + avatar_size + 22
        text_max_w = width - pad_r - text_x
        draw.text((text_x, avatar_y + 10), data.nickname, font=title_font, fill=tuple(colors["title"]))
        title_w = int(draw.textlength(data.title, font=meta_font))
        badge_x, badge_y = text_x, avatar_y + 68
        badge_box = (badge_x, badge_y, badge_x + title_w + 30, badge_y + 32)
        self._glass_chip(draw, badge_box, fill=theme["badge"], outline=theme["accent"][:3] + (90,), radius=16)
        draw.text((badge_x + 15, badge_y + 6), data.title, font=meta_font, fill=theme["badge_text"])
        if data.subtitle:
            draw.text(
                (badge_x + title_w + 40, badge_y + 7),
                self._truncate(draw, data.subtitle, subtitle_font, text_max_w - title_w - 50),
                font=subtitle_font, fill=tuple(colors["muted"]),
            )
        cursor_y = pad_t + header_h + gap

        if chip_lines:
            for row in chip_lines:
                x = pad_l
                for chip in row:
                    tw = int(draw.textlength(chip, font=meta_font))
                    box = (x, cursor_y, x + tw + 22, cursor_y + 30)
                    self._glass_chip(draw, box, fill=tuple(colors["chip"]), outline=tuple(colors["divider"]), radius=15)
                    draw.text((x + 11, cursor_y + 5), chip, font=meta_font, fill=tuple(colors["chip_text"]))
                    x += tw + 28
                cursor_y += 40
            cursor_y += 4
        if role_items:
            role_lines = self._wrap_chips(draw, role_items, tag_font, content_width)
            for row in role_lines:
                x = pad_l
                for role in row:
                    tw = int(draw.textlength(role, font=tag_font))
                    box = (x, cursor_y, x + tw + 28, cursor_y + 32)
                    self._glass_chip(draw, box, fill=theme["accent"][:3] + (36,), outline=theme["accent"][:3] + (120,), radius=16)
                    draw.text((x + 14, cursor_y + 6), role, font=tag_font, fill=theme["badge_text"])
                    x += tw + 34
                cursor_y += 38
            cursor_y += 6
        if quote:
            q_lines = self._wrap_text(draw, f"“{quote}”", quote_font, content_width - 56)
            q_h = 20 + len(q_lines) * (int(getattr(quote_font, "size", 25)) + 8) + 14
            qbox = (pad_l, cursor_y, pad_l + content_width, cursor_y + q_h)
            self._glass_chip(draw, qbox, fill=theme["badge"][:3] + (110,), outline=theme["accent"][:3] + (80,), radius=22)
            draw.rounded_rectangle((pad_l + 12, cursor_y + 16, pad_l + 18, cursor_y + q_h - 16), radius=3, fill=theme["accent"])
            ty = cursor_y + 16
            for line in q_lines:
                draw.text((pad_l + 30, ty), line, font=quote_font, fill=tuple(colors["title"]))
                ty += int(getattr(quote_font, "size", 25)) + 8
            cursor_y += q_h + 14
        if tag_lines:
            draw.text((pad_l, cursor_y), "性格标签", font=meta_font, fill=tuple(colors["section_title"]))
            self._motif_flower(draw, pad_l + 82, cursor_y + 8, 6, theme["accent"][:3] + (150,))
            cursor_y += 26
            for row in tag_lines:
                x = pad_l
                for tag in row:
                    tw = int(draw.textlength(tag, font=tag_font))
                    box = (x, cursor_y, x + tw + 28, cursor_y + 32)
                    self._glass_chip(draw, box, fill=theme["badge"], outline=theme["accent"][:3] + (110,), radius=16)
                    draw.ellipse((x + 9, cursor_y + 12, x + 16, cursor_y + 19), fill=theme["accent"])
                    draw.text((x + 20, cursor_y + 6), tag, font=tag_font, fill=theme["badge_text"])
                    x += tw + 34
                cursor_y += 38
            cursor_y += 8
        if data.metric_cards:
            n = len(data.metric_cards)
            gap_m = 12
            card_w = (content_width - gap_m * (n - 1)) // max(n, 1)
            card_h = 70
            for i, (label, value) in enumerate(data.metric_cards):
                x0 = pad_l + i * (card_w + gap_m)
                box = (x0, cursor_y, x0 + card_w, cursor_y + card_h)
                self._glass_chip(draw, box, fill=theme.get("metric", theme["badge"])[:3] + (175,), outline=theme["accent"][:3] + (70,), radius=18)
                draw.text((x0 + 14, cursor_y + 12), label, font=metric_label_font, fill=tuple(colors["muted"]))
                draw.text((x0 + 14, cursor_y + 34), self._truncate(draw, value, metric_value_font, card_w - 28), font=metric_value_font, fill=theme["badge_text"])
            cursor_y += card_h + 14
        elif data.stats:
            bar_h = 34
            self._glass_chip(draw, (pad_l, cursor_y, pad_l + content_width, cursor_y + bar_h), fill=theme["badge"][:3] + (110,), outline=theme["accent"][:3] + (70,), radius=14)
            sx = pad_l + 16
            for i, item in enumerate(data.stats):
                if i:
                    draw.text((sx, cursor_y + 7), "·", font=meta_font, fill=tuple(colors["muted"]))
                    sx += int(draw.textlength("·", font=meta_font)) + 10
                draw.text((sx, cursor_y + 6), item, font=meta_font, fill=tuple(colors["muted"]))
                sx += int(draw.textlength(item, font=meta_font)) + 14
            cursor_y += bar_h + 12
        if data.catchphrases:
            draw.text((pad_l, cursor_y), "口头禅", font=meta_font, fill=tuple(colors["section_title"]))
            self._motif_bubble(draw, pad_l + 68, cursor_y + 8, 7, theme["accent"][:3] + (150,))
            cursor_y += 26
            catch_lines = self._wrap_chips(draw, data.catchphrases, tag_font, content_width)
            for row in catch_lines:
                x = pad_l
                for phrase in row:
                    tw = int(draw.textlength(phrase, font=tag_font))
                    box = (x, cursor_y, x + tw + 26, cursor_y + 32)
                    self._glass_chip(draw, box, fill=theme["accent"][:3] + (28,), outline=theme["accent"][:3] + (110,), radius=16)
                    draw.text((x + 13, cursor_y + 6), phrase, font=tag_font, fill=theme["badge_text"])
                    x += tw + 34
                cursor_y += 38
            cursor_y += 8
        if data.period_bars and any(r > 0 for _, r in data.period_bars):
            bar_box_h = 94
            self._glass_chip(draw, (pad_l, cursor_y, pad_l + content_width, cursor_y + bar_box_h), fill=theme.get("metric", theme["badge"])[:3] + (160,), outline=theme["accent"][:3] + (70,), radius=20)
            draw.text((pad_l + 16, cursor_y + 10), "活跃时段分布", font=meta_font, fill=tuple(colors["section_title"]))
            chart_top = cursor_y + 34
            chart_bottom = cursor_y + bar_box_h - 26
            chart_h = max(chart_bottom - chart_top, 1)
            n = len(data.period_bars)
            gap_b = 10
            usable_w = content_width - 32
            bar_w = max(18, (usable_w - gap_b * (n - 1)) // max(n, 1))
            max_r = max((r for _, r in data.period_bars), default=0) or 1
            for i, (label, ratio) in enumerate(data.period_bars):
                x0 = pad_l + 16 + i * (bar_w + gap_b)
                h = int(chart_h * (ratio / max_r)) if ratio > 0 else 0
                y1 = chart_bottom
                y0 = y1 - max(h, 3 if ratio > 0 else 0)
                if ratio > 0:
                    draw.rounded_rectangle((x0, y0, x0 + bar_w, y1), radius=8, fill=theme["accent"][:3] + (185,))
                    if h > 10:
                        draw.ellipse((x0 + 3, y0 + 2, x0 + bar_w - 3, y0 + 10), fill=(255, 255, 255, 70))
                lbl = self._truncate(draw, label, metric_label_font, bar_w + 8)
                lw = int(draw.textlength(lbl, font=metric_label_font))
                draw.text((x0 + max((bar_w - lw) / 2, 0), chart_bottom + 5), lbl, font=metric_label_font, fill=tuple(colors["muted"]))
            cursor_y += bar_box_h + 14

        for section in display_sections:
            block_top = cursor_y
            text_y = block_top + 18
            title = section["title"]
            if title:
                text_y += self._text_height(draw, title, section_font, content_width - 72) + 10
            for para in section["paragraphs"]:
                lines = self._wrap_text(draw, para, body_font, content_width - 72)
                text_y += len(lines) * (body_size + line_spacing) + paragraph_spacing
            block_bottom = text_y + 8
            grad_colors, bar_rgb, icon_rgb = self._section_gradient_colors(title or "", theme)
            self._draw_gradient_section(
                image,
                (pad_l, block_top, pad_l + content_width, block_bottom),
                colors=grad_colors,
                alpha=158,
                radius=22,
                outline=tuple(colors["divider"]),
                accent=theme["accent"],
                confetti_seed=hash(title or "") & 0xFFFFFFFF,
            )
            draw = ImageDraw.Draw(image)
            # 左侧竖条：与区块同色系但更深，做出区分
            draw.rounded_rectangle(
                (pad_l + 10, block_top + 16, pad_l + 16, block_bottom - 16),
                radius=3,
                fill=bar_rgb + (210,),
            )
            text_y = block_top + 18
            if title:
                kind = _SECTION_ICON_KIND.get(title, "dot")
                icon_box = (pad_l + 26, int(text_y) + 1, pad_l + 50, int(text_y) + 25)
                # 图标底用区块渐变浅色，图标本体用同系更深色
                soft_fill = grad_colors[0] + (230,)
                self._draw_section_icon(
                    draw,
                    icon_box,
                    kind=kind,
                    color=icon_rgb + (255,),
                    fill=soft_fill,
                )
                draw.text((pad_l + 58, text_y), title, font=section_font, fill=tuple(colors["section_title"]))
                text_y += self._text_height(draw, title, section_font, content_width - 72) + 6
                draw.line(
                    (pad_l + 58, text_y, pad_l + content_width - 22, text_y),
                    fill=bar_rgb + (90,),
                    width=1,
                )
                text_y += 10
            for para in section["paragraphs"]:
                lines = self._wrap_text(draw, para, body_font, content_width - 72)
                for line in lines:
                    draw.text((pad_l + 58, text_y), line, font=body_font, fill=tuple(colors["text"]))
                    text_y += body_size + line_spacing
                text_y += paragraph_spacing
            cursor_y = block_bottom + gap // 2

        footer_y = height - pad_b - int(getattr(footer_font, "size", 15)) - 4
        draw.text((pad_l, footer_y), footer_text, font=footer_font, fill=tuple(colors["footer"]))
        self._motif_leaf(draw, pad_l + int(draw.textlength(footer_text, font=footer_font)) + 14, footer_y + 6, 6, theme["accent"][:3] + (100,))
        out = BytesIO()
        image.convert("RGB").save(out, format="PNG", optimize=True)
        return out.getvalue()

    def render_to_file(self, data: PortraitCardData, output_path: Path) -> Path:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(self.render(data))
        return output_path

    def _load_style(self, path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)

    def _font(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        key = (size, bold)
        if key in self._font_cache:
            return self._font_cache[key]
        candidates = list(_FONT_CANDIDATES)
        if bold:
            candidates = [
                Path(r"C:\Windows\Fonts\msyhbd.ttc"),
                _BUNDLED_FONT_DIR / "LXGWWenKaiLite-Regular.ttf",
                *candidates,
            ]
        for p in candidates:
            if not p.exists():
                continue
            try:
                font = ImageFont.truetype(str(p), size=size, index=0) if p.suffix.lower() == ".ttc" else ImageFont.truetype(str(p), size=size)
                self._font_cache[key] = font
                return font
            except OSError:
                continue
        font = ImageFont.load_default()
        self._font_cache[key] = font
        return font

    def _draw_background(self, image: Image.Image, colors: dict[str, Any], theme: dict[str, tuple[int, ...]], height: int) -> None:
        width = image.size[0]
        top = tuple(colors.get("background_top", (255, 248, 252, 255)))[:3]
        mid = tuple(colors.get("background_mid", (245, 240, 255, 255)))[:3]
        bottom = tuple(colors.get("background_bottom", (236, 248, 255, 255)))[:3]
        base = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        bdraw = ImageDraw.Draw(base)
        for y in range(height):
            r = y / max(height - 1, 1)
            if r < 0.5:
                t = r / 0.5
                color = tuple(int(top[i] * (1 - t) + mid[i] * t) for i in range(3)) + (255,)
            else:
                t = (r - 0.5) / 0.5
                color = tuple(int(mid[i] * (1 - t) + bottom[i] * t) for i in range(3)) + (255,)
            bdraw.line((0, y, width, y), fill=color)
        blob = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        bd = ImageDraw.Draw(blob)
        blobs = [
            (colors.get("blob_pink", theme.get("glow", (255, 186, 206, 70))), (-80, -40, 340, 300)),
            (colors.get("blob_lavender", (198, 188, 255, 65)), (width - 360, 80, width + 80, 420)),
            (colors.get("blob_mint", (170, 230, 220, 60)), (40, height - 360, 380, height + 40)),
            (colors.get("blob_peach", (255, 210, 180, 55)), (width - 300, height - 280, width + 40, height + 20)),
        ]
        for fill, box in blobs:
            f = tuple(fill) if len(fill) == 4 else tuple(fill)[:3] + (60,)
            bd.ellipse(box, fill=f)
        blur = int(self.style.get("decor", {}).get("frost_blur", 18))
        blob = blob.filter(ImageFilter.GaussianBlur(radius=blur))
        base.alpha_composite(blob)
        image.alpha_composite(base)

    def _draw_cute_vectors(self, image: Image.Image, theme: dict[str, tuple[int, ...]], width: int, height: int) -> None:
        """背景稀疏点缀：云朵/气球/花朵/蝴蝶结/月亮 等混搭，避免全是星星爱心。"""
        layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        accent = theme["accent"][:3]
        soft = accent + (75,)
        soft2 = accent + (55,)
        # 右上：云 + 气球
        self._motif_cloud(d, width - 130, 78, 22, soft)
        self._motif_balloon(d, width - 70, 120, 12, soft)
        # 左下：花
        self._motif_flower(d, 78, height - 110, 11, soft)
        # 右下：蝴蝶结
        self._motif_bow(d, width - 100, height - 120, 12, soft2)
        # 中侧：月亮 / 小圆点
        self._motif_moon(d, 58, height // 2, 10, soft2)
        for x, y, r in [(width - 48, 220, 2), (60, 200, 2), (width - 80, height // 2 + 60, 3)]:
            d.ellipse((x - r, y - r, x + r, y + r), fill=soft2)
        image.alpha_composite(layer)

    def _draw_frosted_card(self, image: Image.Image, box: tuple[int, int, int, int], colors: dict[str, Any], *, radius: int = 32) -> None:
        x0, y0, x1, y1 = box
        shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
        sd = ImageDraw.Draw(shadow)
        sh = tuple(colors.get("card_shadow", (160, 140, 190, 48)))
        sd.rounded_rectangle((x0 + 6, y0 + 10, x1 + 6, y1 + 12), radius=radius, fill=sh)
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=14))
        image.alpha_composite(shadow)
        panel = Image.new("RGBA", image.size, (0, 0, 0, 0))
        pd = ImageDraw.Draw(panel)
        pd.rounded_rectangle(box, radius=radius, fill=tuple(colors["card"]), outline=tuple(colors["card_border"]), width=2)
        hi = tuple(colors.get("glass_highlight", (255, 255, 255, 90)))
        pd.rounded_rectangle((x0 + 18, y0 + 10, x1 - 18, y0 + 28), radius=10, fill=hi)
        image.alpha_composite(panel)

    @staticmethod
    def _glass_chip(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *, fill: tuple[int, ...], outline: tuple[int, ...] | None = None, radius: int = 16) -> None:
        draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=1)

    def _draw_gradient_section(
        self,
        image: Image.Image,
        box: tuple[int, int, int, int],
        *,
        colors: list[tuple[int, int, int]],
        alpha: int = 150,
        radius: int = 22,
        outline: tuple[int, ...] | None = None,
        accent: tuple[int, ...] = (255, 168, 188, 255),
        confetti_seed: int = 0,
    ) -> None:
        """半透明渐变区块底；可点缀少量彩色点/短线（不杂乱，右侧不放矢量图）。"""
        x0, y0, x1, y1 = [int(v) for v in box]
        w, h = max(x1 - x0, 1), max(y1 - y0, 1)
        grad = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        gd = ImageDraw.Draw(grad)
        c0, c1 = colors[0], colors[-1] if len(colors) > 1 else colors[0]
        for i in range(h):
            t = i / max(h - 1, 1)
            tx = 0.12 * (0.5 - abs(0.5 - t))
            r = int(c0[0] * (1 - t - tx) + c1[0] * (t + tx))
            g = int(c0[1] * (1 - t - tx) + c1[1] * (t + tx))
            b = int(c0[2] * (1 - t - tx) + c1[2] * (t + tx))
            gd.line((0, i, w, i), fill=(r, g, b, alpha))

        # 少量随机彩色点/短线（基于 seed 稳定，不每帧乱跳）
        import random
        rng = random.Random((confetti_seed * 1315423911) ^ (w * 31 + h))
        palette = [
            (255, 170, 190, 55),
            (170, 200, 255, 50),
            (180, 230, 200, 50),
            (220, 190, 255, 48),
            (255, 210, 170, 48),
        ]
        n_dots = 3 if h < 90 else (4 if h < 140 else 5)
        for _ in range(n_dots):
            col = palette[rng.randrange(len(palette))]
            px = rng.randint(int(w * 0.55), max(int(w * 0.55), w - 18))
            py = rng.randint(12, max(12, h - 14))
            rr = rng.choice([1, 1, 2, 2, 3])
            gd.ellipse((px - rr, py - rr, px + rr, py + rr), fill=col)
        if h >= 70 and rng.random() < 0.7:
            col = palette[rng.randrange(len(palette))]
            lx = rng.randint(int(w * 0.62), max(int(w * 0.62), w - 28))
            ly = rng.randint(16, max(16, h - 20))
            lw = rng.randint(8, 16)
            gd.line((lx, ly, lx + lw, ly + rng.choice([-2, -1, 1, 2])), fill=col, width=1)

        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius, fill=255)
        panel = Image.new("RGBA", image.size, (0, 0, 0, 0))
        panel.paste(grad, (x0, y0), mask)
        sheen = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        sd = ImageDraw.Draw(sheen)
        sd.rounded_rectangle((8, 6, w - 8, min(22, h // 4)), radius=8, fill=(255, 255, 255, 50))
        panel.paste(sheen, (x0, y0), sheen)
        od = ImageDraw.Draw(panel)
        od.rounded_rectangle((x0, y0, x1, y1), radius=radius, outline=outline or (255, 255, 255, 160), width=1)
        image.alpha_composite(panel)

    def _section_gradient_colors(
        self, title: str, theme: dict[str, tuple[int, ...]]
    ) -> tuple[list[tuple[int, int, int]], tuple[int, int, int], tuple[int, int, int]]:
        """返回 (渐变双色, 左侧竖条色, 标题图标色)。

        竖条/图标色与背景同色系但更深一点，做出区分且不刺眼。
        """
        accent = theme["accent"][:3]
        # (grad, bar_rgb, icon_rgb)
        palettes = {
            "群内人设": ([(244, 240, 255), (255, 240, 248)], (186, 160, 220), (168, 138, 210)),
            "身份档案": ([(245, 242, 255), (255, 244, 250)], (170, 150, 215), (150, 130, 200)),
            "一句话总结": ([(255, 240, 246), (244, 236, 255)], (220, 140, 180), (200, 120, 165)),
            "语言风格": ([(255, 236, 244), (236, 232, 255)], (230, 150, 188), (210, 130, 175)),
            "社交姿态": ([(236, 244, 255), (244, 236, 255)], (140, 170, 230), (120, 150, 215)),
            "关系网": ([(240, 246, 255), (248, 240, 255)], (130, 160, 220), (110, 140, 205)),
            "互动关系": ([(240, 246, 255), (248, 240, 255)], (130, 160, 220), (110, 140, 205)),
            "兴趣与话题": ([(240, 255, 246), (255, 244, 236)], (120, 190, 160), (90, 170, 140)),
            "兴趣与价值": ([(240, 255, 246), (255, 244, 236)], (120, 190, 160), (90, 170, 140)),
            "性格特质": ([(244, 240, 255), (255, 240, 248)], (170, 150, 220), (150, 128, 205)),
            "优势分析": ([(236, 252, 244), (248, 255, 240)], (110, 185, 150), (80, 165, 130)),
            "缺点分析": ([(255, 240, 244), (252, 236, 240)], (220, 140, 165), (200, 118, 148)),
            "隐藏闪光点": ([(255, 248, 236), (255, 240, 248)], (230, 180, 120), (215, 160, 95)),
            "摩擦触发点": ([(255, 242, 240), (255, 236, 244)], (225, 145, 145), (210, 120, 125)),
            "核心缺陷": ([(252, 238, 242), (248, 236, 244)], (210, 135, 160), (190, 115, 145)),
            "风险与雷区": ([(255, 240, 242), (252, 236, 246)], (220, 140, 155), (200, 120, 140)),
            "相处建议": ([(240, 248, 255), (244, 240, 255)], (145, 165, 220), (125, 145, 205)),
            "相处避坑": ([(240, 248, 255), (244, 240, 255)], (145, 165, 220), (125, 145, 205)),
            "名场面与荣誉": ([(255, 246, 232), (255, 240, 244)], (230, 175, 120), (215, 155, 95)),
            "群荣誉": ([(255, 246, 232), (255, 240, 244)], (230, 175, 120), (215, 155, 95)),
            "群荣誉（黑称）": ([(248, 236, 240), (244, 232, 240)], (200, 140, 160), (180, 120, 145)),
        }
        if title in palettes:
            return palettes[title]
        c1 = tuple(min(255, int(200 + accent[i] * 0.2)) for i in range(3))
        c2 = tuple(min(255, int(220 + accent[i] * 0.12)) for i in range(3))
        bar = tuple(max(80, min(230, int(accent[i] * 0.75 + 40))) for i in range(3))
        icon = tuple(max(70, min(220, int(accent[i] * 0.7 + 30))) for i in range(3))
        return ([c1, c2], bar, icon)

    def _draw_motif_by_name(self, draw: ImageDraw.ImageDraw, name: str, x: float, y: float, r: float, fill: tuple[int, ...]) -> None:
        n = (name or "").lower()
        if n in {"flower", "spark", "兴趣"}:
            self._motif_flower(draw, x, y, r, fill)
        elif n in {"cloud", "minus"}:
            self._motif_cloud(draw, x, y, r + 2, fill)
        elif n in {"balloon"}:
            self._motif_balloon(draw, x, y, r, fill)
        elif n in {"bow"}:
            self._motif_bow(draw, x, y, r, fill)
        elif n in {"moon", "mind"}:
            self._motif_moon(draw, x, y, r, fill)
        elif n in {"leaf", "plus"}:
            self._motif_leaf(draw, x, y, r, fill)
        elif n in {"chat", "social"}:
            self._motif_bubble(draw, x, y, r, fill)
        elif n in {"medal"}:
            self._motif_medal(draw, x, y, r, fill)
        elif n in {"hint"}:
            self._motif_bulb(draw, x, y, r, fill)
        elif n in {"alert"}:
            self._motif_bolt(draw, x, y, r, fill)
        elif n in {"role"}:
            self._motif_face(draw, x, y, r, fill)
        elif n in {"heart"}:
            self._draw_mini_heart(draw, x, y, r * 0.7, fill)
        else:
            self._draw_mini_star(draw, x, y, r * 0.7, fill)

    @staticmethod
    def _motif_flower(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: tuple[int, ...]) -> None:
        for i in range(5):
            a = math.radians(i * 72 - 90)
            px = x + math.cos(a) * r * 0.55
            py = y + math.sin(a) * r * 0.55
            draw.ellipse((px - r * 0.38, py - r * 0.38, px + r * 0.38, py + r * 0.38), fill=fill)
        core = (255, 255, 255, min(200, fill[3] if len(fill) > 3 else 160))
        draw.ellipse((x - r * 0.28, y - r * 0.28, x + r * 0.28, y + r * 0.28), fill=core)

    @staticmethod
    def _motif_cloud(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: tuple[int, ...]) -> None:
        draw.ellipse((x - r, y - r * 0.35, x - r * 0.1, y + r * 0.55), fill=fill)
        draw.ellipse((x - r * 0.45, y - r * 0.7, x + r * 0.45, y + r * 0.35), fill=fill)
        draw.ellipse((x + r * 0.05, y - r * 0.3, x + r, y + r * 0.55), fill=fill)

    @staticmethod
    def _motif_balloon(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: tuple[int, ...]) -> None:
        draw.ellipse((x - r * 0.7, y - r, x + r * 0.7, y + r * 0.55), fill=fill)
        draw.polygon([(x - r * 0.15, y + r * 0.5), (x + r * 0.15, y + r * 0.5), (x, y + r * 0.75)], fill=fill)
        draw.line((x, y + r * 0.75, x - r * 0.1, y + r * 1.45), fill=fill, width=max(1, int(r * 0.12)))

    @staticmethod
    def _motif_bow(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: tuple[int, ...]) -> None:
        draw.polygon([(x - r, y), (x - r * 0.15, y - r * 0.55), (x - r * 0.15, y + r * 0.55)], fill=fill)
        draw.polygon([(x + r, y), (x + r * 0.15, y - r * 0.55), (x + r * 0.15, y + r * 0.55)], fill=fill)
        draw.ellipse((x - r * 0.22, y - r * 0.22, x + r * 0.22, y + r * 0.22), fill=fill)

    @staticmethod
    def _motif_moon(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: tuple[int, ...]) -> None:
        draw.ellipse((x - r, y - r, x + r, y + r), fill=fill)
        # 挖空形成弯月（用浅色盖住，近似）
        cut = (255, 255, 255, max(0, (fill[3] if len(fill) > 3 else 80) - 20))
        draw.ellipse((x - r * 0.25, y - r * 0.85, x + r * 0.95, y + r * 0.55), fill=(255, 255, 255, 0))
        # 用与背景混合不了，改：覆盖一个略偏移的半透明白圆会发灰，直接画弯月多边形近似
        # 重画：外圆 + 内圆“擦除”用同色更透明不行。采用 crescent polygon
        # 简化：画实心圆 + 右侧更浅的圆叠成月牙感（高层 alpha）
        draw.ellipse((x - r * 0.1, y - r * 0.85, x + r * 1.05, y + r * 0.55), fill=(255, 255, 255, min(90, fill[3] if len(fill) > 3 else 70)))

    @staticmethod
    def _motif_leaf(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: tuple[int, ...]) -> None:
        draw.ellipse((x - r * 0.55, y - r * 0.9, x + r * 0.55, y + r * 0.9), fill=fill)
        draw.line((x, y - r * 0.7, x, y + r * 0.7), fill=(255, 255, 255, min(160, fill[3] if len(fill) > 3 else 120)), width=max(1, int(r * 0.12)))

    @staticmethod
    def _motif_bubble(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: tuple[int, ...]) -> None:
        draw.rounded_rectangle((x - r, y - r * 0.7, x + r, y + r * 0.45), radius=max(3, int(r * 0.35)), fill=fill)
        draw.polygon([(x - r * 0.25, y + r * 0.4), (x + r * 0.05, y + r * 0.4), (x - r * 0.35, y + r * 0.85)], fill=fill)

    @staticmethod
    def _motif_medal(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: tuple[int, ...]) -> None:
        draw.ellipse((x - r * 0.7, y - r * 0.35, x + r * 0.7, y + r * 0.95), outline=fill, width=max(2, int(r * 0.18)))
        draw.polygon([(x - r * 0.55, y - r * 0.85), (x, y - r * 0.15), (x + r * 0.55, y - r * 0.85)], fill=fill)

    @staticmethod
    def _motif_bulb(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: tuple[int, ...]) -> None:
        draw.ellipse((x - r * 0.65, y - r * 0.9, x + r * 0.65, y + r * 0.25), outline=fill, width=max(2, int(r * 0.16)))
        draw.rectangle((x - r * 0.32, y + r * 0.2, x + r * 0.32, y + r * 0.5), outline=fill, width=max(1, int(r * 0.12)))
        draw.line((x - r * 0.28, y + r * 0.65, x + r * 0.28, y + r * 0.65), fill=fill, width=max(1, int(r * 0.12)))

    @staticmethod
    def _motif_bolt(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: tuple[int, ...]) -> None:
        draw.polygon([
            (x - r * 0.15, y - r),
            (x + r * 0.45, y - r * 0.1),
            (x + r * 0.05, y - r * 0.1),
            (x + r * 0.25, y + r),
            (x - r * 0.45, y + r * 0.05),
            (x - r * 0.05, y + r * 0.05),
        ], fill=fill)

    @staticmethod
    def _motif_face(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: tuple[int, ...]) -> None:
        draw.ellipse((x - r * 0.35, y - r * 0.85, x + r * 0.35, y - r * 0.15), fill=fill)
        draw.pieslice((x - r * 0.7, y - r * 0.2, x + r * 0.7, y + r * 0.9), start=200, end=340, fill=fill)


    def _draw_section_icon(self, draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], *, kind: str, color: tuple[int, ...], fill: tuple[int, ...]) -> None:
        x0, y0, x1, y1 = box
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        size = max(min(x1 - x0, y1 - y0), 1)
        accent = color[:3] + (255,)
        soft = (fill[0], fill[1], fill[2], 210) if len(fill) >= 3 else (255, 240, 245, 210)
        stroke = max(2, int(size * 0.12))
        draw.ellipse((x0, y0, x1, y1), fill=soft, outline=accent[:3] + (100,), width=1)
        k = (kind or "dot").lower()
        if k == "quote":
            q = size * 0.12
            draw.ellipse((cx - 2.2 * q, cy - q, cx - 0.4 * q, cy + 0.8 * q), fill=accent)
            draw.ellipse((cx + 0.4 * q, cy - q, cx + 2.2 * q, cy + 0.8 * q), fill=accent)
        elif k == "role":
            hr = size * 0.16
            draw.ellipse((cx - hr, cy - size * 0.28, cx + hr, cy - size * 0.02), fill=accent)
            draw.pieslice((cx - size * 0.28, cy - size * 0.02, cx + size * 0.28, cy + size * 0.34), start=200, end=340, fill=accent)
        elif k == "social":
            draw.rounded_rectangle((x0 + size * 0.18, y0 + size * 0.2, x0 + size * 0.72, y0 + size * 0.55), radius=4, outline=accent, width=stroke)
            draw.rounded_rectangle((x0 + size * 0.3, y0 + size * 0.42, x0 + size * 0.84, y0 + size * 0.78), radius=4, fill=accent)
        elif k == "tag":
            pts = [(cx - size * 0.28, cy), (cx, cy - size * 0.28), (cx + size * 0.28, cy), (cx, cy + size * 0.28)]
            draw.polygon(pts, outline=accent)
            draw.ellipse((cx - size * 0.06, cy - size * 0.06, cx + size * 0.06, cy + size * 0.06), fill=accent)
        elif k == "pulse":
            pts = [(x0 + size * 0.16, cy), (x0 + size * 0.32, cy), (x0 + size * 0.42, cy - size * 0.22), (x0 + size * 0.52, cy + size * 0.22), (x0 + size * 0.62, cy - size * 0.1), (x0 + size * 0.84, cy)]
            draw.line(pts, fill=accent, width=stroke)
        elif k == "chat":
            draw.rounded_rectangle((x0 + size * 0.18, y0 + size * 0.2, x1 - size * 0.18, y1 - size * 0.28), radius=5, outline=accent, width=stroke)
            draw.polygon([(cx - size * 0.08, y1 - size * 0.3), (cx + size * 0.02, y1 - size * 0.3), (cx - size * 0.12, y1 - size * 0.14)], fill=accent)
        elif k == "spark":
            pts = [(cx, y0 + size * 0.12), (cx + size * 0.08, cy - size * 0.08), (x1 - size * 0.12, cy), (cx + size * 0.08, cy + size * 0.08), (cx, y1 - size * 0.12), (cx - size * 0.08, cy + size * 0.08), (x0 + size * 0.12, cy), (cx - size * 0.08, cy - size * 0.08)]
            draw.polygon(pts, fill=accent)
        elif k == "mind":
            draw.ellipse((x0 + size * 0.2, y0 + size * 0.18, x1 - size * 0.2, y1 - size * 0.22), outline=accent, width=stroke)
            draw.line((cx, y0 + size * 0.34, cx, y1 - size * 0.28), fill=accent, width=max(1, stroke - 1))
        elif k == "star":
            pts = []
            for i in range(5):
                a = math.radians(-90 + i * 72)
                pts.append((cx + math.cos(a) * size * 0.3, cy + math.sin(a) * size * 0.3))
                a2 = math.radians(-90 + i * 72 + 36)
                pts.append((cx + math.cos(a2) * size * 0.13, cy + math.sin(a2) * size * 0.13))
            draw.polygon(pts, fill=accent)
        elif k == "plus":
            w = size * 0.12
            draw.rectangle((cx - w, y0 + size * 0.22, cx + w, y1 - size * 0.22), fill=accent)
            draw.rectangle((x0 + size * 0.22, cy - w, x1 - size * 0.22, cy + w), fill=accent)
        elif k == "minus":
            w = size * 0.12
            draw.rectangle((x0 + size * 0.22, cy - w, x1 - size * 0.22, cy + w), fill=accent)
        elif k == "alert":
            draw.polygon([(cx, y0 + size * 0.16), (x1 - size * 0.18, y1 - size * 0.2), (x0 + size * 0.18, y1 - size * 0.2)], outline=accent)
            draw.line((cx, y0 + size * 0.34, cx, cy + size * 0.06), fill=accent, width=stroke)
            draw.ellipse((cx - size * 0.05, cy + size * 0.12, cx + size * 0.05, cy + size * 0.22), fill=accent)
        elif k == "medal":
            draw.ellipse((cx - size * 0.18, cy - size * 0.06, cx + size * 0.18, cy + size * 0.3), outline=accent, width=stroke)
            draw.polygon([(cx - size * 0.16, y0 + size * 0.18), (cx, cy - size * 0.02), (cx + size * 0.16, y0 + size * 0.18)], fill=accent)
        elif k == "hint":
            draw.ellipse((cx - size * 0.18, y0 + size * 0.16, cx + size * 0.18, cy + size * 0.12), outline=accent, width=stroke)
            draw.rectangle((cx - size * 0.1, cy + size * 0.1, cx + size * 0.1, cy + size * 0.22), outline=accent, width=max(1, stroke - 1))
            draw.line((cx - size * 0.08, cy + size * 0.28, cx + size * 0.08, cy + size * 0.28), fill=accent, width=stroke)
        else:
            draw.ellipse((cx - size * 0.1, cy - size * 0.1, cx + size * 0.1, cy + size * 0.1), fill=accent)

    @staticmethod
    def _draw_mini_star(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: tuple[int, ...]) -> None:
        pts = []
        for i in range(5):
            a = math.radians(-90 + i * 72)
            pts.append((x + math.cos(a) * r, y + math.sin(a) * r))
            a2 = math.radians(-90 + i * 72 + 36)
            pts.append((x + math.cos(a2) * r * 0.42, y + math.sin(a2) * r * 0.42))
        draw.polygon(pts, fill=fill)

    @staticmethod
    def _draw_mini_heart(draw: ImageDraw.ImageDraw, x: float, y: float, r: float, fill: tuple[int, ...]) -> None:
        draw.ellipse((x - r, y - r * 0.55, x, y + r * 0.45), fill=fill)
        draw.ellipse((x, y - r * 0.55, x + r, y + r * 0.45), fill=fill)
        draw.polygon([(x - r, y), (x + r, y), (x, y + r * 1.05)], fill=fill)

    def _draw_avatar(self, image: Image.Image, draw: ImageDraw.ImageDraw, avatar_bytes: bytes | None, box: tuple[int, int, int, int], *, ring_color: tuple[int, ...], placeholder_bg: tuple[int, ...], placeholder_fg: tuple[int, ...], nickname: str, font: ImageFont.ImageFont) -> None:
        x0, y0, x1, y1 = box
        size = x1 - x0
        draw.ellipse((x0 - 6, y0 - 6, x1 + 6, y1 + 6), fill=ring_color[:3] + (70,))
        draw.ellipse((x0 - 3, y0 - 3, x1 + 3, y1 + 3), fill=ring_color[:3] + (160,))
        avatar = None
        if avatar_bytes:
            try:
                avatar = Image.open(BytesIO(avatar_bytes)).convert("RGBA").resize((size, size), Image.Resampling.LANCZOS)
            except Exception:
                avatar = None
        if avatar is None:
            # 中性占位：简笔头像，避免数字/字母误导
            draw.ellipse(box, fill=placeholder_bg)
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            r = size * 0.18
            draw.ellipse((cx - r, cy - size * 0.22, cx + r, cy - size * 0.02), fill=placeholder_fg)
            draw.pieslice(
                (cx - size * 0.28, cy - size * 0.02, cx + size * 0.28, cy + size * 0.32),
                start=200,
                end=340,
                fill=placeholder_fg,
            )
            return
        mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(mask).ellipse((0, 0, size, size), fill=255)
        image.paste(avatar, (x0, y0), mask)

    def _split_sections(self, content: str) -> list[dict[str, Any]]:
        text = (content or "").replace("\r\n", "\n").strip()
        if not text:
            return [{"title": "", "paragraphs": ["（暂无画像内容）"]}]
        lines = text.split("\n")
        sections: list[dict[str, Any]] = []
        current_title = ""
        current_paras: list[str] = []
        buf: list[str] = []

        def flush_buf() -> None:
            nonlocal buf
            para = "\n".join(buf).strip()
            if para:
                current_paras.append(para)
            buf = []

        def flush_section() -> None:
            nonlocal current_title, current_paras
            flush_buf()
            if current_title or current_paras:
                sections.append({"title": current_title, "paragraphs": current_paras or ["（无内容）"]})
            current_title = ""
            current_paras = []

        for raw in lines:
            line = raw.rstrip()
            stripped = line.strip()
            if not stripped:
                flush_buf()
                continue
            is_heading = False
            title = stripped
            if stripped.startswith("#"):
                is_heading = True
                title = re.sub(r"^#{1,6}\s*", "", stripped).strip()
            elif stripped.startswith("【") and stripped.endswith("】") and 2 <= len(stripped) <= 24:
                is_heading = True
                title = stripped.strip("【】").strip()
            elif stripped.endswith("：") and 2 <= len(stripped) <= 12 and not re.match(r"^\d+", stripped) and " " not in stripped and "·" not in stripped:
                is_heading = True
                title = stripped[:-1].strip()
            if is_heading:
                flush_section()
                current_title = title
                continue
            cleaned = re.sub(r"[`*_~]", "", stripped)
            cleaned = re.sub(r"^[-*+]\s+", "• ", cleaned)
            buf.append(cleaned)
        flush_section()
        if not sections:
            sections = [{"title": "", "paragraphs": [text]}]
        return sections

    def _wrap_text(self, draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
        if not text:
            return [""]
        lines: list[str] = []
        current = ""
        for ch in text:
            if ch == "\n":
                lines.append(current)
                current = ""
                continue
            trial = current + ch
            if draw.textlength(trial, font=font) <= max_width:
                current = trial
            else:
                if current:
                    lines.append(current)
                current = ch
        if current:
            lines.append(current)
        return lines or [""]

    def _wrap_chips(self, draw: ImageDraw.ImageDraw, items: list[str], font: ImageFont.ImageFont, max_width: int) -> list[list[str]]:
        rows: list[list[str]] = []
        row: list[str] = []
        used = 0
        chip_gap = 10
        for item in items:
            text = self._truncate(draw, item, font, max_width - 40)
            tw = int(draw.textlength(text, font=font)) + 24
            need = tw if not row else tw + chip_gap
            if row and used + need > max_width:
                rows.append(row)
                row = [text]
                used = tw
            else:
                row.append(text)
                used += need
        if row:
            rows.append(row)
        return rows

    @staticmethod
    def _text_height(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> int:
        size = int(getattr(font, "size", 24))
        if not text:
            return size
        if draw.textlength(text, font=font) <= max_width:
            return size
        approx = max(1, int(draw.textlength(text, font=font) // max_width) + 1)
        return approx * (size + 6)

    @staticmethod
    def _truncate(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> str:
        text = (text or "").replace("\n", " ").strip()
        if not text:
            return ""
        if draw.textlength(text, font=font) <= max_width:
            return text
        ellipsis = "…"
        kept = ""
        for ch in text:
            trial = kept + ch + ellipsis
            if draw.textlength(trial, font=font) > max_width:
                break
            kept += ch
        return (kept or text[:1]) + ellipsis

    @staticmethod
    def _build_footer(data: PortraitCardData) -> str:
        # 底部署名：模版作者固定展示
        parts = [data.footer or "模版作者 沐沐沐倾丶"]
        # 仅 QQ 在页脚带账号；微信不带任何账号数字
        if getattr(data, "platform", "qq") == "qq" and data.meta_items:
            for item in data.meta_items:
                if item.startswith(("QQ ", "ID ")):
                    parts.append(item)
                    break
        if getattr(data, "platform", "") == "wechat":
            parts.append("微信")
        if data.generated_at:
            parts.append(data.generated_at)
        return "  ·  ".join(parts)


def render_portrait_image(*, profile: UserProfile, content: str, command: str = "画像", avatar_bytes: bytes | None = None, style_path: Path | None = None, platform: str | None = None, message_count: int | None = None, query_rounds: int | None = None, from_cache: bool = False, activity: ActivityStats | None = None) -> bytes:
    template = PortraitImageTemplate(style_path=style_path)
    data = PortraitCardData.from_profile(profile=profile, content=content, command=command, avatar_bytes=avatar_bytes, platform=platform, message_count=message_count, query_rounds=query_rounds, from_cache=from_cache, activity=activity)
    return template.render(data)
