"""agentkit.cache — SQLite 内容哈希缓存（C4，技能无关，纯 stdlib）。

用法：``CacheManager(db_path)`` 提供 ``get/set/delete/clear/stats/contains``，
键为内容哈希（由调用方计算，如 ``agentkit.llm.client.LLMClient`` 的
SHA256(provider+model+payload)）。
"""

from agentkit.cache.core import CacheManager

__all__ = ["CacheManager"]
