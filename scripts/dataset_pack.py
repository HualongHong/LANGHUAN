"""Offline OpenDataLab text-dataset preflight and package builder (no upload)."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
from datetime import date
from pathlib import Path, PurePosixPath
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
from check_dist_secrets import KEY_RE, PLACEHOLDER_MARKERS  # noqa: E402

INVALID_LICENSES = {"", "UNVERIFIED", "UNKNOWN", "TBD", "NONE", "NULL"}
ALLOWED_SUFFIXES = {".json", ".csv", ".yaml", ".yml", ".md"}
_CODE_RE = re.compile(r"^[A-Z]{3}$")


def _metadata(path: Path) -> dict[str, Any]:
    try:
        import yaml
    except ImportError:
        fields = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            match = re.match(r"^([A-Za-z]\w*):\s*(.*)$", line)
            if match:
                fields[match.group(1)] = match.group(2).strip().strip('"').strip("'")
        return fields
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _secret_hit(data: bytes) -> bool:
    return any(
        match.group(0)
        for match in KEY_RE.finditer(data)
        if not any(marker in match.group(0) for marker in PLACEHOLDER_MARKERS)
    )


def _safe_target(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and "\\" not in value and not path.is_absolute() and ".." not in path.parts and "." not in path.parts


def _validate_payload(path: Path, kind: str, errors: list[str], warnings: list[str]) -> dict[str, int]:
    if kind == "generic":
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path.name}: JSON 无法解析: {exc}")
        return {}
    if kind == "gazetteer":
        places = data.get("places") if isinstance(data, dict) else None
        if not isinstance(places, list):
            errors.append(f"{path.name}: places 必须是数组")
            return {}
        aliases = 0
        for index, place in enumerate(places):
            if not isinstance(place, dict):
                errors.append(f"{path.name}: places[{index}] 不是对象")
                continue
            for field in ("name", "country_code", "country_name"):
                if not str(place.get(field) or "").strip():
                    errors.append(f"{path.name}: places[{index}] 缺 {field}")
            if not _CODE_RE.fullmatch(str(place.get("country_code") or "")):
                errors.append(f"{path.name}: places[{index}] country_code 不是三位大写码")
            raw_aliases = place.get("aliases", [])
            if not isinstance(raw_aliases, list):
                errors.append(f"{path.name}: places[{index}] aliases 不是数组")
            else:
                aliases += len([alias for alias in raw_aliases if str(alias).strip()])
        return {"places": len(places), "aliases": aliases}
    if kind == "country_aliases":
        if not isinstance(data, dict):
            errors.append(f"{path.name}: 国家别名顶层必须是对象")
            return {}
        for alias, code in data.items():
            if not str(alias).strip() or not _CODE_RE.fullmatch(str(code)):
                errors.append(f"{path.name}: 非法别名映射 {alias!r} → {code!r}")
        if not data:
            warnings.append(f"{path.name}: 国家别名表为空")
        return {"country_aliases": len(data)}
    errors.append(f"{path.name}: 未知数据 kind {kind!r}")
    return {}


def preflight(manifest_path: str) -> dict[str, Any]:
    manifest = Path(manifest_path).resolve()
    errors: list[str] = []
    warnings: list[str] = []
    file_reports = []
    try:
        config = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": False, "errors": [f"manifest 无法解析: {exc}"], "warnings": [], "files": []}
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        errors.append("manifest.schema_version 必须为 1")
        config = config if isinstance(config, dict) else {}
    base = manifest.parent
    allowed_root = ROOT if base.is_relative_to(ROOT) else base

    metadata_value = str(config.get("metafile") or "")
    readme_value = str(config.get("readme") or "")
    metadata_path = (base / metadata_value).resolve()
    readme_path = (base / readme_value).resolve()
    metadata = {}
    for label, value, path in (
        ("metafile", metadata_value, metadata_path),
        ("README", readme_value, readme_path),
    ):
        if not _safe_target(value) or not path.is_relative_to(allowed_root):
            errors.append(f"{label} 必须是允许根目录内的安全相对路径")
        elif not path.is_file():
            errors.append(f"{label} 不存在")
        elif _secret_hit(path.read_bytes()):
            errors.append(f"{label} 含疑似密钥")
    if metadata_path.is_file() and metadata_path.is_relative_to(allowed_root):
        try:
            metadata = _metadata(metadata_path)
        except Exception as exc:
            errors.append(f"metafile 无法解析: {exc}")

    for field in ("displayName", "license", "taskTypes", "mediaTypes", "labelTypes", "publisher", "publishDate"):
        if not metadata.get(field):
            errors.append(f"metafile 缺 {field}")
    license_value = str(metadata.get("license") or "").strip()
    if license_value.upper() in INVALID_LICENSES:
        errors.append("公开数据集 license 未确认")
    display_name = str(metadata.get("displayName") or "")
    if display_name and not re.fullmatch(r"[\w\u4e00-\u9fff _-]+", display_name):
        errors.append("displayName 含不支持的字符")
    publish_date = str(metadata.get("publishDate") or "")
    if publish_date:
        try:
            date.fromisoformat(publish_date)
        except ValueError:
            errors.append("publishDate 必须是 YYYY-MM-DD")

    files = config.get("files")
    if not isinstance(files, list) or not files:
        errors.append("files 必须是非空数组")
        files = []
    targets = set()
    for index, item in enumerate(files):
        if not isinstance(item, dict):
            errors.append(f"files[{index}] 不是对象")
            continue
        source_value = str(item.get("source") or "")
        target = str(item.get("target") or "")
        kind = str(item.get("kind") or "generic")
        source = (base / source_value).resolve()
        if not _safe_target(target):
            errors.append(f"files[{index}] target 不安全: {target!r}")
            continue
        target_key = target.casefold()
        reserved = {"readme.md", "metafile.yaml", "manifest.sha256", "preflight.json"}
        if target_key in reserved:
            errors.append(f"files[{index}] target 占用保留文件名: {target}")
            continue
        if target_key in targets:
            errors.append(f"target 重复（跨平台忽略大小写）: {target}")
            continue
        targets.add(target_key)
        if not source.is_relative_to(allowed_root) or not source.is_file():
            errors.append(f"files[{index}] source 不存在或越出允许根目录: {source_value}")
            continue
        if source.suffix.lower() not in ALLOWED_SUFFIXES:
            errors.append(f"files[{index}] 不支持的文本数据格式: {source.name}")
            continue
        payload = source.read_bytes()
        if _secret_hit(payload):
            errors.append(f"files[{index}] 含疑似密钥: {source.name}")
            continue
        stats = _validate_payload(source, kind, errors, warnings)
        file_reports.append({
            "target": target,
            "kind": kind,
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "stats": stats,
        })
    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "files": file_reports,
        "metadata": {"displayName": metadata.get("displayName"), "license": metadata.get("license")},
    }


def build_package(manifest_path: str, output_dir: str) -> dict[str, Any]:
    report = preflight(manifest_path)
    if not report["ok"]:
        return report
    output = Path(output_dir)
    if output.exists() and (not output.is_dir() or any(output.iterdir())):
        report["ok"] = False
        report["errors"].append("output_dir 必须不存在或为空，防止旧文件混入")
        return report
    manifest = Path(manifest_path).resolve()
    config = json.loads(manifest.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    shutil.copy2(manifest.parent / config["readme"], output / "README.md")
    shutil.copy2(manifest.parent / config["metafile"], output / "metafile.yaml")
    for item in config["files"]:
        target = output / item["target"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2((manifest.parent / item["source"]).resolve(), target)
    checksums = []
    for path in sorted(output.rglob("*")):
        if path.is_file():
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            checksums.append(f"{digest}  {path.relative_to(output).as_posix()}")
    (output / "MANIFEST.sha256").write_text("\n".join(checksums) + "\n", encoding="utf-8")
    report["output_dir"] = str(output)
    public_report = {key: value for key, value in report.items() if key != "output_dir"}
    (output / "preflight.json").write_text(
        json.dumps(public_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="OpenDataLab 文本数据集离线预检/打包（不上传）")
    parser.add_argument("--manifest", required=True, help="dataset.json")
    parser.add_argument("--output-dir", help="空输出目录；仅在零 ERROR 时生成")
    parser.add_argument("--check-only", action="store_true", help="只预检，不复制文件")
    args = parser.parse_args(argv)
    if not args.check_only and not args.output_dir:
        parser.error("非 --check-only 模式必须提供 --output-dir")
    report = preflight(args.manifest) if args.check_only else build_package(args.manifest, args.output_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
