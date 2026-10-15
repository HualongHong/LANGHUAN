"""测试环境兜底：确保进程内测试不依赖 ambient .env 里的真实 key。

config_loader 在 import 期以 override=True 加载技能根 .env（见该文件头部注释），
安装副本的 .env 是 langhuan.env 的硬链接、key 为空串，会把测试夹具需要的
DEEPSEEK_API_KEY 等覆盖成空值并触发 PipelineConfig 校验失败。本 conftest 在
该 import 副作用发生**之后**把空值/缺省回填为 dummy——上游仓库有真实 key 时
``or`` 短路保持原值，行为不变；子进程类集成测试不受 conftest 影响（仍需真实 key
或走 agent_bridge，属已知限制）。
"""

from __future__ import annotations

import os

from lit_scout_harvester.core import config_loader  # noqa: F401  触发 import 期 .env 加载

_DUMMY = "test-dummy-key"

for _name in (
    "DEEPSEEK_API_KEY",
    "KIMI_API_KEY",
    "MINIMAX_API_KEY",
    "SHANGHAI_SC_API_KEY",
    "SELFHOST_API_KEY",
    "ALIYUN_DEPLOY_API_KEY",
    "SCNET_DEPLOY_API_KEY",
):
    if not os.environ.get(_name):
        os.environ[_name] = _DUMMY
