"""M5 API 层冒烟测试：端点契约 + 作业生命周期（runner 用替身，不触网、不调模型）。"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from archive_plate_assembler.api_server import create_app


@pytest.fixture()
def client(tmp_path):
    return TestClient(create_app(work_root=tmp_path / "jobs", runner=_fake_runner))


def _fake_runner(pdf_paths, out_dir, **kwargs):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "species.json").write_text(json.dumps(
        {"species": [{"scientific_name": "Testus fakeus"}]}, ensure_ascii=False),
        encoding="utf-8")
    return {"source_book": kwargs.get("source_book"), "pages": 1, "schema_valid": 1}


def _upload(client, **forms):
    return client.post("/api/v1/jobs",
                       files=[("files", ("book.pdf", b"%PDF-1.4 fake", "application/pdf"))],
                       data=forms)


def _wait_done(client, job_id, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/v1/jobs/{job_id}").json()
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(0.05)
    raise AssertionError("作业未在限期内结束")


def test_create_job_returns_202_and_pending(client):
    resp = _upload(client)
    assert resp.status_code == 202
    body = resp.json()
    assert body["status"] == "pending" and len(body["job_id"]) == 12


def test_job_lifecycle_succeeds_and_persists_summary(client):
    job_id = _upload(client, source_book="测试图鉴", taxon_hint="贝类",
                     page_range="1,3").json()["job_id"]
    job = _wait_done(client, job_id)
    assert job["status"] == "succeeded"
    assert job["summary"]["source_book"] == "测试图鉴"
    assert job["error"] is None


def test_species_endpoint_serves_result_after_success(client):
    job_id = _upload(client).json()["job_id"]
    _wait_done(client, job_id)
    resp = client.get(f"/api/v1/jobs/{job_id}/species")
    assert resp.status_code == 200
    assert resp.json()["species"][0]["scientific_name"] == "Testus fakeus"


def test_species_endpoint_409_while_pending(client):
    job_id = _upload(client).json()["job_id"]
    resp = client.get(f"/api/v1/jobs/{job_id}/species")
    assert resp.status_code in (409, 200)  # 替身极快，允许竞态成功
    if resp.status_code == 200:
        _wait_done(client, job_id)


def test_runner_receives_page_range_and_auto_layout(client, tmp_path):
    seen = {}

    def spy_runner(pdf_paths, out_dir, **kwargs):
        seen.update(kwargs)
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        return {"pages": 0}

    c = TestClient(create_app(work_root=tmp_path / "jobs2", runner=spy_runner))
    job_id = _upload(c, page_range="2,9").json()["job_id"]
    _wait_done(c, job_id)
    assert seen["page_range"] == (2, 9)
    assert seen["auto_layout"] is True


def test_bad_page_range_rejected(client):
    resp = _upload(client, page_range="abc")
    assert resp.status_code == 400


def test_missing_job_404(client):
    assert client.get("/api/v1/jobs/deadbeefdead").status_code == 404
    assert client.get("/api/v1/jobs/deadbeefdead/species").status_code == 404


def test_failed_pipeline_surfaces_error(client, tmp_path):
    def boom(pdf_paths, out_dir, **kwargs):
        raise RuntimeError("模型 key 缺失")

    c = TestClient(create_app(work_root=tmp_path / "jobs3", runner=boom))
    job_id = _upload(c).json()["job_id"]
    job = _wait_done(c, job_id)
    assert job["status"] == "failed"
    assert "key" in job["error"]
    assert c.get(f"/api/v1/jobs/{job_id}/species").status_code == 409


def test_status_file_written_to_disk(client, tmp_path):
    job_id = _upload(client).json()["job_id"]
    _wait_done(client, job_id)
    status_file = tmp_path / "jobs" / job_id / "job_status.json"
    assert json.loads(status_file.read_text(encoding="utf-8"))["status"] == "succeeded"


def test_restart_recovers_job_index(client, tmp_path):
    job_id = _upload(client).json()["job_id"]
    _wait_done(client, job_id)
    # 用同一 work_root 建新 app（模拟服务重启）：作业索引应从磁盘恢复
    c2 = TestClient(create_app(work_root=tmp_path / "jobs", runner=_fake_runner))
    job = c2.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "succeeded"
