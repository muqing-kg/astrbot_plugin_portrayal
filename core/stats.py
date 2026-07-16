from __future__ import annotations

import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any


_STOPWORDS = {
    "的",
    "了",
    "呢",
    "吗",
    "啊",
    "呀",
    "吧",
    "嘛",
    "哦",
    "哈",
    "呵",
    "嘿",
    "嗯",
    "是",
    "在",
    "有",
    "和",
    "就",
    "都",
    "也",
    "还",
    "又",
    "不",
    "没",
    "很",
    "太",
    "更",
    "最",
    "这",
    "那",
    "这个",
    "那个",
    "什么",
    "怎么",
    "怎样",
    "为什么",
    "可以",
    "应该",
    "可能",
    "因为",
    "所以",
    "但是",
    "然后",
    "如果",
    "虽然",
    "或者",
    "一个",
    "一下",
    "一些",
    "一样",
    "自己",
    "你们",
    "我们",
    "他们",
    "大家",
    "真的",
    "其实",
    "感觉",
    "知道",
    "觉得",
    "认为",
    "就是",
    "不是",
    "没有",
    "还是",
    "已经",
    "一直",
    "开始",
    "出来",
    "起来",
    "过来",
    "过去",
    "东西",
    "事情",
    "时候",
    "地方",
    "现在",
    "今天",
    "明天",
    "昨天",
    "哈哈",
    "哈哈哈",
    "哈哈哈哈",
    "hhhh",
    "hhh",
    "www",
    "wwww",
    "lol",
    "ok",
    "okay",
    "yes",
    "no",
    "emmm",
    "emmmm",
    "xswl",
    "awsl",
    "nb",
    "nbcs",
    "yyds",
    "bdjw",
    "dd",
    "ss",
    "qq",
    "wx",
}


_PERIODS = (
    ("清晨打卡人", range(5, 9)),
    ("上午搬砖党", range(9, 12)),
    ("午间摸鱼人", range(12, 14)),
    ("下午在线员", range(14, 18)),
    ("晚间话痨王", range(18, 23)),
    ("深夜修仙党", list(range(23, 24)) + list(range(0, 5))),
)


@dataclass(slots=True)
class MessageSample:
    """单条可分析消息样本。"""

    text: str
    timestamp: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "timestamp": int(self.timestamp or 0)}

    @classmethod
    def from_any(cls, value: Any) -> "MessageSample | None":
        if isinstance(value, MessageSample):
            return value
        if isinstance(value, str):
            text = value.strip()
            return cls(text=text) if text else None
        if isinstance(value, dict):
            text = str(value.get("text") or "").strip()
            if not text:
                return None
            ts = value.get("timestamp") or value.get("time") or 0
            try:
                ts_i = int(ts)
            except (TypeError, ValueError):
                ts_i = 0
            return cls(text=text, timestamp=ts_i)
        return None


