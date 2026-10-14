"""agentkit.llm — 统一 LLM 层（C1+C6）：provider 注册表、统一客户端、响应清洗。

- ``base``             LLMProvider ABC + ProviderError + require_aiohttp（延迟导入）
- ``registry``         PROVIDER_REGISTRY + get_provider + register_provider（开放注册）
- ``providers``        openai_compatible / minimax / gemini / claude（aiohttp 可选依赖）
- ``bridge_provider``  通用 AgentBridgeProvider（组合 agentkit.bridge.BridgeQueue；
                       kind 由使用方注册，默认队列目录可子类化覆盖）
- ``cleaning``         strip_think / strip_code_fence / clean_response（默认关闭，
                       由 LLMClient(clean=True) 开启）
- ``client``           LLMClient（缓存 + 重试 + fallback + 用量统计 usage_stats()）
- ``sync``             chat_sync() 同步包装（asyncio.run），供同步脚本使用

环境变量：``AGENTKIT_LLM_USAGE_LOG``（用量 JSONL 快照路径，默认关闭）；
队列相关 ``AGENTKIT_LLM_DIR`` / ``AGENTKIT_LLM_MODE`` 见 ``agentkit.bridge``。
"""

from agentkit.llm.base import LLMProvider, ProviderError, require_aiohttp
from agentkit.llm.bridge_provider import AgentBridgeProvider
from agentkit.llm.cleaning import clean_response, strip_code_fence, strip_think
from agentkit.llm.client import ENV_USAGE_LOG, LLMClient, LLMError
from agentkit.llm.providers import (
    ClaudeProvider,
    DeepSeekProvider,
    GeminiProvider,
    KimiProvider,
    MiniMaxProvider,
    OpenAICompatibleProvider,
    OpenAIProvider,
    QwenProvider,
)
from agentkit.llm.registry import PROVIDER_REGISTRY, get_provider, register_provider

__all__ = [
    "ENV_USAGE_LOG",
    "PROVIDER_REGISTRY",
    "AgentBridgeProvider",
    "ClaudeProvider",
    "DeepSeekProvider",
    "GeminiProvider",
    "KimiProvider",
    "LLMClient",
    "LLMError",
    "LLMProvider",
    "MiniMaxProvider",
    "OpenAICompatibleProvider",
    "OpenAIProvider",
    "ProviderError",
    "QwenProvider",
    "clean_response",
    "get_provider",
    "register_provider",
    "require_aiohttp",
    "strip_code_fence",
    "strip_think",
]
