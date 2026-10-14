"""ORCID 公共 API 连通性自检脚本（PCS · 免 key 环境检查用）。

安全约束：
- 无需任何 API key：ORCID 公共 API（pub.orcid.org/v3.0）对公开记录免鉴权，
  仅用 Python 标准库 urllib 发送一个 personal-details 只读请求。
- 本文件同时提供 load_env()，供 synthesize_collab.py 复用读取本技能 .env
  （仅从 .env 读取凭据，绝不打印密钥明文，只输出是否加载及长度）。

用法：
    python scripts/test_orcid.py [--iid 0000-0003-4284-8096]
"""

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ORCID_API = "https://pub.orcid.org/v3.0"
DEFAULT_IID = "0000-0003-4284-8096"
TIMEOUT = 30  # 秒


def load_env(path: Path) -> dict:
    """极简 .env 解析（KEY=VALUE，忽略注释与空行），避免额外依赖。"""
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def _value(node):
    """ORCID name 字段节点取值（v3.0 JSON: {"value": ...} 或 null）。"""
    if not isinstance(node, dict):
        return None
    return node.get("value")


def fetch_personal_details(iid: str) -> dict:
    """GET /v3.0/{iid}/personal-details（Accept: application/json），返回解析后的 dict。"""
    url = "%s/%s/personal-details" % (ORCID_API, iid)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="ORCID 公共 API 连通性自检（免 key，纯标准库 urllib）")
    parser.add_argument("--iid", default=DEFAULT_IID,
                        help="ORCID iD（默认 %s，支持完整 https://orcid.org/ 形式）" % DEFAULT_IID)
    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    # 兼容粘贴完整 ORCID URL 的情况
    iid = args.iid.strip().rstrip("/").rsplit("/", 1)[-1]
    url = "%s/%s/personal-details" % (ORCID_API, iid)
    print("[..] GET %s（timeout=%ds）" % (url, TIMEOUT))

    try:
        payload = fetch_personal_details(iid)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        print("[FAIL] ORCID API 调用失败：%s: %s" % (type(exc).__name__, exc))
        return 1

    name = payload.get("name") or {}
    given = _value(name.get("given-names"))
    family = _value(name.get("family-name"))
    credit = _value(name.get("credit-name"))
    display = credit or " ".join(part for part in (given, family) if part)
    print("[OK] HTTP 200，name = %s" % (display or "（ORCID 未公开姓名）"))
    print("[OK] family-name = %s，given-names = %s"
          % (family or "（未公开）", given or "（未公开）"))

    if display:
        print("[PASS] ORCID 公共 API 连通性验证通过（%s）" % iid)
        return 0
    print("[WARN] 已连通，但姓名字段为空（iD 可能受限或未公开，不影响连通性结论）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
