"""agentkit.bridge — 宿主 Agent 磁盘工单队列协议（零 API key，collect/apply 两段式）。

技能侧用法：

1. 通过 ``register_kind(...)`` 注册领域 kind（分类匹配器 + 占位符工厂）；
   agentkit 内置仅有通用 ``chat`` 兜底；
2. 用 ``BridgeQueue`` 组合进自己的 LLM provider 适配器，把
   ``MissingAnswerError`` 翻译成技能自己的错误类型；
3. 需要保留历史默认队列目录的技能，向 ``resolve_queue_dir`` /
   ``BridgeQueue`` 显式传入 ``default`` / ``default_queue_dir``。

环境变量：``AGENTKIT_LLM_DIR`` / ``AGENTKIT_LLM_MODE``（新，优先），
``LIT_SCOUT_LLM_DIR`` / ``LIT_SCOUT_LLM_MODE``（compat, remove after one release）。
"""

from agentkit.bridge.core import (
    ANSWERS_JSON,
    ANSWERS_JSONL,
    DEFAULT_KIND,
    DEFAULT_QUEUE_DIR,
    ENV_MODE,
    ENV_QUEUE_DIR,
    LEGACY_ENV_MODE,
    LEGACY_ENV_QUEUE_DIR,
    PLACEHOLDER,
    REQUESTS_FILE,
    BridgeQueue,
    KindSpec,
    MissingAnswerError,
    classify_request,
    clear_kinds,
    job_id_for,
    load_answers,
    placeholder_for,
    queue_stats,
    read_requests,
    register_kind,
    registered_kinds,
    resolve_mode,
    resolve_queue_dir,
    unregister_kind,
)

__all__ = [
    "ANSWERS_JSON",
    "ANSWERS_JSONL",
    "DEFAULT_KIND",
    "DEFAULT_QUEUE_DIR",
    "ENV_MODE",
    "ENV_QUEUE_DIR",
    "LEGACY_ENV_MODE",
    "LEGACY_ENV_QUEUE_DIR",
    "PLACEHOLDER",
    "REQUESTS_FILE",
    "BridgeQueue",
    "KindSpec",
    "MissingAnswerError",
    "classify_request",
    "clear_kinds",
    "job_id_for",
    "load_answers",
    "placeholder_for",
    "queue_stats",
    "read_requests",
    "register_kind",
    "registered_kinds",
    "resolve_mode",
    "resolve_queue_dir",
    "unregister_kind",
]
