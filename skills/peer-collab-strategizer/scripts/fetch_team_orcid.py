"""fetch_team_orcid.py — 团队 ORCID 成果聚合（Phase 2 结构化锚点底稿）。

输入团队成员 ORCID iD 列表，经 ORCID 公共 API（pub.orcid.org/v3.0，免 key）
聚合成果：成员信息、按 DOI 去重的作品清单、年份/期刊分布、标题主题词。
输出 JSON 全部落盘（--out），纯只读公开接口。

用法：
    python fetch_team_orcid.py --iid 0000-0003-4284-8096 0000-0002-6002-8086 \
        --out <workdir>/team-orcid.json [--timeout 30]

边界说明：合作者网络边列表需逐 put-code 拉取作品详情，v0.1 不做；
跨团队共同 DOI 由两份聚合 JSON 对比得出（本脚本对单团队聚合，
同团队多成员间的 shared_dois 会自动计算）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

API = "https://pub.orcid.org/v3.0"
UA = "langhuan-pcs/0.1 (ORCID public API aggregation; polite pool)"

_STOP = {
    "the", "of", "and", "in", "a", "to", "from", "across", "by", "with",
    "on", "for", "an", "is", "are", "as", "at", "be", "may", "using", "use",
    "based", "via", "their", "between", "into", "under", "over", "during",
}


def _get(path: str, timeout: int) -> Dict[str, Any]:
    req = urllib.request.Request(
        f"{API}{path}",
        headers={"Accept": "application/json", "User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def member_profile(iid: str, timeout: int) -> Dict[str, Any]:
    rec = _get(f"/{iid}/record", timeout)
    name = rec.get("name") or {}
    given = ((name.get("given-names") or {}).get("value")) or ""
    family = ((name.get("family-name") or {}).get("value")) or ""
    affiliations = []
    for group in (rec.get("activities-summary", {}).get("employments", {}).get("affiliation-group") or []):
        for s in group.get("summaries") or []:
            e = s.get("employment-summary") or {}
            org = ((e.get("organization") or {}).get("name")) or ""
            if org:
                affiliations.append(org)
    return {"iid": iid, "name": f"{given} {family}".strip() or iid,
            "affiliations": affiliations}


def member_works(iid: str, timeout: int) -> List[Dict[str, Any]]:
    data = _get(f"/{iid}/works", timeout)
    items: List[Dict[str, Any]] = []
    for group in data.get("group") or []:
        ext = {e.get("external-id-type"): (e.get("external-id-value") or "").lower()
               for e in (group.get("external-ids", {}).get("external-id") or [])}
        doi = ext.get("doi", "")
        for ws in group.get("work-summary") or []:
            title = (((ws.get("title") or {}).get("title") or {}).get("value")) or "(无题)"
            year = (((ws.get("publication-date") or {}).get("year") or {}).get("value")) or "?"
            journal = ((ws.get("journal-title") or {}).get("value")) or ""
            items.append({
                "iid": iid, "title": title, "year": year, "journal": journal,
                "doi": doi, "type": (ws.get("type") or ""),
            })
    return items


def aggregate(members: List[Dict[str, Any]], items: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_doi: Dict[str, Dict[str, Any]] = {}
    no_doi: List[Dict[str, Any]] = []
    for it in items:
        if it["doi"]:
            cur = by_doi.get(it["doi"])
            if cur is None or (it["year"].isdigit() and (not cur["year"].isdigit() or it["year"] > cur["year"])):
                by_doi[it["doi"]] = it
        else:
            no_doi.append(it)
    works = sorted(by_doi.values(), key=lambda x: x["year"], reverse=True)

    by_year = Counter(w["year"] for w in works if w["year"].isdigit())
    by_journal = Counter(w["journal"] for w in works if w["journal"])
    words = Counter()
    for w in works:
        for word in re.findall(r"[A-Za-z][a-z-]{4,}", w["title"].lower()):
            if word not in _STOP:
                words[word] += 1

    iid_sets = {m["iid"]: set() for m in members}
    for it in items:
        if it["doi"]:
            iid_sets[it["iid"]].add(it["doi"])
    shared = set.intersection(*iid_sets.values()) if len(members) > 1 else set()

    return {
        "team": {
            "members": members,
            "member_count": len(members),
            "works_total_records": len(items),
            "works_unique_doi": len(works),
            "works_without_doi": len(no_doi),
            "shared_dois_within_team": sorted(shared),
        },
        "by_year": dict(sorted(by_year.items(), reverse=True)),
        "by_journal_top20": dict(by_journal.most_common(20)),
        "top_keywords": [w for w, _ in words.most_common(30)],
        "works": works,
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="聚合团队 ORCID 成果为结构化 JSON")
    parser.add_argument("--iid", nargs="+", required=True, help="团队成员 ORCID iD（一个或多个）")
    parser.add_argument("--out", required=True, type=Path, help="输出 JSON 路径")
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args(argv)

    members, items = [], []
    for iid in args.iid:
        sys.stderr.write(f"[fetch] {iid} ...\n")
        members.append(member_profile(iid, args.timeout))
        items.extend(member_works(iid, args.timeout))

    result = aggregate(members, items)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    t = result["team"]
    sys.stderr.write(
        f"[done] {t['member_count']} 成员 | 原始 {t['works_total_records']} 条 | "
        f"去重 {t['works_unique_doi']} DOI | 输出 {args.out}\n"
    )
    print(json.dumps({"ok": True, "out": str(args.out),
                      "unique_dois": t["works_unique_doi"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
