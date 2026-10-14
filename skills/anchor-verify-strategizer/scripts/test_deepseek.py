"""DeepSeek API 连通性验证脚本（AVS 环境检查用）。

安全约束：
- 仅从项目根目录 .env 读取凭据，绝不打印密钥明文（只输出是否加载及长度）。
- 发送一个最小化 ping 请求，打印模型、回复与 token 用量。

用法：
    .venv/Scripts/python.exe scripts/test_deepseek.py
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = ROOT / ".env"

# 定位 agentkit：zip 分发时 vendor 在技能根，monorepo 开发布局在套件根 libs/。
for _candidate in (Path(__file__).resolve().parents[3] / "libs", ROOT):
    if (_candidate / "agentkit").is_dir() and str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

import agentkit  # noqa: F401,E402


def load_env(path: Path) -> dict:
    """极简 .env 解析（KEY=VALUE，忽略注释与空行），避免额外依赖。"""
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main() -> int:
    if not ENV_FILE.exists():
        print(f"[FAIL] 未找到 {ENV_FILE}")
        return 1

    env = load_env(ENV_FILE)
    api_key = env.get("DEEPSEEK_API_KEY", "")
    base_url = env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    if not api_key:
        print("[FAIL] .env 中缺少 DEEPSEEK_API_KEY")
        return 1
    # 只报告密钥存在性与长度，绝不输出内容
    print(f"[OK] 已加载 DEEPSEEK_API_KEY（长度 {len(api_key)}，内容不予显示）")
    print(f"[OK] DEEPSEEK_BASE_URL = {base_url}")

    from agentkit.llm import get_provider
    from agentkit.llm.sync import chat_sync

    provider = get_provider("deepseek", model="deepseek-v4-flash",
                            api_key=api_key, base_url=base_url)
    try:
        content = chat_sync(
            provider,
            [
                {"role": "system", "content": "你是 Anchor-Verify-Strategizer 环境自检助手。"},
                {"role": "user", "content": "请只回复：AVS-PING-OK"},
            ],
            max_tokens=64,  # v4 为推理模型，需给 reasoning 预留 token
            temperature=0,
        ).strip()
    except Exception as exc:  # 网络/鉴权/配额等异常统一在此报告
        print(f"[FAIL] API 调用失败：{type(exc).__name__}: {exc}")
        return 1

    print(f"[OK] 模型：{provider.model}")
    print(f"[OK] 回复：{content}")
    if provider.last_usage:
        usage = provider.last_usage
        print(f"[OK] token 用量：prompt={usage['prompt_tokens']} "
              f"completion={usage['completion_tokens']} total={usage['total_tokens']}")

    if "AVS-PING-OK" in content:
        print("[PASS] DeepSeek API 连通性验证通过")
        return 0
    print("[WARN] 已连通，但回复内容不符合预期（不影响连通性结论）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
