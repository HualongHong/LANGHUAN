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

退出码：0 = 无 FAIL（WARN/INFO 不阻断）；1 = 存在 FAIL。
凭证文件位置可用环境变量 LANGHUAN_SKILLS_DIR 覆盖（默认 ~/.agents/skills）。
用法：python3 scripts/doctor.py
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "libs"))
sys.path.insert(0, str(ROOT / "scripts"))

from agentkit.credentials import fingerprint  # noqa: E402
from check_dist_secrets import KEY_RE, PLACEHOLDER_MARKERS  # noqa: E402

SKILLS_DIR = Path(os.environ.get("LANGHUAN_SKILLS_DIR", Path.home() / ".agents" / "skills"))
SUITE_ENV = "langhuan.env"

_findings: list[tuple[str, str, str]] = []  # (level, group, msg)


def emit(level: str, group: str, msg: str) -> None:
    _findings.append((level, group, msg))
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
        py = skill_dir / ".venv" / "bin" / "python"
        if not py.exists():
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


def check_hosts() -> None:
    for name, path, desc in HOST_HINTS:
        if path.exists():
            emit("INFO", "hosts", f"{name}: {path} 存在（{desc}）")
        else:
            emit("INFO", "hosts", f"{name}: 未发现 {path}（未安装或未初始化）")


def main() -> int:
    print("LangHuan doctor — 环境体检（凭证只显示末 4 位指纹，不回显明文）\n")
    check_python()
    check_venvs()
    env_slots = check_creds()
    check_settings(env_slots)
    check_hosts()
    fails = sum(1 for level, _, _ in _findings if level == "FAIL")
    warns = sum(1 for level, _, _ in _findings if level == "WARN")
    print(f"\n结果：{fails} FAIL / {warns} WARN" + (" — 存在需修复项" if fails else " — 无阻断项"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
