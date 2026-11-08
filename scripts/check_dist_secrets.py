"""dist/ 分发包密钥泄漏巡检：所有 skill zip 不得含真实 .env 或疑似密钥。

规则（对应 capsule.yaml invariant dist-no-env）：
- zip 内不得有文件名恰为 .env 的条目（.env.example 占位模板放行）；
- zip 内文本不得出现疑似真实密钥（sk- 长串 / *API_KEY|TOKEN=25+ 位值），
  占位符（含 xxx / your-）不算。

退出码 0 = 干净（或 dist/ 无 zip），1 = 发现泄漏。纯标准库。
用法：python scripts/check_dist_secrets.py [dist目录，默认 dist/]
"""

from __future__ import annotations

import glob
import re
import sys
import zipfile

KEY_RE = re.compile(
    rb"(sk-[A-Za-z0-9]{30,}|"
    rb"(?:DEEPSEEK|MINIMAX|QWEN|SCNET|SHANGHAI_SC|ALIYUN|JEV)[A-Z0-9_]*"
    rb"(?:KEY|TOKEN)\s*=\s*[\"']?[A-Za-z0-9_\-]{25,})"
)
PLACEHOLDER_MARKERS = (b"xxx", b"your-", b"XXX", b"YOUR-")


def is_placeholder(match: bytes) -> bool:
    return any(marker in match for marker in PLACEHOLDER_MARKERS)


def scan_zip(path: str) -> list[str]:
    findings: list[str] = []
    try:
        zf = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        return [f"{path}: 损坏的 zip"]
    with zf:
        for name in zf.namelist():
            base = name.rsplit("/", 1)[-1]
            if base == ".env":
                findings.append(f"{path}: 含真实 .env 条目 {name}")
        for name in zf.namelist():
            if name.endswith("/") or base_is_env_example(name):
                continue
            try:
                data = zf.read(name)
            except OSError:
                continue
            for m in KEY_RE.finditer(data):
                if not is_placeholder(m.group(0)):
                    findings.append(
                        f"{path}: {name} 含疑似密钥片段 {m.group(0)[:12]}...")
    return findings


def base_is_env_example(name: str) -> bool:
    return name.rsplit("/", 1)[-1] == ".env.example"


def main(argv: list[str]) -> int:
    dist_dir = argv[1] if len(argv) > 1 else "dist"
    zips = sorted(glob.glob(f"{dist_dir}/*.zip"))
    if not zips:
        print(f"[OK] {dist_dir}/ 无 zip，跳过")
        return 0
    all_findings: list[str] = []
    for zp in zips:
        findings = scan_zip(zp)
        status = "FAIL" if findings else "OK"
        print(f"[{status}] {zp}")
        all_findings.extend(findings)
        for f in findings:
            print(f"       {f}")
    if all_findings:
        print(f"\n发现 {len(all_findings)} 处疑似泄漏，禁止分发。")
        return 1
    print(f"\n{len(zips)} 个 zip 全部干净。")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