@dataclass(slots=True)
class ActivityStats:
    """基于聊天样本的本地活跃与语言特征。"""

    message_count: int = 0
    total_chars: int = 0
    avg_chars: float = 0.0
    max_chars: int = 0
    active_days: int = 0
    peak_period: str = ""
    peak_hour: int | None = None
    hour_histogram: dict[int, int] = field(default_factory=dict)
    period_histogram: dict[str, int] = field(default_factory=dict)
    top_words: list[str] = field(default_factory=list)
    catchphrases: list[str] = field(default_factory=list)
    question_ratio: float = 0.0
    exclaim_ratio: float = 0.0
    emoji_ratio: float = 0.0
    english_mix_ratio: float = 0.0
    short_msg_ratio: float = 0.0
    long_msg_ratio: float = 0.0
    night_ratio: float = 0.0
    style_labels: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        # JSON key 统一成 str，便于落盘
        data["hour_histogram"] = {
            str(k): int(v) for k, v in (self.hour_histogram or {}).items()
        }
        data["period_histogram"] = {
            str(k): int(v) for k, v in (self.period_histogram or {}).items()
        }
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ActivityStats":
        if not data or not isinstance(data, dict):
            return cls()
        hour_raw = data.get("hour_histogram") or {}
        hour_histogram: dict[int, int] = {}
        if isinstance(hour_raw, dict):
            for k, v in hour_raw.items():
                try:
                    hour_histogram[int(k)] = int(v)
                except (TypeError, ValueError):
                    continue
        period_raw = data.get("period_histogram") or {}
        period_histogram: dict[str, int] = {}
        if isinstance(period_raw, dict):
            for k, v in period_raw.items():
                try:
                    period_histogram[str(k)] = int(v)
                except (TypeError, ValueError):
                    continue

        def _f(key: str, default: float = 0.0) -> float:
            try:
                return float(data.get(key, default) or default)
            except (TypeError, ValueError):
                return default

        def _i(key: str, default: int = 0) -> int:
            try:
                return int(data.get(key, default) or default)
            except (TypeError, ValueError):
                return default

        peak_hour = data.get("peak_hour")
        try:
            peak_hour_i = int(peak_hour) if peak_hour is not None and peak_hour != "" else None
        except (TypeError, ValueError):
            peak_hour_i = None

        top_words = data.get("top_words") or []
        catchphrases = data.get("catchphrases") or []
        style_labels = data.get("style_labels") or []
        if not isinstance(top_words, list):
            top_words = []
        if not isinstance(catchphrases, list):
            catchphrases = []
        if not isinstance(style_labels, list):
            style_labels = []

        return cls(
            message_count=_i("message_count"),
            total_chars=_i("total_chars"),
            avg_chars=round(_f("avg_chars"), 1),
            max_chars=_i("max_chars"),
            active_days=_i("active_days"),
            peak_period=str(data.get("peak_period") or ""),
            peak_hour=peak_hour_i,
            hour_histogram=hour_histogram,
            period_histogram=period_histogram,
            top_words=[str(x) for x in top_words if str(x).strip()],
            catchphrases=[str(x) for x in catchphrases if str(x).strip()],
            question_ratio=round(_f("question_ratio"), 3),
            exclaim_ratio=round(_f("exclaim_ratio"), 3),
            emoji_ratio=round(_f("emoji_ratio"), 3),
            english_mix_ratio=round(_f("english_mix_ratio"), 3),
            short_msg_ratio=round(_f("short_msg_ratio"), 3),
            long_msg_ratio=round(_f("long_msg_ratio"), 3),
            night_ratio=round(_f("night_ratio"), 3),
            style_labels=[str(x) for x in style_labels if str(x).strip()],
        )

    def summary_lines(self) -> list[str]:
        if self.message_count <= 0:
            return ["暂无足够样本"]
        lines = [
            f"样本消息：{self.message_count} 条",
            f"平均字数：{self.avg_chars:.1f}",
            f"最长一条：{self.max_chars} 字",
        ]
        if self.active_days > 0:
            lines.append(f"覆盖天数：约 {self.active_days} 天")
        if self.peak_period:
            hour_text = f"（高峰约 {self.peak_hour:02d}:00）" if self.peak_hour is not None else ""
            lines.append(f"活跃时段：{self.peak_period}{hour_text}")
        if self.night_ratio >= 0.25:
            lines.append(f"深夜发言占比：约 {self.night_ratio * 100:.0f}%")
        if self.top_words:
            lines.append("高频词：" + "、".join(self.top_words[:8]))
        if self.catchphrases:
            lines.append("口头禅候选：" + "、".join(self.catchphrases[:5]))
        if self.style_labels:
            lines.append("语言倾向：" + "、".join(self.style_labels))
        return lines

    def summary_text(self) -> str:
        return "\n".join(self.summary_lines())

    def card_stats(self) -> list[str]:
        items: list[str] = []
        if self.message_count > 0:
            items.append(f"样本 {self.message_count}")
        if self.avg_chars > 0:
            items.append(f"均长 {self.avg_chars:.0f}")
        if self.peak_period:
            items.append(self.peak_period)
        if self.top_words:
            items.append(f"高频 {self.top_words[0]}")
        return items[:5]

    def period_bars(self) -> list[tuple[str, float]]:
        """返回 6 段活跃比例，供卡片迷你条使用。"""
        if self.period_histogram:
            total = sum(self.period_histogram.values()) or 1
            return [
                (label, self.period_histogram.get(label, 0) / total)
                for label, _ in _PERIODS
            ]
        if not self.hour_histogram:
            return [(label, 0.0) for label, _ in _PERIODS]
        scores: list[tuple[str, int]] = []
        for label, hours in _PERIODS:
            scores.append((label, sum(self.hour_histogram.get(h, 0) for h in hours)))
        total = sum(v for _, v in scores) or 1
        return [(label, v / total) for label, v in scores]


def normalize_samples(values: list[Any]) -> list[MessageSample]:
    samples: list[MessageSample] = []
    for value in values:
        sample = MessageSample.from_any(value)
        if sample is not None:
            samples.append(sample)
    return samples


