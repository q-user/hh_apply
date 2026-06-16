"""OpenAI client for shared kernel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import openai  # type: ignore[import-not-found]
    from openai.types.chat import (  # type: ignore[import-not-found]
        ChatCompletionMessageParam,
    )


@dataclass
class AIConfig:
    """Configuration for AI client."""

    api_key: str | None = None
    base_url: str | None = None
    model: str = "gpt-4o-mini"
    timeout: float = 60.0
    max_retries: int = 3


class AIClient:
    """Client for interacting with OpenAI API."""

    def __init__(self, config: AIConfig | None = None) -> None:
        self._config = config or AIConfig()
        self._client = openai.OpenAI(
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            timeout=self._config.timeout,
            max_retries=self._config.max_retries,
        )

    def complete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        """Generate completion for a prompt."""
        messages: list[ChatCompletionMessageParam] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        response = self._client.chat.completions.create(
            model=self._config.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""

    async def acomplete(
        self,
        prompt: str,
        system_prompt: str | None = None,
        temperature: float = 0.7,
        max_tokens: int | None = None,
    ) -> str:
        """Generate completion for a prompt (async)."""
        messages: list[ChatCompletionMessageParam] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        async_client = openai.AsyncOpenAI(
            api_key=self._config.api_key,
            base_url=self._config.base_url,
            timeout=self._config.timeout,
            max_retries=self._config.max_retries,
        )
        response = await async_client.chat.completions.create(
            model=self._config.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return response.choices[0].message.content or ""


def create_ai_client(config: AIConfig | None = None) -> AIClient:
    """Factory function to create an AIClient instance."""
    return AIClient(config=config)
