"""LangHuan → WorkBuddy models.json 同步脚本（零依赖，Python ≥3.12）。

把 LangHuan 运营商注册表（providers.json）中启用的供应商展开为 WorkBuddy 自定义
模型条目，写入 WorkBuddy 的 models.json：

  1. 过滤：enabled && !reserved && type ∈ {openai_compatible,
     openai_compatible_decision, model_deployment}；ssh_tunnel / jev_legacy /
     未知 type 跳过并记日志（语义在 WorkBuddy 无落点，fail-soft）。
  2. 每个 (provider, model) 生成一条目：
       id       = "langhuan-<pid>--<model>"（非法字符折叠为 -）
       name     = "<operator> · <model>"
       vendor   = "Custom"
       url      = base_url 规范化（去尾斜杠；不以 /v1 结尾则补 /v1）
       apiKey   = 该 provider 的 api_key_env 变量在 langhuan.env（回退进程
                  环境）中的值；解析不到 → 记 WARN 并写入空串
       supportsToolCall = true
       supportsImages   = false
       supportsReasoning = openai_compatible_decision → true；模型名含
                  "reason"（如 deepseek-reasoner）→ true；否则 false
       useCustomProtocol = false
  3. 合并写盘：保留所有非 langhuan- 前缀条目，删除旧 langhuan- 条目后追加新
     条目；写前 .bak 备份，临时文件 + os.replace 原子写。
  4. 双路径探测：%USERPROFILE%\\.workbuddy-ai\\models.json（新版，优先）→
     %USERPROFILE%\\.workbuddy\\models.json（旧版）；两处均无现存文件时，
     若对应目录已存在（WorkBuddy 装过但还没自定义模型）则就地新建，否则
     die 提示先装 WorkBuddy。--models 可显式覆盖目标路径（测试/非常规安装）。

⚠ 密钥红线（每次运行开头都会打印）：
   WorkBuddy 不支持环境变量间接引用，apiKey 只能明文落 models.json——这是对
   LangHuan「key 只存中央 langhuan.env」默认红线的显性偏离，属对 WorkBuddy
   能力的适配。请勿分发 models.json。

models.json 逐字段 schema 未获官方公布（社区实证，见
docs/research/workbuddy-plugin-contract.md）。fail-soft 约定：写盘结构按社区实证；
读取对未知字段宽容保留（整条目原样回写），JSON 损坏按空表处理并记 WARN。

用法：
    python scripts/sync_workbuddy_models.py                 # 默认路径（见下）
    python scripts/sync_workbuddy_models.py --dry-run       # 只报告不写盘
    python scripts/sync_workbuddy_models.py --providers <p.json> --env <langhuan.env>
    python scripts/sync_workbuddy_models.py --models <models.json>   # 覆盖目标路径

默认路径：
    providers = ~/.agents/skills/langhuan-lit-scout-harvester/config/providers.json
    env       = ~/.agents/skills/langhuan.env
    models    = 双路径探测（见上），--models 优先

退出码：0 = 完成（WARNING 另计并在结尾汇总）；1 = 致命错误（输入缺失 /
models.json 损坏 / 两处 WorkBuddy 目录都不存在等）。
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

SYNCABLE_TYPES = {"openai_compatible", "openai_compatible_decision",
                  "model_deployment"}
# 显式登记的跳过类（语义在 WorkBuddy 无落点）；其余未知 type 也跳过（fail-soft）
KNOWN_SKIP_TYPES = {"ssh_tunnel", "jev_legacy"}

LANGHUAN_PREFIX = "langhuan-"
WB_DIR_CANDIDATES = (".workbuddy-ai", ".workbuddy")  # 新版优先，旧版兜底

DEFAULT_SKILLS_DIR = Path.home() / ".agents" / "skills"
DEFAULT_PROVIDERS = (DEFAULT_SKILLS_DIR / "langhuan-lit-scout-harvester"
                     / "config" / "providers.json")
DEFAULT_ENV = DEFAULT_SKILLS_DIR / "langhuan.env"

RED_LINE_NOTICE = """\
======================================================================
⚠ 密钥红线偏离提醒
   WorkBuddy 仅支持 key 明文存储（models.json），本条目的 apiKey 将以明文
   写入该文件——这与 LangHuan『key 只存中央 langhuan.env』的默认红线不同，
   属对 WorkBuddy 能力的适配；请勿分发 models.json。
