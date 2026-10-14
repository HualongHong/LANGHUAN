"""agentkit.config — .env 显式加载 + YAML extends/深合并/${VAR:default} 插值（C5）。

抽取自 Lit-Scout-Harvester ``core/config_loader.py``（LangHuan Phase 2 P2）。

设计红线（docs/INTEGRATION_PLAN.md §4）：本模块 **import 时无任何副作用** ——
不读 .env、不 chdir、不改 ``os.environ``。.env 加载必须通过显式调用
:func:`load_env` 完成；是否沿用"import 即加载"的遗留行为由技能侧 shim 决定
（LSH 的 shim 选择保留，见 ``lit_scout_harvester/core/config_loader.py``）。

依赖：纯 stdlib；``yaml`` 在 :func:`load_yaml` 调用时才延迟导入；
``python-dotenv`` 若可用则优先使用（与 LSH 原实现一致），否则回退到内置的
极简 .env 解析器。
"""

from agentkit.config.core import (
    ConfigError,
    ENV_VAR_PATTERN,
    deep_merge,
    interpolate,
    load_env,
    load_yaml,
)

__all__ = [
    "ConfigError",
    "ENV_VAR_PATTERN",
    "deep_merge",
    "interpolate",
    "load_env",
    "load_yaml",
]