def compute_activity_stats(values: list[Any], *, top_n: int = 10) -> ActivityStats:
    samples = normalize_samples(values)
    if not samples:
        return ActivityStats()

    lengths = [len(s.text) for s in samples]
    total = sum(lengths)
    count = len(samples)
    avg = total / count if count else 0.0

    hour_hist: Counter[int] = Counter()
    day_keys: set[str] = set()
    for sample in samples:
        if sample.timestamp > 0:
            dt = datetime.fromtimestamp(sample.timestamp)
            hour_hist[dt.hour] += 1
            day_keys.add(dt.strftime("%Y-%m-%d"))

    peak_hour: int | None = None
    peak_period = ""
    period_hist: dict[str, int] = {}
    night_count = 0
    if hour_hist:
        peak_hour = max(hour_hist.items(), key=lambda kv: kv[1])[0]
        best_label = ""
        best_score = -1
        for label, hours in _PERIODS:
            score = sum(hour_hist.get(h, 0) for h in hours)
            period_hist[label] = score
            if score > best_score:
                best_score = score
                best_label = label
        peak_period = best_label
        night_hours = set(list(range(23, 24)) + list(range(0, 5)))
        night_count = sum(hour_hist.get(h, 0) for h in night_hours)

    question_n = sum(1 for s in samples if ("?" in s.text or "？" in s.text))
    exclaim_n = sum(1 for s in samples if ("!" in s.text or "！" in s.text))
    emoji_n = sum(1 for s in samples if _has_emoji_like(s.text))
    english_n = sum(1 for s in samples if re.search(r"[A-Za-z]{2,}", s.text))
    short_n = sum(1 for n in lengths if n <= 8)
    long_n = sum(1 for n in lengths if n >= 40)

    top_words = _extract_top_words([s.text for s in samples], top_n=top_n)
    catchphrases = _extract_catchphrases([s.text for s in samples], top_n=5)
    night_ratio = (night_count / sum(hour_hist.values())) if hour_hist else 0.0
    style_labels = _style_labels(
        question_ratio=question_n / count,
        exclaim_ratio=exclaim_n / count,
        emoji_ratio=emoji_n / count,
        english_mix_ratio=english_n / count,
        short_msg_ratio=short_n / count,
        long_msg_ratio=long_n / count,
        avg_chars=avg,
        peak_period=peak_period,
        night_ratio=night_ratio,
    )

    return ActivityStats(
        message_count=count,
        total_chars=total,
        avg_chars=round(avg, 1),
        max_chars=max(lengths) if lengths else 0,
        active_days=len(day_keys),
        peak_period=peak_period,
        peak_hour=peak_hour,
        hour_histogram={int(k): int(v) for k, v in sorted(hour_hist.items())},
        period_histogram=period_hist,
        top_words=top_words,
        catchphrases=catchphrases,
        question_ratio=round(question_n / count, 3),
        exclaim_ratio=round(exclaim_n / count, 3),
        emoji_ratio=round(emoji_n / count, 3),
        english_mix_ratio=round(english_n / count, 3),
        short_msg_ratio=round(short_n / count, 3),
        long_msg_ratio=round(long_n / count, 3),
        night_ratio=round(night_ratio, 3),
        style_labels=style_labels,
    )


def _has_emoji_like(text: str) -> bool:
    if re.search(r"[\U0001F300-\U0001FAFF]", text):
        return True
    return bool(re.search(r"(\[[^\]]{1,8}\]|[\u2600-\u27BF])", text))


def _extract_top_words(texts: list[str], *, top_n: int = 10) -> list[str]:
    counter: Counter[str] = Counter()
    for text in texts:
        # 2-4 字中文片段 + 英文词
        for token in re.findall(r"[\u4e00-\u9fff]{2,4}|[A-Za-z]{2,12}", text):
            key = token.lower() if re.fullmatch(r"[A-Za-z]+", token) else token
            if key in _STOPWORDS or key.isdigit():
                continue
            if len(key) < 2:
                continue
            counter[key] += 1
    # 去掉几乎每句都出现的超高频噪音：仅保留出现次数合理的
    return [w for w, _ in counter.most_common(top_n * 2) if counter[w] >= 2][:top_n]


def _extract_catchphrases(texts: list[str], *, top_n: int = 5) -> list[str]:
    """优先统计短句整句复读；过滤过长短粘连噪声。"""
    counter: Counter[str] = Counter()
    for text in texts:
        cleaned = re.sub(r"\s+", "", text or "")
        if 2 <= len(cleaned) <= 8 and cleaned not in _STOPWORDS:
            counter[cleaned] += 1
        for part in re.split(r"[，,。！!？?、；;：:\s]+", text or ""):
            part = re.sub(r"\s+", "", (part or "").strip())
            if 2 <= len(part) <= 8 and part not in _STOPWORDS:
                counter[part] += 1
    result: list[str] = []
    for word, cnt in counter.most_common(50):
        if cnt < 3:
            continue
        # 过滤几乎是长句切片的噪音：数字过多等
        if sum(ch.isdigit() for ch in word) >= 2:
            continue
        if any(word in kept or kept in word for kept in result):
            continue
        result.append(word)
        if len(result) >= top_n:
            break
    return result


def _style_labels(
    *,
    question_ratio: float,
    exclaim_ratio: float,
    emoji_ratio: float,
    english_mix_ratio: float,
    short_msg_ratio: float,
    long_msg_ratio: float,
    avg_chars: float,
    peak_period: str,
    night_ratio: float = 0.0,
) -> list[str]:
    labels: list[str] = []
    if peak_period:
        labels.append(peak_period)
    if night_ratio >= 0.35:
        labels.append("夜猫子")
    if short_msg_ratio >= 0.55 and avg_chars <= 12:
        labels.append("短句连发")
    elif long_msg_ratio >= 0.25 or avg_chars >= 36:
        labels.append("长文输出")
    if question_ratio >= 0.18:
        labels.append("爱提问")
    if exclaim_ratio >= 0.18:
        labels.append("情绪外放")
    if emoji_ratio >= 0.15:
        labels.append("表情密集")
    if english_mix_ratio >= 0.12:
        labels.append("中英夹杂")
    if not labels:
        labels.append("平稳输出")
    return labels[:5]
