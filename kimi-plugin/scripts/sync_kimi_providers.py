#!/usr/bin/env python3
"""把 LangHuan providers.json 同步为 Kimi Code config.toml 的 providers/models 标记块。

红线：本脚本只读取 api_key_env 环境变量**名**，永不读取、打印、写落 key 值。

语义：
- 源：providers.json（LangHuan 中央运营商注册表）。
- 目标：$KIMI_CODE_HOME/config.toml（缺省 ~/.kimi-code/config.toml）。
- 只同步 enabled && !reserved 且 type ∈ {openai_compatible,
  openai_compatible_decision, model_deployment} 的条目；ssh_tunnel 与其他
  未知类型跳过并记日志。
- 在 config.toml 管理 # >>> langhuan-providers … # <<< langhuan-providers
  标记块（整块替换）；块外内容原样保留；重复运行幂等。
- 写前 .bak 备份，原子写（tmp + replace）；--dry-run 只打印将要写入的块。

退出码：0 成功（含无变化/干跑），1 失败。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

START_MARKER = "# >>> langhuan-providers"
END_MARKER = "# <<< langhuan-providers"

DEFAULT_PROVIDERS = (
    "~/.agents/skills/langhuan-lit-scout-harvester/config/providers.json"
)
SYNCABLE_TYPES = {
    "openai_compatible",
    "openai_compatible_decision",
    "model_deployment",
}


def log(msg: str) -> None:
    print(msg, file=sys.stderr)


def expand_home(p: str) -> Path:
    return Path(os.path.expandvars(p)).expanduser()


def toml_basic_string(s: str) -> str:
    r"""TOML basic string 转义（JSON 转义是兼容子集：\" \\ \uXXXX 等）。"""
    return json.dumps(str(s), ensure_ascii=False)


def sanitize_comment(s: str) -> str:
    return " ".join(str(s).splitlines()).strip()


def select_providers(data: dict) -> tuple[list[dict], list[str]]:
    """按同步语义过滤条目，返回 (待同步, 跳过原因日志)。"""
    selected, skips = [], []
    providers = data.get("providers")
    if not isinstance(providers, list):
        raise ValueError("providers.json 缺少 providers 数组")
    for p in providers:
        if not isinstance(p, dict) or not p.get("id"):
            skips.append("跳过一条无 id 的畸形条目")
            continue
        pid, ptype = str(p["id"]), str(p.get("type", ""))
        if p.get("reserved"):
            skips.append(f"跳过 {pid}: reserved（预留入口）")
            continue
        if not p.get("enabled"):
            skips.append(f"跳过 {pid}: 未启用")
            continue
        if ptype == "ssh_tunnel":
            skips.append(f"跳过 {pid}: ssh_tunnel（隧道由 LangHuan model_tunnel.py 管理）")
            continue
        if ptype not in SYNCABLE_TYPES:
            skips.append(f"跳过 {pid}: 未知类型 {ptype!r}")
            continue
        if not str(p.get("base_url", "")).strip():
            skips.append(f"跳过 {pid}: base_url 为空")
            continue
        if not str(p.get("api_key_env", "")).strip():
            skips.append(f"跳过 {pid}: api_key_env 为空（无法声明凭证来源）")
            continue
        selected.append(p)
    return selected, skips


def build_block(selected: list[dict]) -> str:
    """生成标记块文本（首尾为 marker 行，块内自带尾随换行）。"""
    lines: list[str] = [
        START_MARKER,
        "# 本块由 langhuan-provider-card 插件（scripts/sync_kimi_providers.py）管理，",
        "# 请勿手工编辑；请改 providers.json 后重跑 set-provider 同步。",
    ]
    for p in selected:
        pid = str(p["id"])
        lines.append("")
        operator = str(p.get("operator", "")).strip()
        header = f"{operator}（{pid}）" if operator else pid
        lines.append(f"# {header}")
        note = sanitize_comment(p.get("note", ""))
        if note:
            lines.append(f"# 备注: {note}")
        lines.append(f'[providers."langhuan-{pid}"]')
        lines.append('type = "openai"')
        lines.append(f"base_url = {toml_basic_string(p['base_url'])}")
        lines.append(f"api_key_env = {toml_basic_string(p['api_key_env'])}")
        for m in p.get("models", []):
            m = str(m)
            lines.append("")
            lines.append(f'[models."langhuan-{pid}--{m}"]')
            lines.append(f'provider = "langhuan-{pid}"')
            lines.append(f"model = {toml_basic_string(m)}")
    lines.append(END_MARKER)
    return "\n".join(lines) + "\n"


