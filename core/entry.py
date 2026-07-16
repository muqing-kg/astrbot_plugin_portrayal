# config.py
from __future__ import annotations

from typing import Any

import yaml

from astrbot.api import logger

from .config import PluginConfig, PromptEntry


# 默认命令若缺少这些标题，则用内置 yaml 内容覆盖（升级兼容）
_REQUIRED_MARKERS: dict[str, tuple[str, ...]] = {
    "画像": ("【社交姿态】", "【优势分析】", "【缺点分析】", "【相处建议】"),
}


class EntryService:
    def __init__(self, config: PluginConfig):
        self.cfg = config
        self._migrate_entry_storage()

        # 加载用户配置
        self.entries: list[PromptEntry] = [
            PromptEntry(item) for item in self.cfg.entry_storage
        ]
        self._load_prompts()
        logger.debug(f"已注册命令：{[e.command for e in self.entries]}")

    def _load_prompts(self) -> None:
        with self.cfg.builtin_prompt_file.open("r", encoding="utf-8") as f:
            data: list[dict[str, Any]] = yaml.safe_load(f) or []
            self._refresh_builtin_contents(data)
            self.add_entry(data)

    def _refresh_builtin_contents(self, builtin_data: list[dict[str, Any]]) -> None:
        """当默认三命令的提示词缺少新维度标题时，用内置文件覆盖 content。"""
        builtin_by_cmd = {
            str(item.get("command") or ""): item
            for item in builtin_data
            if isinstance(item, dict) and item.get("command")
        }
        updated = False
        for item in self.cfg.entry_storage:
            if not isinstance(item, dict):
                continue
            cmd = str(item.get("command") or "")
            markers = _REQUIRED_MARKERS.get(cmd)
            if not markers:
                continue
            content = str(item.get("content") or "")
            if all(m in content for m in markers):
                continue
            src = builtin_by_cmd.get(cmd)
            if not src or not src.get("content"):
                continue
            item["content"] = src["content"]
            if "need_admin" not in item:
                item["need_admin"] = bool(src.get("need_admin", False))
            updated = True
            logger.info(f"已从内置文件刷新提示词内容：{cmd}")

        if updated:
            # 同步内存 entries
            self.entries = [PromptEntry(x) for x in self.cfg.entry_storage if isinstance(x, dict)]
            self.cfg.save_config()

    def _migrate_entry_storage(self) -> None:
        updated = False
        for item in self.cfg.entry_storage:
            if "need_admin" not in item:
                item["need_admin"] = False
                updated = True
        # 移除已废弃的正/负画像默认命令
        before = len(self.cfg.entry_storage)
        self.cfg.entry_storage[:] = [
            item
            for item in self.cfg.entry_storage
            if str((item or {}).get("command") or "") not in {"正画像", "负画像", "查看画像"}
        ]
        if len(self.cfg.entry_storage) != before:
            updated = True
            logger.info("已移除废弃提示词命令：正画像 / 负画像 / 查看画像")
        if updated:
            self.cfg.save_config()
            logger.info("已为旧版提示词配置补全 need_admin 字段")

    def add_entry(self, data: list[dict[str, Any]]) -> None:
        existed_commands = {e.command for e in self.entries}
        new_items: list[dict[str, Any]] = []

        for item in data:
            if item["command"] in existed_commands:
                continue
            if "need_admin" not in item:
                item["need_admin"] = False
            self.cfg.entry_storage.append(item)
            new_items.append(item)
            self.entries.append(PromptEntry(item))

        if new_items:
            self.cfg.save_config()
            logger.info(f"已加载提示词：{[item['command'] for item in new_items]}")

    def get_entry(self, command: str) -> PromptEntry | None:
        """获取条目"""
        for entry in self.entries:
            if entry.command == command:
                return entry
        return None
