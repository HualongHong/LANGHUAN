"""agent_run --resume: an interrupted OpenAlex job resumes without re-requesting.

The fetch layer is faked by monkeypatching ``agentkit.fetch.fetch`` (which is
what ``openalex._default_fetcher`` builds on), so the test never touches the
network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import agentkit.fetch as agentkit_fetch
import scripts.agent_run as agent_run

SKILL_ROOT = Path(agent_run.__file__).resolve().parents[1]


def make_work(n: int) -> dict:
    return {
        "id": f"https://openalex.org/W{n:012d}",
        "doi": f"https://doi.org/10.1000/test{n}",
        "display_name": f"Study number {n}",
        "publication_year": 2023,
        "type": "article",
        "language": "en",
        "authorships": [
            {"author": {"display_name": "Smith, John"}, "raw_affiliation_string": "Example Univ"}
        ],
        "primary_location": {"source": {"display_name": "Journal of Examples"}},
    }


def _json_response(url, payload):
    return agentkit_fetch.FetchResult(
        status=200,
        headers={"Content-Type": "application/json", "X-RateLimit-Remaining": "900"},
        body=json.dumps(payload).encode("utf-8"),
        redirect_chain=[],
        url=url,
    )


class FakeHttp:
    """Serves two cursor pages; raises once on the second page to simulate a kill."""

    def __init__(self):
        self.urls: list[str] = []
        self.served: list[str] = []
        self._fail_page2_once = True

    def __call__(self, url, **kwargs):
        self.urls.append(url)
        if "cursor=%2A" in url or "cursor=*" in url:
            self.served.append(url)
            return _json_response(
                url,
                {
                    "results": [make_work(1), make_work(2)],
                    "meta": {"next_cursor": "C2"},
                },
            )
        if "cursor=C2" in url:
            if self._fail_page2_once:
                self._fail_page2_once = False
                raise RuntimeError("simulated interruption during page 2")
            self.served.append(url)
            return _json_response(url, {"results": [make_work(3)], "meta": {"next_cursor": None}})
        raise AssertionError(f"unexpected URL: {url}")


def _write_job(tmp_path: Path) -> Path:
    job = tmp_path / "openalex_job.json"
    job.write_text(
        json.dumps({"mode": "filter", "filter": "title.search:study", "per_page": 2}),
        encoding="utf-8",
    )
    return job


def _write_config(tmp_path: Path) -> Path:
    """Minimal config extending the default: everything LLM/heavy disabled."""
    cfg = {
        "extends": str(SKILL_ROOT / "config/default.yaml"),
        "skeleton": {
            "datasource": "openalex",
            "language_detection": False,
            "translate_to_chinese": False,
            "segment_abstract": False,
            "restore_author_address": False,
        },
        "review": {"enabled": False},
        "global": {"generate_citation": False},
    }
    path = tmp_path / "test_config.json"  # JSON is valid YAML
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return path


def _args(tmp_path: Path, resume: bool) -> list[str]:
    argv = [
        "--input",
        str(_write_job(tmp_path)),
        "--config",
        str(_write_config(tmp_path)),
        "--workdir",
        str(tmp_path / "workdir"),
        "--disable-llm",
        "--run-name",
        "resume_test",
    ]
    if resume:
        argv.append("--resume")
    return argv


def test_resume_no_duplicate_requests(tmp_path, monkeypatch):
    fake = FakeHttp()
    monkeypatch.setattr(agentkit_fetch, "fetch", fake)
    (tmp_path / "workdir").mkdir()

    # Run 1: interrupted while fetching page 2.
    with pytest.raises(RuntimeError, match="simulated interruption"):
        agent_run.run(agent_run.build_parser().parse_args(_args(tmp_path, resume=True)))

    state_dir = tmp_path / "workdir" / "quota_state"
    assert (state_dir / "openalex_cursor.json").exists()
    cursor = json.loads((state_dir / "openalex_cursor.json").read_text(encoding="utf-8"))
    assert cursor["cursor"] == "C2"

    # Run 2: resumes from the cursor; page 1 (fully received in run 1) is
    # never re-requested. Page 2 never arrived in run 1 (the request died),
    # so retrying that one URL is expected.
    summary = agent_run.run(agent_run.build_parser().parse_args(_args(tmp_path, resume=True)))
    assert summary["records"] == 3
    assert summary["quota_state"] == str(state_dir)
    assert len(fake.served) == 2, fake.urls
    run2_urls = fake.urls[len(fake.urls) - 1 :]
    assert not set(run2_urls) & {fake.served[0]}, "resume re-requested the received page 1"
    assert (state_dir / "openalex_done.json").exists()
    csv_path = Path(summary["outputs"]["csv"])
    assert csv_path.exists()


def test_without_resume_no_state_persisted(tmp_path, monkeypatch):
    fake = FakeHttp()
    fake._fail_page2_once = False  # uninterrupted run
    monkeypatch.setattr(agentkit_fetch, "fetch", fake)
    (tmp_path / "workdir").mkdir()

    args = agent_run.build_parser().parse_args(_args(tmp_path, resume=False))
    summary = agent_run.run(args)
    assert summary["records"] == 3
    assert summary["quota_state"] is None
    assert not (tmp_path / "workdir" / "quota_state").exists()
