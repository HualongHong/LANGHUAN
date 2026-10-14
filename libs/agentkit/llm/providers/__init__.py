"""agentkit.llm.providers — HTTP provider 实现（aiohttp 延迟导入）。

模块 import 本身只依赖 stdlib；首次发起请求时才通过
``agentkit.llm.base.require_aiohttp`` 加载 aiohttp，未安装时抛出带安装提示的
``ProviderError``。
"""

from agentkit.llm.providers.claude import ClaudeProvider
from agentkit.llm.providers.gemini import GeminiProvider
from agentkit.llm.providers.minimax import MiniMaxProvider
from agentkit.llm.providers.openai_compatible import (
    DeepSeekProvider,
    KimiProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
    QwenProvider,
)

__all__ = [
    "ClaudeProvider",
    "DeepSeekProvider",
    "GeminiProvider",
    "KimiProvider",
    "MiniMaxProvider",
    "OpenAICompatibleProvider",
    "OpenAIProvider",
    "QwenProvider",
]
