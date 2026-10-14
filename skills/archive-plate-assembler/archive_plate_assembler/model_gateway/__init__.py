"""模型网关子包：角色化多模态模型网关（传输层由 agentkit.llm 承担）。

默认角色 A=MiniMax（主提取）、B=Qwen（校验）；新增角色用 register_role
注册 RoleProfile，不改网关代码。
"""

from .gateway import (
    ModelGateway,
    ModelGatewayError,
    RoleProfile,
    register_role,
    registered_roles,
)
from .mock import MockGateway

__all__ = [
    "ModelGateway",
    "ModelGatewayError",
    "MockGateway",
    "RoleProfile",
    "register_role",
    "registered_roles",
]
