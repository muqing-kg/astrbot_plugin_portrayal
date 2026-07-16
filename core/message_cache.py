from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from astrbot.api import logger


@dataclass
class CachedMessages:
    """Store cached messages for one user.

    Prefer rich samples with timestamps when available. Keep ``texts`` for
    backward compatibility with older cache files and callers.
    """

    texts: list[str]
    timestamp: float
    samples: list[dict[str, Any]] = field(default_factory=list)

    def ensure_samples(self) -> list[dict[str, Any]]:
        if self.samples:
            return self.samples
        return [{"text": text, "timestamp": 0} for text in self.texts]

    def append_message(self, text: str, msg_timestamp: int = 0) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.texts.append(text)
        self.samples.append({"text": text, "timestamp": int(msg_timestamp or 0)})


class MessageCacheStorage:
    """Persist message cache state as JSON."""

    def __init__(self, cache_dir: Path):
        self.file = cache_dir / "message_cache.json"

    def load(self) -> tuple[dict[str, CachedMessages], dict[str, int]]:
        if not self.file.exists():
            return {}, {}

        try:
            payload: dict[str, Any] = json.loads(self.file.read_text(encoding="utf-8"))
            raw_users = payload.get("users", {})
            raw_cursors = payload.get("group_cursors", {})
            if not isinstance(raw_users, dict) or not isinstance(raw_cursors, dict):
                raise ValueError("Invalid message cache structure")

            users: dict[str, CachedMessages] = {}
            for key, value in raw_users.items():
                if not isinstance(key, str) or not isinstance(value, dict):
                    continue
                texts = value.get("texts")
                timestamp = value.get("timestamp")
                samples = value.get("samples") or []
                if not isinstance(texts, list) or not all(
                    isinstance(text, str) for text in texts
                ):
                    continue
                if not isinstance(timestamp, int | float):
                    continue
                clean_samples: list[dict[str, Any]] = []
                if isinstance(samples, list):
                    for item in samples:
                        if not isinstance(item, dict):
                            continue
                        text = str(item.get("text") or "").strip()
                        if not text:
                            continue
                        try:
                            ts = int(item.get("timestamp") or 0)
                        except (TypeError, ValueError):
                            ts = 0
                        clean_samples.append({"text": text, "timestamp": ts})
                if not clean_samples and texts:
                    clean_samples = [{"text": t, "timestamp": 0} for t in texts]
                users[key] = CachedMessages(
                    texts=texts,
                    timestamp=float(timestamp),
                    samples=clean_samples,
                )

            cursors = {
                str(group_id): int(cursor)
                for group_id, cursor in raw_cursors.items()
                if isinstance(cursor, int)
            }
            return users, cursors
        except Exception as e:
            logger.warning(f"Failed to load message cache: {e}")
            return {}, {}

    def save(
        self,
        users: dict[str, CachedMessages],
        group_cursors: dict[str, int],
    ) -> None:
        payload = {
            "users": {key: asdict(value) for key, value in users.items()},
            "group_cursors": group_cursors,
        }
        temporary_file = self.file.with_suffix(".tmp")
        try:
            temporary_file.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temporary_file.replace(self.file)
        except Exception as e:
            logger.error(f"Failed to save message cache: {e}")

    def clear(self) -> None:
        try:
            self.file.unlink(missing_ok=True)
        except Exception as e:
            logger.error(f"Failed to clear message cache: {e}")
