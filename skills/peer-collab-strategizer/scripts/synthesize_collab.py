"""Phase 4 SWOT × 合作模式草稿合成脚本（PCS · DeepSeek API 执行）。

将 peer-collab-strategizer/prompts/synthesize-collab.md 作为系统提示词，
三份上游报告全文作为用户输入，调用 DeepSeek 模型产出 SWOT × 合作模式
第一轮草稿，写入 --output。

安全约束：仅从本技能根 .env 读取凭据，绝不打印密钥。

全部业务变量（我方团队/目标团队、技术方向、GAP 编号接续、上游报告路径、
模型与额度）均为命令行参数，无任何案例默认值。

用法：
    PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe scripts/synthesize_collab.py \
        --self-team "我方团队（含团队边界说明）" --peer-team "目标团队（含 PI 消歧）" \
        --tech-directions "方向A" \
        --gap-start "GAP-28（本阶段新缺口自 GAP-29 起编）" \
        --phase1 output/phase1.md --phase2 output/phase2.md --phase3 output/phase3.md \
        --output output/phase4-collab-draft.md
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))

# 定位 agentkit：zip 分发时 vendor 在技能根，import 直接命中；
# monorepo 开发布局在套件根 libs/，失败时插入后重试。
try:
    import agentkit  # noqa: F401
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "libs"))
    import agentkit  # noqa: F401,E402

from test_orcid import load_env  # noqa: E402

PROMPT_FILE = ROOT / "peer-collab-strategizer" / "prompts" / "synthesize-collab.md"

DEFAULT_MODEL = "deepseek-v4-pro"
DEFAULT_MAX_TOKENS = 16384  # v4 为推理模型，额度含 reasoning；草稿较长需充足余量


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PCS Phase 4 SWOT × 合作模式草稿合成（DeepSeek API）")
    parser.add_argument("--self-team", required=True,
                        help="我方团队名称（含团队边界/PI 说明）")
    parser.add_argument("--peer-team", required=True,
                        help="目标团队名称（含 PI 消歧与机构边界说明）")
    parser.add_argument("--tech-directions", required=True, nargs="+",
                        help="技术方向（可多个，如：遥感生态监测 碳通量反演）")
    parser.add_argument("--gap-start", required=True,
                        help="上游 GAP/VT/SD 最大编号与本阶段编号接续说明")
    parser.add_argument("--phase1", required=True, type=Path, help="Phase 1 本团队画像报告路径")
    parser.add_argument("--phase2", required=True, type=Path, help="Phase 2 目标团队画像报告路径")
    parser.add_argument("--phase3", required=True, type=Path, help="Phase 3 学术生态位扫描报告路径")
    parser.add_argument("--output", type=Path, default=Path("output/phase4-collab-draft.md"),
                        help="草稿输出路径（默认 output/phase4-collab-draft.md）")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="DeepSeek 模型名")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                        help="max_tokens 上限（v4 为推理模型，额度含 reasoning）")
    return parser


def run(args: argparse.Namespace) -> dict:
    """执行合成，返回统计 dict；失败抛异常（缺文件/缺凭据/API 失败/空响应）。"""
    reports = {
        "Phase1_本团队画像报告全文": args.phase1,
        "Phase2_目标团队画像报告全文": args.phase2,
        "Phase3_学术生态位扫描报告全文": args.phase3,
    }
    for name, p in reports.items():
        if not p.exists():
            raise FileNotFoundError(f"找不到 {name}：{p}")

    variables = {
        "我方团队名称": args.self_team,
        "目标团队名称": args.peer_team,
        "技术方向": "、".join(args.tech_directions),
        "上游GAP最大编号": args.gap_start,
    }

    env = load_env(ROOT / ".env")
    api_key = env.get("DEEPSEEK_API_KEY", "")
    base_url = env.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    if not api_key:
        raise RuntimeError(".env 中缺少 DEEPSEEK_API_KEY")
    print(f"[OK] 凭据已加载（密钥长度 {len(api_key)}，不显示）")

    system_prompt = PROMPT_FILE.read_text(encoding="utf-8")
    parts = ["## 输入变量赋值\n"]
    for k, v in variables.items():
        parts.append(f"- {{{k}}} = {v}")
    parts.append("")
    for k, path in reports.items():
        text = path.read_text(encoding="utf-8")
        parts.append(f"\n## {{{k}}}\n\n<text name=\"{k}\">\n{text}\n</text>\n")
    user_message = "\n".join(parts)
    print(f"[OK] 输入规模：system {len(system_prompt)} 字符，user {len(user_message)} 字符")

    from agentkit.llm import get_provider
    from agentkit.llm.sync import chat_sync

    provider = get_provider("deepseek", model=args.model, api_key=api_key,
                            base_url=base_url, timeout=1800)
    print(f"[..] 调用 {args.model} 合成中（max_tokens={args.max_tokens}）...")
    t0 = time.time()
    content = chat_sync(
        provider,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
        max_tokens=args.max_tokens,
        temperature=0,
    )
    elapsed = time.time() - t0

    usage = provider.last_usage
    print(f"[OK] 耗时 {elapsed:.0f}s")
    if usage:
        print(f"[OK] token：prompt={usage['prompt_tokens']} completion={usage['completion_tokens']} "
              f"total={usage['total_tokens']}")
    if not content.strip():
        raise RuntimeError("API 返回内容为空（可能被 reasoning 耗尽额度）")

    out_file = args.output
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(content, encoding="utf-8")
    print(f"[OK] 草稿已写入 {out_file}（{len(content)} 字符）")

    return {
        "model": args.model,
        "output": str(out_file),
        "chars": len(content),
        "prompt_tokens": usage["prompt_tokens"] if usage else None,
        "completion_tokens": usage["completion_tokens"] if usage else None,
        "total_tokens": usage["total_tokens"] if usage else None,
        "api_elapsed_s": round(elapsed, 1),
    }


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run(args)
    except Exception as exc:
        print(f"[FAIL] {type(exc).__name__}: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
