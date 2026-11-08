"""设置卡片 ↔ 中央 langhuan.env 双向同步/核对器。

背景：DSH 设置卡片（经 dsh-plugin 落盘 providers.json）是运营商入口（base_url、
api_key_env 变量名等）的单一事实来源；key 本体只存中央 langhuan.env（卡片红线：
Config 只存变量名永不存 key 值）。本脚本保证两者衔接一致：

正向（卡片 → env）：providers.json 里每家运营商的 api_key_env 变量名，在中央
  langhuan.env 中必有槽位——缺失的追加 `VAR=` 空行（只补名不填值，值仍由人填）。
反向（env → 卡片）：langhuan.env 中已填值的 *API_KEY/*TOKEN 变量若未被任何
  运营商引用，报 WARN（可能填了 key 但卡片没配对应入口）；BASE_URL 类变量的
  值永不进卡片，属设计使然，不检查。

幂等：重复运行只补缺失、只报异常。不写 providers.json（卡片→providers.json
由 dsh-plugin boot/volatile-update 负责，见 dsh-plugin/lib/index.js）。

用法：
    python scripts/sync_card_env.py            # 默认路径（见下）
    python scripts/sync_card_env.py --providers <providers.json> --env <langhuan.env>

默认路径：
    providers = ~/.agents/skills/langhuan-lit-scout-harvester/config/providers.json
    env       = ~/.agents/skills/langhuan.env
退出码：0 = 一致（含本次补齐后一致）；1 = 存在 WARN/ERROR。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

DEFAULT_SKILLS = Path.home() / ".agents" / "skills"
DEFAULT_PROVIDERS = (DEFAULT_SKILLS / "langhuan-lit-scout-harvester"
                     / "config" / "providers.json")
DEFAULT_ENV = DEFAULT_SKILLS / "langhuan.env"

KEY_VALUE_SUFFIXES = ("_API_KEY", "_TOKEN", "_SECRET")


def load_env_vars(path: Path) -> dict[str, str]:
    """极简 KEY=VALUE 解析（与技能侧 load_env 同语义；不打印值）。"""
    vars_: dict[str, str] = {}
    if not path.exists():
        return vars_
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        vars_[key.strip()] = value.strip().strip('"').strip("'")
    return vars_


def providers_env_names(providers_path: Path) -> list[tuple[str, str]]:
    """返回 [(provider_id, api_key_env), ...]。"""
    data = json.loads(providers_path.read_text(encoding="utf-8"))
    result = []
    for p in data.get("providers", []):
        env_name = (p.get("api_key_env") or "").strip()
        if env_name:
            result.append((p.get("id", "?"), env_name))
    return result


def append_empty_slots(env_path: Path, names: list[str]) -> list[str]:
    """在 langhuan.env 末尾补缺失的空槽位（VAR=，按现有段落风格带一条注释）。"""
    existing = set(load_env_vars(env_path))
    missing = [n for n in names if n not in existing]
    if not missing:
        return []
    with env_path.open("a", encoding="utf-8") as f:
        f.write("\n# ---- 设置卡片同步补槽（值由人工填写；见 scripts/sync_card_env.py）----\n")
        for n in missing:
            f.write(f"{n}=\n")
    return missing


def main(argv: list[str]) -> int:
    # Windows 控制台默认 GBK，强制 UTF-8 输出避免箭头/全角字符炸掉
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--providers", type=Path, default=DEFAULT_PROVIDERS)
    ap.add_argument("--env", type=Path, default=DEFAULT_ENV)
    ap.add_argument("--dry-run", action="store_true",
                    help="只报告不补槽")
    args = ap.parse_args(argv[1:])

    problems = 0
    if not args.providers.exists():
        print(f"[ERROR] providers.json 不存在：{args.providers}"
              "（DSH 插件尚未完成 boot 同步？）")
        return 1
    if not args.env.exists():
        print(f"[ERROR] 中央 langhuan.env 不存在：{args.env}")
        return 1

    refs = providers_env_names(args.providers)
    if not refs:
        print("[WARN] providers.json 没有任何 api_key_env 引用")
        problems += 1
    env_vars = load_env_vars(args.env)

    # 正向：卡片 → env 补槽
    names = sorted({n for _, n in refs})
    missing = [n for n in names if n not in env_vars]
    if missing:
        if args.dry_run:
            print(f"[正向-待补] {len(missing)} 个变量槽位缺失（dry-run 未写入）：")
            for n in missing:
                print(f"  + {n}=")
        else:
            added = append_empty_slots(args.env, missing)
            print(f"[正向-OK] 已补 {len(added)} 个空槽位：{', '.join(added)}"
                  "（值由人工填写后生效）")
    else:
        print(f"[正向-OK] 卡片引用的 {len(names)} 个 key 变量在 langhuan.env 均有槽位")

    # 反向：env 已填值但无卡片引用 → WARN
    referenced = set(names)
    orphans = sorted(k for k, v in env_vars.items()
                     if v and k.endswith(KEY_VALUE_SUFFIXES) and k not in referenced)
    if orphans:
        problems += 1
        print(f"[反向-WARN] {len(orphans)} 个已填值的 key 变量未被卡片引用：")
        for k in orphans:
            print(f"  ? {k}（len={len(env_vars[k])}）：填了 key 但卡片无对应运营商，"
                  "检查变量名拼写或在卡片中配置对应入口")
    else:
        print("[反向-OK] 无孤儿 key 变量")

    # 汇总空槽提醒（含原本就空的）
    empty_filled_check = [n for n in names if not env_vars.get(n)]
    if empty_filled_check:
        print(f"[INFO] {len(empty_filled_check)} 个槽位仍为空待填："
              f"{', '.join(empty_filled_check)}")

    if problems:
        print(f"\n结果：{problems} 处需关注（退出码 1）")
        return 1
    print("\n结果：卡片 ↔ langhuan.env 一致")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
