"""MockGateway：与 ModelGateway 同接口的测试替身，不发起任何网络请求。"""

from __future__ import annotations

from typing import Callable, Union

FixtureSource = Union[dict[str, dict], Callable[[str], dict]]


class MockGateway:
    """按 prompt 返回预置 fixture 的假网关。

    fixtures 两种形式：
    - dict[str, dict]：key 为 prompt 的子串，第一个匹配中的 key 对应的 value 作为返回；
    - callable：接收完整 prompt，返回 dict。

    无匹配时抛 KeyError。所有调用记录在 calls 列表中（role/prompt/image_count），
    供测试断言。
    """

    def __init__(self, fixtures: FixtureSource):
        self.fixtures = fixtures
        self.calls: list[dict] = []

    def chat_vision(
        self,
        role: str,
        prompt: str,
        images: list[str] | None = None,
        json_mode: bool = True,
        system: str | None = None,
        temperature: float = 0.1,
    ) -> dict:
        self.calls.append({
            "role": role,
            "prompt": prompt,
            "image_count": len(images or []),
        })
        if callable(self.fixtures):
            return self.fixtures(prompt)
        for key, value in self.fixtures.items():
            if key in prompt:
                return value
        raise KeyError(f"MockGateway 无匹配 fixture，prompt: {prompt[:200]!r}")