======================================================================"""

_ID_LEGAL = re.compile(r"[^A-Za-z0-9._-]+")


def sanitize_id_component(text: str) -> str:
    """把任意字符串折叠为 models.json id 合法字符集 [A-Za-z0-9._-]。"""
    return _ID_LEGAL.sub("-", text).strip("-") or "x"


def normalize_base_url(url: str) -> str:
    """去尾斜杠；不以 /v1 结尾则补 /v1（WorkBuddy 社区实证要求 url 以 /v1 结尾）。"""
    u = url.strip().rstrip("/")
    if not u.endswith("/v1"):
        u += "/v1"
    return u


def infer_supports_reasoning(provider_type: str, model: str) -> bool:
    """按 type + 模型名推断思维链能力位。"""
    if provider_type == "openai_compatible_decision":
        return True
    return "reason" in model.lower()


def load_env_vars(path: Path) -> dict[str, str]:
    """极简 KEY=VALUE 解析（与 scripts/sync_card_env.py 同语义；不打印值）。"""
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


def resolve_api_key(env_name: str, env_file_vars: dict[str, str]) -> str | None:
    """先查 langhuan.env 文件，回退进程环境（AGENTS.md 的 set -a 导出约定）。

    返回 None 表示两处都解析不到。绝不打印值本身。
    """
    if not env_name:
        return None
    if env_name in env_file_vars and env_file_vars[env_name]:
        return env_file_vars[env_name]
    return os.environ.get(env_name) or None


def field(provider: dict, snake: str, camel: str) -> str:
    """providers.json 文件为 snake_case，plugin-core 视图为 camelCase，两者兼容。"""
    value = provider.get(snake)
    if value is None:
        value = provider.get(camel)
    return value if value is not None else ""


def load_providers(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        providers = data.get("providers")
        if not isinstance(providers, list):
            raise ValueError("providers.json 是 dict 形态但无 providers 列表字段")
        return [p for p in providers if isinstance(p, dict)]
    if isinstance(data, list):
        return [p for p in data if isinstance(p, dict)]
    raise ValueError(f"providers.json 形态无法识别：{type(data).__name__}")


def detect_models_path(home: Path, override: Path | None) -> Path | None:
    """双路径探测（.workbuddy-ai 优先，其次 .workbuddy）。

    override（--models）优先。无现存文件时目录存在则就地新建；否则返回 None
    （调用方 die）。
    """
    if override is not None:
        return override
    for dirname in WB_DIR_CANDIDATES:
        candidate = home / dirname / "models.json"
        if candidate.is_file():
            return candidate
    for dirname in WB_DIR_CANDIDATES:
        directory = home / dirname
        if directory.is_dir():
            return directory / "models.json"  # 已装 WorkBuddy 但尚无自定义模型
    return None


def read_existing_models(path: Path) -> tuple[list, str]:
    """读取现有 models.json。列表或 dict 形态兼容（dict 取 values；"models"
    包裹层也认）；未知字段整条目保留。损坏/缺失 → 空表 + WARN（fail-soft）。
    """
    if not path.exists():
        return [], "missing"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"[WARN] {path} 不是合法 JSON（{exc}）——按空表处理，"
              "原文件内容不会被本脚本改写（仍会生成 .bak 备份）")
        return [], "corrupt"
    if isinstance(data, list):
        return data, "list"
    if isinstance(data, dict):
        inner = data.get("models")
        if isinstance(inner, list):
            return inner, "dict:models-wrapper"
        return list(data.values()), "dict:values"
    print(f"[WARN] {path} 内容形态无法识别（{type(data).__name__}）——按空表处理")
    return [], "unknown"


def is_langhuan_entry(entry: object) -> bool:
    return isinstance(entry, dict) and \
        str(entry.get("id", "")).startswith(LANGHUAN_PREFIX)


def atomic_write(path: Path, text: str) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=path.parent,
                                    prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def build_entries(providers: list[dict], env_file_vars: dict[str, str]) -> list[dict]:
    """过滤 + 展开 (provider, model) → WorkBuddy 条目。跳过类记日志。"""
    entries: list[dict] = []
    for p in providers:
        pid = str(p.get("id") or "?")
        ptype = str(field(p, "type", "type") or "")
        if not p.get("enabled", False):
            print(f"[SKIP] {pid}: enabled=false")
            continue
        if p.get("reserved", False):
            print(f"[SKIP] {pid}: reserved=true（入口保留位，不导出）")
            continue
        if ptype not in SYNCABLE_TYPES:
            reason = (f"type={ptype} 无 WorkBuddy 落点"
                      if ptype in KNOWN_SKIP_TYPES
                      else f"未知 type={ptype}")
            print(f"[SKIP] {pid}: {reason}，fail-soft 跳过")
            continue
        operator = str(field(p, "operator", "operator") or pid)
        base_url = str(field(p, "base_url", "baseUrl") or "")
        env_name = str(field(p, "api_key_env", "apiKeyEnv") or "").strip()
        models = p.get("models") or []
        if not base_url:
            print(f"[WARN] {pid}: base_url 为空，跳过该供应商")
            continue
        if "<" in base_url or ">" in base_url:
            print(f"[WARN] {pid}: base_url 含占位符（{base_url}）"
                  "——请先在 providers.json 填实际地址")
        api_key = resolve_api_key(env_name, env_file_vars)
        if api_key is None:
            print(f"[WARN] {pid}: 环境变量 {env_name or '(未配置 api_key_env)'} "
                  "在 langhuan.env 与进程环境中均未解析到值 → apiKey 写入空串")
        key_state = f"已解析(len={len(api_key)})" if api_key is not None else "空串"
        for model in models:
            model = str(model).strip()
            if not model:
                continue
            entry = {
                "id": f"{LANGHUAN_PREFIX}{sanitize_id_component(pid)}--"
                      f"{sanitize_id_component(model)}",
                "name": f"{operator} · {model}",
                "vendor": "Custom",
                "url": normalize_base_url(base_url),
                "apiKey": api_key if api_key is not None else "",
                "supportsToolCall": True,
                "supportsImages": False,
                "supportsReasoning": infer_supports_reasoning(ptype, model),
                "useCustomProtocol": False,
            }
            entries.append(entry)
            print(f"[ADD ] {entry['id']}  name={entry['name']!r}  "
                  f"url={entry['url']}  apiKey={key_state}  "
                  f"reasoning={entry['supportsReasoning']}")
    return entries


def main(argv: list[str]) -> int:
    # Windows 控制台默认 GBK，强制 UTF-8 输出避免全角/箭头字符炸掉
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass

    print(RED_LINE_NOTICE)
    print()

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--providers", type=Path, default=DEFAULT_PROVIDERS)
    ap.add_argument("--env", type=Path, default=DEFAULT_ENV)
    ap.add_argument("--models", type=Path, default=None,
                    help="显式指定 models.json 目标路径（跳过双路径探测；测试用）")
    ap.add_argument("--dry-run", action="store_true", help="只报告不写盘")
    args = ap.parse_args(argv[1:])

    # ---- 输入校验（致命） ----
    if not args.providers.is_file():
        print(f"[ERROR] providers.json 不存在：{args.providers}")
        return 1
    if not args.env.is_file():
        print(f"[ERROR] 中央 langhuan.env 不存在：{args.env}")
        return 1
    try:
        providers = load_providers(args.providers)
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"[ERROR] providers.json 解析失败：{exc}")
        return 1

    models_path = detect_models_path(Path.home(), args.models)
    if models_path is None:
        print("[ERROR] 未找到 WorkBuddy："
              f"~/.workbuddy-ai/ 与 ~/.workbuddy/ 目录均不存在。"
              "请先安装 WorkBuddy（模型设置页会显示实际路径），再运行本脚本。")
        return 1

    print(f"[INFO] providers: {args.providers}")
    print(f"[INFO] env:       {args.env}")
    print(f"[INFO] 目标文件:  {models_path}"
          f"{'（--models 覆盖）' if args.models else '（双路径探测）'}")
    print()

    # ---- 生成条目 ----
    env_file_vars = load_env_vars(args.env)
    new_entries = build_entries(providers, env_file_vars)
    if not new_entries:
        print("[WARN] 没有可导出的 (provider, model) 组合——"
              "请检查 providers.json 是否有 enabled 且配了 models 的供应商")

    # ---- 合并 ----
    existing, form = read_existing_models(models_path)
    kept = [e for e in existing if not is_langhuan_entry(e)]
    removed = [e for e in existing if is_langhuan_entry(e)]
    merged = kept + new_entries

    print()
    print(f"[MERGE] 现有文件形态: {form}；总条目 {len(existing)} → "
          f"保留(非 langhuan-) {len(kept)}，删除(旧 langhuan-) {len(removed)}，"
          f"新增 {len(new_entries)}，合并后 {len(merged)}")
    for e in removed:
        print(f"[DROP] {e.get('id', '?')}（旧 langhuan- 条目，由新同步替换）")

    if args.dry_run:
        print()
        print("[DRY-RUN] 未写盘。去掉 --dry-run 后执行："
              f"备份 {models_path}.bak → 原子写入上述 {len(merged)} 条。")
        return 0

    # ---- 写盘：.bak + 原子写 ----
    models_path.parent.mkdir(parents=True, exist_ok=True)
    if models_path.exists():
        shutil.copy2(models_path, str(models_path) + ".bak")
        print(f"[BACKUP] {models_path}.bak")
    text = json.dumps(merged, ensure_ascii=False, indent=2) + "\n"
    atomic_write(models_path, text)
    print(f"[WRITE] {models_path}（{len(merged)} 条，原子写）")
    print()
    print("下一步：完全退出 WorkBuddy（托盘图标右键退出，仅关窗口不生效），"
          "重新启动后在模型选择器选用 langhuan-* 条目。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
