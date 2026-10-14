"""模型网关真实 API 连通性冒烟脚本（手动执行，不在测试中运行）。

用法：.venv/Scripts/python.exe scripts/smoke_gateway.py

对角色 A（MiniMax）、B（Qwen）各发一次最小请求验证连通性；
若 output/ 下存在已渲染的页面 PNG，则附带一张测试多模态输入。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from archive_plate_assembler.model_gateway import ModelGateway, ModelGatewayError

TEXT_PROMPT = "以JSON回答：1+1等于几？字段名answer"
VISION_PROMPT = "以JSON回答：这张图里大致有几只贝类/生物个体？字段名count，不确定就填-1"


def find_sample_png() -> str | None:
    """优先用 output/ 下已渲染的页面 PNG。"""
    output_dir = PROJECT_ROOT / "output"
    if output_dir.is_dir():
        for png in sorted(output_dir.rglob("*.png")):
            return str(png)
    return None


def check_role(gateway: ModelGateway, role: str, png: str | None) -> bool:
    ok = True

    try:
        result = gateway.chat_vision(role, TEXT_PROMPT, images=None)
        print(f"[角色 {role}] 纯文本请求 ok: {result}")
    except Exception as exc:
        ok = False
        print(f"[角色 {role}] 纯文本请求失败: {type(exc).__name__}: {exc}")

    if png:
        try:
            result = gateway.chat_vision(role, VISION_PROMPT, images=[png])
            print(f"[角色 {role}] 多模态请求 ok（图片 {png}）: {result}")
        except Exception as exc:
            ok = False
            print(f"[角色 {role}] 多模态请求失败: {type(exc).__name__}: {exc}")
    else:
        print(f"[角色 {role}] 未找到 output/ 下的页面 PNG，跳过多模态测试")

    return ok


def main() -> int:
    png = find_sample_png()
    if png:
        print(f"多模态测试图片: {png}")
    else:
        print("output/ 下无 PNG，将只测试纯文本请求")

    all_ok = True
    with ModelGateway(snapshot_dir=str(PROJECT_ROOT / "output" / "gateway_snapshots")) as gateway:
        for role in ("A", "B"):
            try:
                if not check_role(gateway, role, png):
                    all_ok = False
            except ModelGatewayError as exc:
                all_ok = False
                print(f"[角色 {role}] 配置错误: {exc}")

    print("总体结果:", "全部通过" if all_ok else "存在失败")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
