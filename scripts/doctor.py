"""LangHuan 环境体检（doctor）：运行前置 + 凭证卫生一键检查。

检查组（对应 BACKLOG P2 登记项）：
  1. python    解释器版本 >= 3.14（套件统一基线）
  2. venv      各技能 .venv 存在且可执行（缺失给重建提示，不判失败——技能
               可经 agent_bridge 零 key 运行）
  3. creds     凭证文件存在性 / 权限（0600 硬约束）/ 已填 key 槽位
               —— 只显示末 4 位指纹（agentkit.credentials.fingerprint），
               绝不打印明文
  4. settings  settings 文件原始密钥黑名单扫描（复用 check_dist_secrets 的
               KEY_RE 口径）：Config 只存 api_key_env 变量名，检出疑似明文
               密钥即 FAIL
  5. hosts     各宿主插件目录可见性提示（仅存在性：dsh/kimi/workbuddy/trae）
  6. origins   技能形态/宿主插件形态同名技能检测（只读，不自动停用）
  7. platforms OpenDataLab 体系平台可达性/凭据（MinerU flash 端点、Sciverse
               key 指纹与 /meta-catalog 探测、Dianshi MCP SSO token 与 tools/list
               探测、magic-pdf 本地档可用性）及 MinerU 当日配额看板——
               不可达报 DEGRADED 不判 FAIL（fail-soft 语义）

退出码：0 = 无 FAIL（WARN/INFO 不阻断）；1 = 存在 FAIL。
凭证文件位置可用 LANGHUAN_SKILLS_DIR 覆盖（默认 ~/.agents/skills）；重复技能诊断
可用 LANGHUAN_WORKBUDDY_HOME / LANGHUAN_TRAE_HOME 指定宿主配置目录。
用法：python3 scripts/doctor.py [--offline] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "scripts"))

from agentkit.credentials import fingerprint  # noqa: E402
from check_dist_secrets import KEY_RE, PLACEHOLDER_MARKERS  # noqa: E402

SKILLS_DIR = Path(os.environ.get("LANGHUAN_SKILLS_DIR", Path.home() / ".agents" / "skills"))
SUITE_ENV = "langhuan.env"

_findings: list[tuple[str, str, str]] = []  # (level, group, msg)
_json_output = False


def emit(level: str, group: str, msg: str) -> None:
    _findings.append((level, group, msg))
    if not _json_output:
        print(f"[{level:<4}] {group:<8} {msg}")


def check_python() -> None:
    v = sys.version_info
    if (v.major, v.minor) >= (3, 14):
        emit("OK", "python", f"{v.major}.{v.minor}.{v.micro} >= 3.14 基线")
    else:
        emit("FAIL", "python", f"{v.major}.{v.minor}.{v.micro} < 3.14（套件统一基线）")


def check_venvs() -> None:
    for skill_dir in sorted((ROOT / "skills").iterdir()):
        if not skill_dir.is_dir():
            continue
        # 开发基线同时覆盖 Windows 与 POSIX；只查 bin/python 会把可用的
        # Windows venv 误报为缺失。
        candidates = [skill_dir / ".venv" / "bin" / "python"]
        if os.name == "nt":
            candidates.insert(0, skill_dir / ".venv" / "Scripts" / "python.exe")
        py = next((path for path in candidates if path.is_file()), candidates[0])
        if not py.is_file():
            emit("WARN", "venv", f"{skill_dir.name}: 无 .venv（重建: python3.14 -m venv "
                                 f"skills/{skill_dir.name}/.venv）")
            continue
        try:
            out = subprocess.run([str(py), "--version"], capture_output=True, text=True, timeout=15)
            if out.returncode == 0:
                emit("OK", "venv", f"{skill_dir.name}: {out.stdout.strip()}")
            else:
                emit("WARN", "venv", f"{skill_dir.name}: .venv 解释器不可运行（疑 Windows 残留，删除重建）")
        except OSError:
            emit("WARN", "venv", f"{skill_dir.name}: .venv 解释器不可执行（疑 Windows 残留，删除重建）")


def _iter_env_slots(path: Path) -> dict[str, str]:
    """解析 KEY=VALUE 行（跳过注释/空行）；返回非空槽位（调用方负责脱敏展示）。"""
    slots: dict[str, str] = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            if value:
                slots[key.strip()] = value
    except OSError:
        pass
    return slots


def check_creds() -> dict[str, str]:
    """凭证文件体检；返回 {变量名: 值} 供 settings 组交叉核对（全程不打印值）。"""
    candidates = [SKILLS_DIR / SUITE_ENV, ROOT / SUITE_ENV]
    candidates += sorted((ROOT / "skills").glob("*/.env"))
    candidates += sorted(SKILLS_DIR.glob("langhuan-*/.env"))

    merged: dict[str, str] = {}
    seen: set[Path] = set()
    for path in candidates:
        if not path.exists():
            continue
        try:
            real = path.resolve()
        except OSError:
            real = path
        if real in seen:  # 硬链接去重（中央 env 与各技能 .env 同 inode）
            continue
        seen.add(real)
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode != 0o600:
            emit("WARN", "creds", f"{path}: 权限 {mode:o} ≠ 0600（建议 chmod 600）")
        else:
            emit("OK", "creds", f"{path}: 权限 0600")
        slots = _iter_env_slots(path)
        if not slots:
            emit("INFO", "creds", f"{path}: 全部槽位为空（可经 agent_bridge 零 key 运行）")
        for name, value in slots.items():
            if re.fullmatch(r"[A-Z0-9_]*(KEY|TOKEN|SECRET)", name):
                merged.setdefault(name, value)
                emit("OK", "creds", f"{path.name} 槽位 {name} = {fingerprint(value)}")
    if not seen:
        emit("INFO", "creds", f"未发现任何凭证文件（中央 {SUITE_ENV} 与各技能 .env 均不存在；"
                              f"install 后位于 {SKILLS_DIR / SUITE_ENV}）")
    return merged


def check_settings(env_slots: dict[str, str]) -> None:
    targets = [
        ROOT / "skills/lit-scout-harvester/config/providers.json",
        SKILLS_DIR / "langhuan-lit-scout-harvester/config/providers.json",
    ]
    scanned = False
    for path in targets:
        if not path.exists():
            continue
        scanned = True
        data = path.read_bytes()
        hits = [m.group(0) for m in KEY_RE.finditer(data)
                if not any(marker in m.group(0) for marker in PLACEHOLDER_MARKERS)]
        if hits:
            emit("FAIL", "settings", f"{path} 含疑似明文密钥 {hits[0][:12]}...（红线：Config 只存变量名）")
        else:
            emit("OK", "settings", f"{path}: 无原始密钥（黑名单扫描通过）")
        try:
            doc = json.loads(data)
        except json.JSONDecodeError as exc:
            emit("FAIL", "settings", f"{path}: JSON 非法（{exc}）")
            continue
        names = [p.get("api_key_env") for p in doc.get("providers", []) if p.get("api_key_env")]
        unresolved = [n for n in names if not os.environ.get(n) and n not in env_slots]
        if unresolved:
            head = ", ".join(unresolved[:3]) + ("…" if len(unresolved) > 3 else "")
            emit("WARN", "settings", f"{path.name}: {len(unresolved)}/{len(names)} 个 api_key_env "
                                     f"未解析（{head}）——不影响 agent_bridge 零 key 模式")
        else:
            emit("OK", "settings", f"{path.name}: {len(names)} 个 api_key_env 槽位全部可解析")
    if not scanned:
        emit("INFO", "settings", "未找到 providers.json（install 或 DSH 插件 boot 同步后生成）")


HOST_HINTS = [
    ("dsh", Path.home() / ".dsh", "DSH CLI/Desktop profile"),
    ("kimi", Path.home() / ".kimi-code", "Kimi Code CLI（config.toml / plugins）"),
    ("workbuddy", Path.home() / ".workbuddy", "WorkBuddy 配置目录"),
    ("trae", Path.home() / ".trae", "TRAE 配置目录"),
]


def _probe(url: str, headers: Optional[dict] = None, timeout: float = 5.0) -> Optional[int]:
    """GET 探测：返回 HTTP 状态码（含 4xx/5xx——服务可达即返回），网络失败 None。"""
    import urllib.error
    import urllib.request

    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (OSError, ValueError):
        return None


MINERU_QUOTA_WARN_RATIO = 0.8


def check_mineru_quota(
    paths: Optional[list[Path]] = None,
    warn_ratio: float = MINERU_QUOTA_WARN_RATIO,
) -> None:
    """展示 APA 各 job 的 UTC 当日 MinerU 用量，并预警 token 日页数额度。"""
    from agentkit.mineru import TOKEN_DAILY_PAGE_LIMIT, load_usage_today

    if not 0.0 < warn_ratio <= 1.0:
        raise ValueError("warn_ratio must be in (0, 1]")
    if paths is None:
        override = os.environ.get("LANGHUAN_MINERU_QUOTA", "").strip()
        if override:
            paths = [Path(override)]
        else:
            paths = []
            output_roots = [
                ROOT / "output",
                ROOT / "skills/archive-plate-assembler/output",
                SKILLS_DIR / "langhuan-archive-plate-assembler/output",
            ]
            for output_root in output_roots:
                paths.extend(output_root.glob("*/quota_state/mineru_quota.json"))
                paths.extend(output_root.glob("quota_state/mineru_quota.json"))
    if not paths:
        emit("INFO", "platforms", "未发现 MinerU 配额账本（APA 运行后位于 job_dir/quota_state/）")
        return

    for raw_path in sorted(set(paths), key=str):
        path = Path(raw_path)
        if path.name != "mineru_quota.json":
            if (path / "quota_state" / "mineru_quota.json").is_file():
                path = path / "quota_state" / "mineru_quota.json"
            elif path.is_dir():
                path = path / "mineru_quota.json"
        if not path.is_file():
            emit("WARN", "platforms", f"MinerU 配额账本不存在：{path}")
            continue
        usage = load_usage_today(path.parent)
        parts = []
        for tier in ("flash", "token", "local"):
            tasks = usage.get(f"{tier}_tasks", 0)
            pages = usage.get(f"{tier}_pages")
            if tasks or pages:
                parts.append(f"{tier} {tasks} 任务/{pages if pages is not None else '?'} 页")
        if not parts:
            emit("INFO", "platforms", f"{path.parent}: UTC 当日无 MinerU 用量")
            continue
        token_pages = usage.get("token_pages")
        threshold = TOKEN_DAILY_PAGE_LIMIT * warn_ratio
        if token_pages is not None and token_pages >= TOKEN_DAILY_PAGE_LIMIT:
            emit("WARN", "platforms", f"{path.parent}: {'; '.join(parts)}；token 日页数已达/超过 "
                                      f"{TOKEN_DAILY_PAGE_LIMIT} 页上限，请改用 local 或次日再跑")
        elif token_pages is not None and token_pages >= threshold:
            emit("WARN", "platforms", f"{path.parent}: {'; '.join(parts)}；token 日页数超过 "
                                      f"{warn_ratio:.0%} 预警线（{token_pages}/{TOKEN_DAILY_PAGE_LIMIT}）")
        else:
            suffix = "" if token_pages is not None or not usage.get("token_tasks") else "；token 页数未回传"
            emit("OK", "platforms", f"{path.parent}: {'; '.join(parts)}{suffix}")


def check_platforms() -> None:
    """OpenDataLab 体系平台体检（2026-11-12 双平台对接）：只报状态，不判 FAIL。"""
    import importlib.util

    from agentkit.sciverse import resolve_token as sciverse_token

    # MinerU：flash 免登录端点可达性 + 本地引擎档可用性
    status = _probe("https://mineru.net")
    if status is None:
        emit("WARN", "platforms", "mineru.net 不可达：APA 解码器将回落 local 档（零行为变化）")
    else:
        emit("OK", "platforms", f"mineru.net 可达（HTTP {status}）：flash 免登录档可用")
    if importlib.util.find_spec("magic_pdf") is not None:
        emit("OK", "platforms", "magic-pdf 可导入：MinerU 本地引擎档可用（零网络兜底）")
    else:
        emit("INFO", "platforms", "magic-pdf 未安装：MinerU local 档不可用（可选增强，非必需）")

    # Sciverse：key 指纹 + /meta-catalog 探测（无 key 时期待 401 = 服务可达）
    token = sciverse_token()
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if token:
        emit("OK", "platforms", f"SCIVERSE_API_KEY 已配置 = {fingerprint(token)}")
    else:
        emit("INFO", "platforms", "SCIVERSE_API_KEY 未配置：题录补全与 sciverse 数据源停用（fail-soft）")
    status = _probe("https://api.sciverse.space/meta-catalog", headers=headers)
    if status is None:
        emit("WARN", "platforms", "api.sciverse.space 不可达：LSH Sciverse 增强层自动停用")
    elif status == 200:
        emit("OK", "platforms", "Sciverse /meta-catalog 探测通过（key 有效）")
    elif status == 401:
        emit("OK" if not token else "WARN", "platforms",
             "Sciverse 可达（HTTP 401）" + ("：key 无效？" if token else "：未配置 key（预期）"))
    else:
        emit("WARN", "platforms", f"Sciverse /meta-catalog HTTP {status}：增强层状态待观察")

    # Dianshi（点石）MCP：SSO token 档（无独立 API key）；GET /api/mcp 无 token
    # 预期 401 = 服务可达；有 token 时调一次 tools/list 验证有效性（计入配额）
    from agentkit.dianshi import DianshiClient, resolve_token as dianshi_token

    token = dianshi_token()
    if token:
        emit("OK", "platforms", f"DIANSHI_SSO_TOKEN 已配置 = {fingerprint(token)}")
    else:
        emit("INFO", "platforms", "DIANSHI_SSO_TOKEN 未配置：点石题录补全/数据源停用（fail-soft）")
    status = _probe("https://dianshi.opendatalab.org.cn/api/mcp")
    if status is None:
        emit("WARN", "platforms", "dianshi.opendatalab.org.cn 不可达：LSH Dianshi 增强层自动停用")
    elif status == 401:
        emit("OK", "platforms", "Dianshi /api/mcp 可达（HTTP 401 预期：未带凭据）")
    else:
        emit("WARN", "platforms", f"Dianshi /api/mcp HTTP {status}：增强层状态待观察")
    if token:
        try:
            tools = DianshiClient().tools_list()
        except Exception as exc:  # 客户端本身 fail-open，这里是双保险
            tools = None
            emit("WARN", "platforms", f"Dianshi tools/list 异常：{exc}")
        if tools is not None:
            emit("OK", "platforms", f"Dianshi token 有效：{len(tools)} 个 MCP 工具已注册")
        else:
            emit("WARN", "platforms", "Dianshi tools/list 失败：token 可能过期/权限未审批")


def _skill_names(root: Path) -> set[str]:
    return {
        path.name for path in root.glob("langhuan-*")
        if path.is_dir() and (path / "SKILL.md").is_file()
    }


def _plugin_skill_names(home: Path) -> set[str]:
    return {
        path.name for path in home.glob("plugins/**/skills/langhuan-*")
        if path.is_dir() and (path / "SKILL.md").is_file()
    }


def _disabled_skills(path: Path) -> set[str]:
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取 {path}: {exc}") from exc
    values = data.get("disabledSkills", []) if isinstance(data, dict) else []
    if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
        raise ValueError(f"{path}: disabledSkills 必须是字符串数组")
    return set(values)


def _is_disabled_skill(name: str, disabled: set[str]) -> bool:
    return name in disabled or any(value.endswith(f":{name}") for value in disabled)


def check_duplicate_skills(
    skills_dir: Optional[Path] = None,
    workbuddy_home: Optional[Path] = None,
    trae_home: Optional[Path] = None,
    members: Optional[set[str]] = None,
) -> None:
    """只读报告同名技能来源；停用仍由用户在对应宿主中显式执行。"""
    shared_root = skills_dir or Path(
        os.environ.get("LANGHUAN_SKILLS_DIR", Path.home() / ".agents" / "skills")
    )
    wb_home = workbuddy_home or Path(
        os.environ.get("LANGHUAN_WORKBUDDY_HOME", Path.home() / ".workbuddy")
    )
    trae = trae_home or Path(os.environ.get("LANGHUAN_TRAE_HOME", Path.home() / ".trae-cn"))
    known = members or {f"langhuan-{path.name}" for path in (ROOT / "skills").iterdir()
                        if path.is_dir() and (path / "SKILL.md").is_file()}
    shared = _skill_names(shared_root) & known
    for host, home, local_root in (
        ("WorkBuddy", wb_home, wb_home / "skills"),
        ("TRAE", trae, trae / "skills"),
    ):
        local = _skill_names(local_root) & known
        plugins = _plugin_skill_names(home) & known
        duplicates = (shared | local) & plugins
        try:
            disabled = _disabled_skills(home / "skill-config.json")
        except ValueError as exc:
            emit("WARN", "origins", f"{host}: {exc}")
            disabled = set()
        if duplicates:
            active = {name for name in duplicates if not _is_disabled_skill(name, disabled)}
            if active:
                emit("WARN", "origins", f"{host}: {len(active)} 个同名技能可能重复命中 "
                                       f"({', '.join(sorted(active)[:3])}{'…' if len(active) > 3 else ''})；"
                                       "请在宿主 UI/disabledSkills 停用其中一份，勿删除共享技能树")
            else:
                emit("OK", "origins", f"{host}: {len(duplicates)} 个同名技能均已列入 disabledSkills")
        elif plugins:
            emit("OK", "origins", f"{host}: {len(plugins)} 个插件技能，无同名技能形态重叠")
        else:
            emit("INFO", "origins", f"{host}: 未发现插件技能；共享 {len(shared)} / 宿主 {len(local)}")


def check_hosts() -> None:
    for name, path, desc in HOST_HINTS:
        if path.exists():
            emit("INFO", "hosts", f"{name}: {path} 存在（{desc}）")
        else:
            emit("INFO", "hosts", f"{name}: 未发现 {path}（未安装或未初始化）")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="LangHuan 环境体检（不回显密钥明文）")
    parser.add_argument("--offline", action="store_true", help="跳过平台网络探测，保留全部本地检查")
    parser.add_argument("--json", action="store_true", help="仅输出机器可读 JSON 摘要")
    args = parser.parse_args(argv)
    global _json_output
    _json_output = args.json
    _findings.clear()
    if not args.json:
        print("LangHuan doctor — 环境体检（凭证只显示末 4 位指纹，不回显明文）\n")
    check_python()
    check_venvs()
    env_slots = check_creds()
    check_settings(env_slots)
    check_hosts()
    check_duplicate_skills()
    if args.offline:
        emit("INFO", "platforms", "--offline：跳过 MinerU/Sciverse/Dianshi 网络探测")
    else:
        check_platforms()
    check_mineru_quota()
    fails = sum(1 for level, _, _ in _findings if level == "FAIL")
    warns = sum(1 for level, _, _ in _findings if level == "WARN")
    if args.json:
        print(json.dumps({
            "ok": fails == 0,
            "fail": fails,
            "warn": warns,
            "offline": args.offline,
            "findings": [
                {"level": level, "group": group, "message": message}
                for level, group, message in _findings
            ],
        }, ensure_ascii=False))
    else:
        print(f"\n结果：{fails} FAIL / {warns} WARN" + (" — 存在需修复项" if fails else " — 无阻断项"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
