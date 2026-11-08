"""API 层（M5）：把六节点管道包装为 HTTP 服务，供非 Agent 调用方使用。

端点（前缀 /api/v1）：
- POST   /jobs            multipart 上传一个或多个图鉴 PDF，后台跑管道
- GET    /jobs/{job_id}   作业状态（pending/running/succeeded/failed）+ 运行摘要
- GET    /jobs/{job_id}/species   Schema 合规物种 JSON（完成后可用）

设计要点：
- API 场景无人可交互，版式确认固定走 auto_layout=True（候选 0、不排除页）；
  要人工版式确认仍用 CLI/Agent 入口。
- 作业目录 = <work_root>/<job_id>/，状态落盘 job_status.json（服务重启可恢复）；
  管道产物（species.json 等）原样在作业目录内。
- 运行管道在后台线程执行；无模型 key 时作业以 failed 收场，错误进状态文件。

启动：python -m archive_plate_assembler.api_server（端口 env APA_API_PORT，默认 8317）
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from ..pipeline import run_pipeline

logger = logging.getLogger(__name__)

DEFAULT_WORK_ROOT = Path("output") / "api_jobs"


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # Windows 上新建文件常被杀软/索引器短暂占用，os.replace 偶发 WinError 5；
    # 短重试即可消化（Linux 无此问题，重试路径不会触发）。
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))
    raise RuntimeError("unreachable")


class JobStore:
    """作业注册表：内存索引 + 作业目录内 job_status.json 双写。"""

    def __init__(self, work_root: Path | str):
        self.work_root = Path(work_root)
        self.work_root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        # 服务重启后回收磁盘上已有的作业状态
        self._jobs: dict[str, dict] = {}
        for status_file in self.work_root.glob("*/job_status.json"):
            try:
                self._jobs[status_file.parent.name] = json.loads(
                    status_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue

    def create(self) -> tuple[str, Path]:
        job_id = uuid.uuid4().hex[:12]
        job_dir = self.work_root / job_id
        (job_dir / "input").mkdir(parents=True, exist_ok=True)
        status = {"job_id": job_id, "status": "pending",
                  "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                  "finished_at": None, "error": None, "summary": None}
        with self._lock:
            self._jobs[job_id] = status
        _atomic_write_json(job_dir / "job_status.json", status)
        return job_id, job_dir

    def update(self, job_id: str, **fields) -> dict:
        # 磁盘写入必须在锁内、内存发布之前完成：否则观察者先看到内存新状态、
        # 磁盘还是旧状态（测试/外部轮询读文件时出现 running≠succeeded 竞态）。
        with self._lock:
            job = self._jobs.setdefault(job_id, {"job_id": job_id})
            job.update(fields)
            job = dict(job)
            _atomic_write_json(self.work_root / job_id / "job_status.json", job)
        return job

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def path(self, job_id: str) -> Path:
        return self.work_root / job_id


def create_app(work_root: Path | str = DEFAULT_WORK_ROOT,
               runner=run_pipeline) -> FastAPI:
    """app 工厂。runner 可注入替身（测试用），默认真实 run_pipeline。"""
    store = JobStore(work_root)
    app = FastAPI(title="LangHuan Archive-Plate-Assembler API", version="0.1.0")

    def _run_job(job_id: str, job_dir: Path, options: dict) -> None:
        store.update(job_id, status="running")
        try:
            summary = runner(options["pdfs"], str(job_dir), **options["kwargs"])
            store.update(job_id, status="succeeded", finished_at=time.strftime(
                "%Y-%m-%dT%H:%M:%S%z"), summary=summary)
        except Exception as exc:  # 管道失败不拖垮服务进程
            logger.exception("作业 %s 管道失败", job_id)
            store.update(job_id, status="failed", finished_at=time.strftime(
                "%Y-%m-%dT%H:%M:%S%z"), error=str(exc))

    @app.post("/api/v1/jobs")
    async def create_job(
        files: list[UploadFile] = File(...),
        source_book: str | None = Form(default=None),
        taxon_hint: str | None = Form(default=None),
        page_range: str | None = Form(default=None),
        verify: bool = Form(default=True),
    ) -> JSONResponse:
        if not files or all(not f.filename for f in files):
            raise HTTPException(400, "至少上传一个 PDF 文件")
        job_id, job_dir = store.create()
        input_dir = job_dir / "input"
        pdfs = []
        for f in files:
            target = input_dir / Path(f.filename).name
            target.write_bytes(await f.read())
            pdfs.append(str(target))
        kwargs: dict = {"auto_layout": True, "verify": verify}
        if source_book:
            kwargs["source_book"] = source_book
        if taxon_hint:
            kwargs["taxon_hint"] = taxon_hint
        if page_range:
            try:
                start, end = (int(x) for x in page_range.split(",", 1))
                kwargs["page_range"] = (start, end)
            except ValueError:
                raise HTTPException(400, "page_range 格式应为 起,止（含端点）")
        threading.Thread(target=_run_job, args=(job_id, job_dir, {
            "pdfs": pdfs, "kwargs": kwargs}), daemon=True).start()
        return JSONResponse({"job_id": job_id, "status": "pending"}, status_code=202)

    @app.get("/api/v1/jobs/{job_id}")
    async def get_job(job_id: str) -> dict:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "作业不存在")
        return job

    @app.get("/api/v1/jobs/{job_id}/species")
    async def get_species(job_id: str) -> dict:
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, "作业不存在")
        species_path = store.path(job_id) / "species.json"
        if job.get("status") != "succeeded" or not species_path.exists():
            raise HTTPException(409, "作业尚未成功完成，无 species.json")
        try:
            return json.loads(species_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(500, f"species.json 损坏: {exc}")

    return app


def main() -> None:
    import os
    import uvicorn
    logging.basicConfig(level=logging.INFO)
    port = int(os.environ.get("APA_API_PORT", "8317"))
    uvicorn.run(create_app(), host=os.environ.get("APA_API_HOST", "127.0.0.1"),
                port=port)


if __name__ in ("__main__", "__mp_main__"):
    main()
