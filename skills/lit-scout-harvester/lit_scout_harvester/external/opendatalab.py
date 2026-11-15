"""Verified offline consumption of LangHuan OpenDataLab dataset packages."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Union

_INVALID_LICENSES = {"", "UNVERIFIED", "UNKNOWN", "TBD", "NONE", "NULL"}
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


class DatasetPackageError(ValueError):
    """The package is incomplete, inconsistent, unlicensed, or corrupted."""


def _safe_relative(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise DatasetPackageError(f"unsafe package path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"..", "."} for part in path.parts):
        raise DatasetPackageError(f"unsafe package path: {value!r}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class OpenDataLabPackage:
    """Verify hashes and provenance fields before exposing package payload paths.

    Checksums detect accidental or post-build modification; they do not authenticate
    the publisher. Users must still trust the package source and its stated license.
    """

    def __init__(self, root: Union[str, Path]):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise DatasetPackageError(f"dataset package is not a directory: {root}")
        self._manifest = self._load_manifest()
        self._report = self._load_report()
        self.metadata = self._verify_metadata()
        self._paths: Dict[str, Path] = {}
        for path_text, expected in self._manifest.items():
            path = self._payload_path(path_text)
            if _sha256(path) != expected:
                raise DatasetPackageError(f"checksum mismatch: {path_text}")
        files = self._report.get("files")
        if not isinstance(files, list) or not files:
            raise DatasetPackageError("preflight.json: files must be a non-empty array")
        for item in files:
            if not isinstance(item, dict):
                raise DatasetPackageError("preflight.json: file entry must be an object")
            target = _safe_relative(item.get("target"))
            kind = item.get("kind")
            if target not in self._manifest:
                raise DatasetPackageError(f"preflight target absent from MANIFEST: {target}")
            if item.get("sha256") != self._manifest[target]:
                raise DatasetPackageError(f"preflight checksum mismatch: {target}")
            if kind in {"gazetteer", "country_aliases"}:
                if kind in self._paths:
                    raise DatasetPackageError(f"multiple payloads for kind: {kind}")
                self._paths[kind] = self._payload_path(target)

    def _payload_path(self, target: str) -> Path:
        path = self.root / _safe_relative(target)
        if path.is_symlink() or not path.is_file():
            raise DatasetPackageError(f"package payload missing or unsafe: {target}")
        resolved = path.resolve()
        if not resolved.is_relative_to(self.root):
            raise DatasetPackageError(f"package payload escapes root: {target}")
        return resolved

    def _load_manifest(self) -> Dict[str, str]:
        path = self.root / "MANIFEST.sha256"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise DatasetPackageError(f"cannot read MANIFEST.sha256: {exc}") from exc
        manifest: Dict[str, str] = {}
        for line in lines:
            if not line.strip():
                continue
            digest, separator, target = line.partition("  ")
            target = _safe_relative(target)
            if not separator or not _DIGEST_RE.fullmatch(digest):
                raise DatasetPackageError(f"invalid MANIFEST.sha256 line: {line!r}")
            if target in manifest:
                raise DatasetPackageError(f"duplicate MANIFEST target: {target}")
            manifest[target] = digest
        for required in ("README.md", "metafile.yaml"):
            if required not in manifest:
                raise DatasetPackageError(f"MANIFEST.sha256 missing {required}")
        return manifest

    def _load_report(self) -> Dict[str, Any]:
        try:
            report = json.loads((self.root / "preflight.json").read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DatasetPackageError(f"cannot read preflight.json: {exc}") from exc
        if not isinstance(report, dict) or report.get("ok") is not True:
            raise DatasetPackageError("preflight.json does not report a valid package")
        return report

    def _verify_metadata(self) -> Dict[str, Any]:
        metadata = self._report.get("metadata")
        if not isinstance(metadata, dict):
            raise DatasetPackageError("preflight.json: metadata must be an object")
        license_value = str(metadata.get("license") or "").strip()
        if license_value.upper() in _INVALID_LICENSES:
            raise DatasetPackageError("dataset license is unverified")
        metafile = self._payload_path("metafile.yaml").read_text(encoding="utf-8")
        match = re.search(r"(?m)^license:\s*(.*?)\s*$", metafile)
        actual_license = match.group(1).strip("\"'") if match else ""
        if actual_license != license_value:
            raise DatasetPackageError("license differs between metafile.yaml and preflight.json")
        return {"displayName": metadata.get("displayName"), "license": license_value}

    def path_for(self, kind: str) -> Path:
        """Return the verified payload path for gazetteer or country_aliases."""
        try:
            return self._paths[kind]
        except KeyError as exc:
            raise DatasetPackageError(f"dataset package has no unique payload for {kind!r}") from exc


__all__ = ["DatasetPackageError", "OpenDataLabPackage"]
