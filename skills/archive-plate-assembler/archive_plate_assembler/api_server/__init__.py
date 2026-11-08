"""API 层（M5）：HTTP 封装六节点管道。见 app.py 模块 docstring。"""

from .app import create_app

__all__ = ["create_app"]