def replace_block(text: str, block: str) -> tuple[str, bool]:
    """把 text 中的标记块替换为 block；无块则追加。返回 (新文本, 是否本就有块)。"""
    lines = text.split("\n")
    start_idx = [i for i, ln in enumerate(lines) if ln.strip() == START_MARKER]
    end_idx = [i for i, ln in enumerate(lines) if ln.strip() == END_MARKER]
    if len(start_idx) != len(end_idx) or (start_idx and start_idx[0] > end_idx[0]):
        raise ValueError("config.toml 标记块不成对/顺序错乱，请手工修复或删除残留 marker 行后重试")
    if not start_idx:
        body = text.rstrip("\n")
        new = (body + "\n\n" if body else "") + block
        return new, False
    # 替换第一处；多余残留块（成对出现）整段删除并记日志。
    s, e = start_idx[0], end_idx[0]
    new_lines = lines[:s] + block.rstrip("\n").split("\n") + lines[e + 1 :]
    if len(start_idx) > 1:
        log(f"警告: 发现 {len(start_idx)} 处标记块，保留第一处，其余残留块已移除")
        # 简单起见：反复从头扫描删除后续成对块。
        while True:
            cur = "\n".join(new_lines)
            ss = [i for i, ln in enumerate(new_lines) if ln.strip() == START_MARKER]
            ee = [i for i, ln in enumerate(new_lines) if ln.strip() == END_MARKER]
            if len(ss) <= 1 or len(ss) != len(ee) or ss[1] > ee[1]:
                break
            new_lines = new_lines[: ss[1]] + new_lines[ee[1] + 1 :]
        cur = "\n".join(new_lines)
        return cur.rstrip("\n") + "\n", True
    return "\n".join(new_lines).rstrip("\n") + "\n", True


def main() -> int:
    # Windows 控制台默认 GBK，强制 UTF-8 输出避免全角字符炸掉（同 sync_card_env.py）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--providers", default=DEFAULT_PROVIDERS,
                    help="providers.json 路径（默认 %(default)s）")
    ap.add_argument("--kimi-home", default=None,
                    help="Kimi Code 数据根（默认 $KIMI_CODE_HOME 或 ~/.kimi-code）")
    ap.add_argument("--dry-run", action="store_true", help="只打印将要写入的块，不动文件")
    args = ap.parse_args()

    src = expand_home(args.providers)
    home = expand_home(args.kimi_home) if args.kimi_home else expand_home(
        os.environ.get("KIMI_CODE_HOME", "~/.kimi-code"))
    target = home / "config.toml"

    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except Exception as exc:  # 缺文件/坏 JSON 统一报失败
        log(f"错误: 读取 {src} 失败: {exc}")
        return 1

    try:
        selected, skips = select_providers(data)
    except ValueError as exc:
        log(f"错误: {exc}")
        return 1
    for s in skips:
        log(f"提示: {s}")

    block = build_block(selected)
    existed = target.exists()
    old_text = target.read_text(encoding="utf-8") if existed else ""

    try:
        new_text, had_block = replace_block(old_text, block)
    except ValueError as exc:
        log(f"错误: {exc}")
        return 1

    if args.dry_run:
        print(f"# 干跑模式：以下块将写入 {target}（{'替换已有块' if had_block else '追加新块'}）\n")
        print(block, end="")
        print(f"\n# 摘要: 同步 {len(selected)} 家，跳过 {len(skips)} 条；"
              f"内容{'无变化' if new_text == old_text else '有变化'}。")
        return 0

    if new_text == old_text:
        print(f"已是最新，跳过写入: {target}")
        return 0

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if existed:
            shutil.copy2(target, target.with_name(target.name + ".bak"))
        fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".config-", suffix=".toml")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)
        os.replace(tmp, target)
    except Exception as exc:
        log(f"错误: 写入 {target} 失败: {exc}")
        return 1

    print(f"已同步 {len(selected)} 家运营商 → {target}"
          f"（备份 {target.name}.bak，{'替换标记块' if had_block else '新增标记块'}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
