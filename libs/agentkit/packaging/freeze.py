"""版本冻结与逐字节核验（D7/S3 · Phase 4）：把"release 目录 + MANIFEST +
零回归比对"的冻结纪律纳入套件构建。

13 次冻结的经验：逐字节比对是发现下游产物回归的关键手段。本模块提供两个
原语：

- :func:`freeze_directory`：把输出目录按当前状态冻结——先写 ``FROZEN.md``
  （时间/版本/文件数/总字节/生成命令），再为目录内**除自身以外**的每个文件
  计算 sha256，生成 ``MANIFEST.sha256``（相对 POSIX 路径 → 哈希，按路径
  稳定排序，UTF-8、``\\n`` 行尾，跨平台字节稳定）。
- :func:`verify_directory`：重算哈希逐条比对，报告三类清单——``missing``
  （清单在、磁盘无）、``changed`` （哈希不符）、``added`` （磁盘有、清单无）。
  三类皆空才视为零回归。

排除约定：``MANIFEST.sha256`` 自身永不入清单（自引用哈希不可验证）；
``FROZEN.md`` 作为冻结状态的一部分**入清单**（被篡改即报 changed）。

依赖：纯标准库。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

MANIFEST_NAME = "MANIFEST.sha256"
FROZEN_NAME = "FROZEN.md"

_HASH_CHUNK = 8192


@dataclass
class FreezeResult:
    """一次冻结的结果摘要。"""

    root: Path
    version: str
    manifest_path: Path
    frozen_path: Path
    file_count: int
    total_bytes: int


@dataclass
class VerifyResult:
    """一次核验的结果：三类差异清单；全空即 ``ok``。"""

    root: Path
    manifest_path: Path
    missing: List[str] = field(default_factory=list)
    changed: List[str] = field(default_factory=list)
    added: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.changed or self.added)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def _iter_files(root: Path) -> List[Path]:
    """列出冻结/核验覆盖的文件（相对路径，稳定排序）。

    排除 ``MANIFEST.sha256`` 自身（自引用不可验证）；其余一切文件入册，
    含隐藏文件。
    """
    files = [
        p.relative_to(root)
        for p in root.rglob("*")
        if p.is_file() and p.name != MANIFEST_NAME
    ]
    return sorted(files, key=lambda p: p.as_posix())


def _render_manifest(entries: Dict[str, str]) -> str:
    """按相对路径稳定排序渲染清单（``<sha256>  <posix路径>``，\\n 行尾）。"""
    lines = [f"{entries[rel]}  {rel}" for rel in sorted(entries)]
    return "\n".join(lines) + "\n"


def _parse_manifest(text: str) -> Dict[str, str]:
    entries: Dict[str, str] = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        digest, sep, rel = line.partition("  ")
        if not sep or len(digest.strip()) != 64:
            raise ValueError(f"MANIFEST 第 {lineno} 行格式非法: {line!r}")
        entries[rel.strip()] = digest.strip()
    return entries


def freeze_directory(
    root: Path,
    version: str,
    *,
    command: str = "",
    frozen_name: str = FROZEN_NAME,
) -> FreezeResult:
    """冻结 ``root`` 目录的当前状态。

    顺序：先写 ``FROZEN.md``（其内容计入清单），再算全部文件哈希写
    ``MANIFEST.sha256``。重复冻结同一未变更目录，清单逐字节一致
    （FROZEN.md 时间戳变化会被报为 changed，属预期——冻结状态即含时间）。

    Args:
        root: 要冻结的目录（须已存在）。
        version: 冻结版本号（原样记录，如 ``0.3``/``1.0rc1``；不做数值解析）。
        command: 生成命令（原样记入 FROZEN.md，缺省为空）。
        frozen_name: FROZEN 文件名（测试隔离用，一般不改）。
    """
    root = Path(root)
    if not root.is_dir():
        raise FileNotFoundError(f"冻结目录不存在: {root}")

    frozen_path = root / frozen_name
    manifest_path = root / MANIFEST_NAME

    # 统计口径（无自引用循环，一次写盘）：file_count 含 FROZEN.md 自身
    # （条数确定）；total_bytes 为 payload 字节（不含 FROZEN.md 自身与
    # MANIFEST.sha256——本文件体积计入自身字段会产生循环依赖）。
    payload = [p for p in _iter_files(root) if p.as_posix() != frozen_name]
    total_bytes = 0
    for rel in payload:
        try:
            total_bytes += (root / rel).stat().st_size
        except OSError:  # pragma: no cover - 冻结期文件被并发删改
            pass
    frozen_path.write_text(
        _render_frozen(
            version=version,
            file_count=len(payload) + 1,  # +1 = FROZEN.md 自身
            total_bytes=total_bytes,
            command=command,
        ),
        encoding="utf-8",
        newline="\n",
    )

    entries: Dict[str, str] = {}
    for rel in _iter_files(root):
        entries[rel.as_posix()] = _sha256(root / rel)

    manifest_path.write_text(_render_manifest(entries), encoding="utf-8", newline="\n")
    return FreezeResult(
        root=root,
        version=version,
        manifest_path=manifest_path,
        frozen_path=frozen_path,
        file_count=len(entries),
        total_bytes=total_bytes,
    )


def _render_frozen(
    *, version: str, file_count: int, total_bytes: int, command: str
) -> str:
    stamp = datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
    return (
        f"# FROZEN · LangHuan dist freeze\n"
        f"\n"
        f"- version: {version}\n"
        f"- frozen_at: {stamp}\n"
        f"- file_count: {file_count}\n"
        f"- total_bytes: {total_bytes}\n"
        f"- command: {command}\n"
        f"- manifest: {MANIFEST_NAME}\n"
        f"\n"
        f"file_count 含本文件（其哈希入 MANIFEST，篡改即报 changed）；\n"
        f"total_bytes 为 payload 字节（不含本文件与 {MANIFEST_NAME}）。\n"
        f"核验（任何 missing/changed/added 差异均视为下游产物回归）：\n"
        f"    python build_plugin.py verify\n"
    )


def verify_directory(
    root: Optional[Path] = None,
    *,
    manifest_path: Optional[Path] = None,
) -> VerifyResult:
    """按 ``MANIFEST.sha256`` 重算哈希逐条比对。

    Args:
        root: 被核验目录；缺省取 ``manifest_path`` 的父目录。
        manifest_path: 清单路径；缺省 ``<root>/MANIFEST.sha256``。
            二者至少给一个。

    Raises:
        FileNotFoundError: 清单文件不存在。
        ValueError: 清单格式非法。
    """
    if manifest_path is None:
        if root is None:
            raise ValueError("verify_directory: root 与 manifest_path 至少给一个")
        manifest_path = Path(root) / MANIFEST_NAME
    manifest_path = Path(manifest_path)
    if root is None:
        root = manifest_path.parent
    root = Path(root)
    if not manifest_path.is_file():
        raise FileNotFoundError(f"清单不存在: {manifest_path}")

    expected = _parse_manifest(manifest_path.read_text(encoding="utf-8"))

    actual: Dict[str, str] = {}
    for rel in _iter_files(root):
        actual[rel.as_posix()] = _sha256(root / rel)

    missing = [rel for rel in sorted(expected) if rel not in actual]
    changed = [rel for rel in sorted(expected) if rel in actual and actual[rel] != expected[rel]]
    added = [rel for rel in sorted(actual) if rel not in expected]
    return VerifyResult(
        root=root,
        manifest_path=manifest_path,
        missing=missing,
        changed=changed,
        added=added,
    )


__all__ = [
    "FROZEN_NAME",
    "MANIFEST_NAME",
    "FreezeResult",
    "VerifyResult",
    "freeze_directory",
    "verify_directory",
]
