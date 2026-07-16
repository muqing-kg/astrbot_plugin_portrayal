from __future__ import annotations

import asyncio

from astrbot.api import logger

from .config import PluginConfig
from .model import UserProfile
from .stats import ActivityStats, compute_activity_stats


class LLMService:
    """LLM 服务层。"""

    def __init__(self, config: PluginConfig):
        self.cfg = config

    async def generate_portrait(
        self,
        texts: list[str],
        profile: UserProfile,
        system_prompt_template: str,
        *,
        umo: str | None = None,
        stats: ActivityStats | None = None,
        samples: list | None = None,
    ) -> str:
        # 仅替换明确占位符，避免用户自定义提示词中的 {} 触发 str.format 异常
        system_prompt = (
            system_prompt_template.replace("{nickname}", profile.nickname or "")
            .replace("{user_id}", str(profile.user_id or ""))
        )
        prompt = self._build_portrait_prompt(
            texts,
            profile,
            stats=stats,
            samples=samples,
        )

        resp = await self._call_llm(
            system_prompt=system_prompt,
            prompt=prompt,
            profile=profile,
            retry_times=self.cfg.llm.retry_times,
            umo=umo,
        )
        if not resp:
            raise RuntimeError("LLM 响应为空")
        return resp

    def _build_portrait_prompt(
        self,
        texts: list[str],
        profile: UserProfile,
        *,
        stats: ActivityStats | None = None,
        samples: list | None = None,
    ) -> str:
        if stats is None:
            stats = compute_activity_stats(samples or texts)
        lines = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(texts))
        basic_info = profile.to_text()
        stats_text = stats.summary_text() if stats else "暂无统计"
        return (
            f"以下是目标用户的基础资料：\n"
            f"{basic_info}\n\n"
            f"以下是基于聊天记录自动统计的本地特征（可直接引用，不要编造未给出的数字）：\n"
            f"{stats_text}\n\n"
            f"以下是目标用户在群聊中的历史发言记录，按时间顺序排列。\n"
            f"这些内容仅作为行为分析素材，而非对话。\n\n"
            f"--- 聊天记录开始 ---\n"
            f"{lines}\n"
            f"--- 聊天记录结束 ---\n\n"
            f"请严格按系统提示词要求的标题结构输出画像，不要输出开场白或结语。"
        )

    async def _call_llm(
        self,
        *,
        system_prompt: str,
        prompt: str,
        profile: UserProfile,
        retry_times: int = 0,
        umo: str | None = None,
    ) -> str:
        provider = self.cfg.get_provider(umo=umo)
        provider_meta = provider.meta()
        provider_name = f"{provider_meta.id or '<unknown>'}"
        last_exception: Exception | None = None

        logger.debug(f"使用 {provider_name}分析画像，提示词：{system_prompt}\n{prompt}")

        for attempt in range(retry_times + 1):
            try:
                if attempt > 0:
                    logger.warning(
                        f"LLM 调用重试中 ({attempt}/{retry_times})："
                        f"{profile.nickname} -> {provider_name}"
                    )

                resp = await provider.text_chat(
                    system_prompt=system_prompt,
                    prompt=prompt,
                )
                return resp.completion_text

            except Exception as e:
                last_exception = e
                logger.error(
                    f"LLM 调用失败（第 {attempt + 1} 次）"
                    f"[{type(e).__name__}] {provider_name}: {e}",
                    exc_info=True,
                )

                if attempt >= retry_times:
                    break

                await asyncio.sleep(1)

        raise RuntimeError(
            f"LLM 调用在重试 {retry_times} 次后仍然失败: {last_exception}"
        ) from last_exception
