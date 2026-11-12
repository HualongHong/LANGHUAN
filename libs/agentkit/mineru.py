"""MinerU 文档解析客户端（OpenDataLab 体系）。

三档统一封装，输出归一化为 :class:`MinerUDoc`，调用方不感知档位：

- ``flash`` 免登录轻量档：``/api/v1/agent/parse/*``，≤10MB / ≤20 页，仅
  Markdown（CDN 链接），IP 限频（429），无需任何凭据；
- ``token`` 精准解析档：``/api/v4/*``，本地文件走 ``file-urls/batch``
  签名上传（PUT → 自动建任务），≤200MB / ≤200 页，zip 输出
  （full.md + content_list.json + 图片），Bearer ``MINERU_API_TOKEN``，
  每日 1000 页最高优先级额度；
- ``local`` 本地引擎档：``magic-pdf`` 懒加载，零网络零配额；未安装或
  调用失败即视为该档不可用。

失败语义（平台是增强层不是依赖层）：任何网络错误、429 限频、401/鉴权
失败、任务 state=failed、结果下载失败，都抛 :class:`MinerUError` 子类，
**绝不返回半成品**。:func:`MinerUClient.parse_file` 的 ``mode="auto"``
按 文件大小/凭据/本地可用性 依次尝试各档，全部失败才向上抛——调用方
（如 APA document_decoder）据此回落本地 render+视觉路径，保证平台
不可用时工作流独立执行。

纪律（:mod:`agentkit.quota`）：不伪造 User-Agent（urllib 诚实标识）、
严格串行、收到 429 不睡死——直接视为本档不可用并落下一档。

凭据解析顺序：构造参数 → 环境变量 ``MINERU_API_TOKEN``。
端点可用 ``MINERU_FLASH_BASE_URL`` / ``MINERU_TOKEN_BASE_URL`` 覆盖
（自托管 mineru-api 网关场景）。

纯标准库；HTTP 经可注入 transport（测试注入 fake，零网络）::

    def fake(method, url, headers, body, timeout):
        return RawResponse(status=200, headers={}, body=b"{}")
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

FLASH_BASE_URL = "https://mineru.net/api/v1/agent"
TOKEN_BASE_URL = "https://mineru.net/api/v4"
ENV_TOKEN = "MINERU_API_TOKEN"
ENV_FLASH_BASE = "MINERU_FLASH_BASE_URL"
ENV_TOKEN_BASE = "MINERU_TOKEN_BASE_URL"

#: flash 档限制（官方 docs）：超限会被服务端以 -30001/-30003 拒绝，
#: auto 档据此直接跳过（不浪费一次请求）。
FLASH_MAX_BYTES = 10 * 1024 * 1024

#: token 档轮询中的"未完成"状态。
_PENDING_STATES = {"waiting-file", "uploading", "pending", "running", "converting"}


class MinerUError(Exception):
    """MinerU 调用失败的基类；子类区分失败性质，调用方可选择性捕获。"""


class MinerURateLimited(MinerUError):
    """429：IP/账号限频。视为本档暂不可用，回落下一档。"""


class MinerUAuthError(MinerUError):
    """401 / A0202 / A0211：token 缺失、错误 or 过期。"""


class MinerUTaskFailed(MinerUError):
    """任务 state=failed。带 err_code/err_msg。"""

    def __init__(self, message: str, err_code: Optional[int] = None):
        super().__init__(message)
        self.err_code = err_code


@dataclass(frozen=True)
class MinerUDoc:
    """归一化解析产物（三档一致）。"""

    mode: str  # "flash" | "token" | "local"
    markdown: str
    task_id: str = ""
    images: Mapping[str, bytes] = field(default_factory=dict)  # 相对路径 → 字节
    content_list_json: Optional[str] = None  # token 档的 *_content_list.json 原文
    meta: Mapping[str, object] = field(default_factory=dict)


# Transport: (method, url, headers, body, timeout) -> RawResponse
Transport = Callable[[str, str, Mapping[str, Optional[bytes]], float], "RawResponse"]
Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class RawResponse:
    """Transport 层响应（与 agentkit.fetch.RawResponse 同形态，独立定义避免耦合）。"""

    status: int
    headers: Mapping[str, str]
    body: bytes


def default_transport(
    method: str, url: str, headers: Mapping[str, str], body: Optional[bytes], timeout: float
) -> RawResponse:
    """urllib 诚实实现：不伪造 UA，3xx 原样返回（MinerU 端点无重定向需求）。"""
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return RawResponse(status=resp.status, headers=dict(resp.headers), body=resp.read())
    except urllib.error.HTTPError as exc:
        return RawResponse(status=exc.code, headers=dict(exc.headers or {}), body=exc.read() or b"")
    except urllib.error.URLError as exc:
        raise MinerUError(f"network error: {exc}") from exc


def _json_body(payload: Mapping[str, object]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class _DailyPageLedger:
    """跨日重置的页数/任务数记账（state_dir/mineru_quota.json）。

    对应方案"分桶记账到 <workdir>/quota_state/"的承诺：doctor 可读取展示，
    帮助决定今天该用哪一档。页数仅在平台回传 total_pages 时累加。
    """

    def __init__(self, state_dir: Optional[Path]):
        self._path = (state_dir / "mineru_quota.json") if state_dir else None
        self._data: Dict[str, Dict[str, int]] = {}
        if self._path and self._path.exists():
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._data = {}

    def record(self, tier: str, pages: Optional[int]) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        bucket = self._data.setdefault(today, {})
        bucket[f"{tier}_tasks"] = bucket.get(f"{tier}_tasks", 0) + 1
        if pages:
            bucket[f"{tier}_pages"] = bucket.get(f"{tier}_pages", 0) + int(pages)
        if self._path:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self._path)

    def today(self) -> Dict[str, int]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return dict(self._data.get(today, {}))


class MinerUClient:
    """MinerU 三档解析客户端。所有方法串行执行；失败抛 :class:`MinerUError`。"""

    def __init__(
        self,
        token: Optional[str] = None,
        transport: Optional[Transport] = None,
        sleeper: Sleeper = time.sleep,
        poll_interval: float = 3.0,
        poll_timeout: float = 300.0,
        request_timeout: float = 60.0,
        state_dir: Optional[Path] = None,
        model_version: str = "vlm",
    ):
        self._token = token if token is not None else os.environ.get(ENV_TOKEN, "").strip() or None
        self._transport = transport or default_transport
        self._sleeper = sleeper
        self._poll_interval = poll_interval
        self._poll_timeout = poll_timeout
        self._request_timeout = request_timeout
        self._ledger = _DailyPageLedger(Path(state_dir) if state_dir else None)
        self._model_version = model_version
        self._flash_base = os.environ.get(ENV_FLASH_BASE, FLASH_BASE_URL).rstrip("/")
        self._token_base = os.environ.get(ENV_TOKEN_BASE, TOKEN_BASE_URL).rstrip("/")

    # ------------------------------------------------------------------ 档位

    def modes_available(self) -> Dict[str, bool]:
        """各档可用性快照（不发起网络请求）。

        flash 恒 True（免登录，可达性在调用时才知道）；token 取决于凭据；
        local 取决于 magic-pdf 是否可导入。
        """
        return {
            "flash": True,
            "token": bool(self._token),
            "local": importlib.util.find_spec("magic_pdf") is not None,
        }

    def usage_today(self) -> Dict[str, int]:
        """今日已消耗（账本视角）：{flash_tasks, flash_pages, token_tasks, ...}。"""
        return self._ledger.today()

    # ------------------------------------------------------------------ 入口

    def parse_file(
        self,
        path: str,
        mode: str = "auto",
        page_range: Optional[str] = None,
        language: str = "ch",
        is_ocr: bool = True,
        enable_table: bool = True,
        enable_formula: bool = False,
    ) -> MinerUDoc:
        """解析本地文件。``mode`` 为 ``auto|flash|token|local``。

        auto 档序：flash（≤10MB 才尝试）→ token（有凭据）→ local（可导入）。
        每档失败记录 WARN 并落下一档；全部失败抛 :class:`MinerUError`。
        """
        p = Path(path)
        if not p.is_file():
            raise MinerUError(f"file not found: {path}")
        if mode != "auto":
            # 显式指定档位：错误原样上抛（调用方要的就是这一档的成败）。
            doc = self._parse_file_with_tier(
                mode, p, page_range, language, is_ocr, enable_table, enable_formula
            )
            self._ledger.record(mode, _pages_from_meta(doc.meta))
            return doc
        tiers = self._resolve_tiers(mode, p.stat().st_size)
        errors = []
        for tier in tiers:
            try:
                doc = self._parse_file_with_tier(
                    tier, p, page_range, language, is_ocr, enable_table, enable_formula
                )
                self._ledger.record(tier, _pages_from_meta(doc.meta))
                return doc
            except MinerUError as exc:
                logger.warning("mineru tier %s failed for %s: %s", tier, p.name, exc)
                errors.append(f"{tier}: {exc}")
        raise MinerUError(f"all tiers failed for {p.name}: " + "; ".join(errors))

    def parse_url(
        self,
        url: str,
        mode: str = "auto",
        page_range: Optional[str] = None,
        language: str = "ch",
        is_ocr: bool = True,
        enable_table: bool = True,
        enable_formula: bool = False,
    ) -> MinerUDoc:
        """解析公开可访问的文件 URL（两云端档均支持；local 档不适用）。"""
        if mode != "auto":
            if mode == "local":
                raise MinerUError("mode 'local' does not support URL input")
            doc = self._parse_url_with_tier(
                mode, url, page_range, language, is_ocr, enable_table, enable_formula
            )
            self._ledger.record(mode, _pages_from_meta(doc.meta))
            return doc
        tiers = self._resolve_tiers(mode, size=None)
        errors = []
        for tier in tiers:
            if tier == "local":
                continue
            try:
                doc = self._parse_url_with_tier(
                    tier, url, page_range, language, is_ocr, enable_table, enable_formula
                )
                self._ledger.record(tier, _pages_from_meta(doc.meta))
                return doc
            except MinerUError as exc:
                logger.warning("mineru tier %s failed for %s: %s", tier, url, exc)
                errors.append(f"{tier}: {exc}")
        raise MinerUError(f"all tiers failed for {url}: " + "; ".join(errors))

    # ------------------------------------------------------------- 档位解析

    def _resolve_tiers(self, mode: str, size: Optional[int]) -> list:
        avail = self.modes_available()
        if mode != "auto":
            if not avail.get(mode):
                raise MinerUError(f"mode {mode!r} unavailable (no token or magic-pdf?)")
            return [mode]
        tiers: list = []
        if size is None or size <= FLASH_MAX_BYTES:
            tiers.append("flash")
        if avail["token"]:
            tiers.append("token")
        if avail["local"]:
            tiers.append("local")
        if not tiers:
            raise MinerUError("no mineru tier available (no token, magic-pdf not installed)")
        return tiers

    def _parse_file_with_tier(self, tier, p, page_range, language, is_ocr, enable_table, enable_formula):
        if tier == "flash":
            return self._flash_file(p, page_range, language, is_ocr, enable_table, enable_formula)
        if tier == "token":
            return self._token_file(p, page_range, language, is_ocr, enable_table, enable_formula)
        return self._local_file(p, language, is_ocr, enable_table, enable_formula)

    def _parse_url_with_tier(self, tier, url, page_range, language, is_ocr, enable_table, enable_formula):
        if tier == "flash":
            return self._flash_url(url, page_range, language, is_ocr, enable_table, enable_formula)
        return self._token_url(url, page_range, language, is_ocr, enable_table, enable_formula)

    # ------------------------------------------------------------ flash 档

    def _flash_headers(self) -> Dict[str, str]:
        return {"Content-Type": "application/json", "Accept": "*/*"}

    def _flash_submit_file(self, file_name, page_range, language, is_ocr, enable_table, enable_formula):
        payload: Dict[str, object] = {
            "file_name": file_name,
            "language": language,
            "is_ocr": is_ocr,
            "enable_table": enable_table,
            "enable_formula": enable_formula,
        }
        if page_range:
            payload["page_range"] = page_range
        raw = self._request(
            "POST", f"{self._flash_base}/parse/file", self._flash_headers(), _json_body(payload)
        )
        data = self._expect_json(raw, "flash submit")
        return data["task_id"], data.get("file_url", "")

    def _flash_submit_url(self, url, page_range, language, is_ocr, enable_table, enable_formula):
        payload: Dict[str, object] = {
            "url": url,
            "language": language,
            "is_ocr": is_ocr,
            "enable_table": enable_table,
            "enable_formula": enable_formula,
        }
        if page_range:
            payload["page_range"] = page_range
        raw = self._request(
            "POST", f"{self._flash_base}/parse/url", self._flash_headers(), _json_body(payload)
        )
        data = self._expect_json(raw, "flash submit")
        return data["task_id"], ""

    def _flash_poll(self, task_id: str) -> Dict[str, object]:
        url = f"{self._flash_base}/parse/{task_id}"
        deadline = time.monotonic() + self._poll_timeout
        while True:
            data = self._expect_json(self._request("GET", url, {}, None), "flash poll")
            state = data.get("state", "")
            if state == "done":
                return data
            if state == "failed":
                raise MinerUTaskFailed(
                    f"flash task failed: {data.get('err_msg', 'unknown')}",
                    err_code=data.get("err_code"),
                )
            if time.monotonic() > deadline:
                raise MinerUError(f"flash poll timeout ({self._poll_timeout}s): {task_id}")
            self._sleeper(self._poll_interval)

    def _flash_file(self, p, page_range, language, is_ocr, enable_table, enable_formula):
        task_id, file_url = self._flash_submit_file(
            p.name, page_range, language, is_ocr, enable_table, enable_formula
        )
        if not file_url:
            raise MinerUError("flash submit returned no file_url")
        put = self._request("PUT", file_url, {"Content-Type": "application/octet-stream"}, p.read_bytes())
        if not 200 <= put.status < 300:
            raise MinerUError(f"flash upload failed: HTTP {put.status}")
        done = self._flash_poll(task_id)
        markdown_url = done.get("markdown_url", "")
        if not markdown_url:
            raise MinerUError("flash done but no markdown_url")
        md = self._request("GET", markdown_url, {}, None)
        if md.status != 200:
            raise MinerUError(f"flash markdown download failed: HTTP {md.status}")
        return MinerUDoc(
            mode="flash",
            markdown=md.body.decode("utf-8", errors="replace"),
            task_id=task_id,
            meta={"state": "done"},
        )

    def _flash_url(self, url, page_range, language, is_ocr, enable_table, enable_formula):
        task_id, _ = self._flash_submit_url(url, page_range, language, is_ocr, enable_table, enable_formula)
        done = self._flash_poll(task_id)
        markdown_url = done.get("markdown_url", "")
        if not markdown_url:
            raise MinerUError("flash done but no markdown_url")
        md = self._request("GET", markdown_url, {}, None)
        if md.status != 200:
            raise MinerUError(f"flash markdown download failed: HTTP {md.status}")
        return MinerUDoc(
            mode="flash",
            markdown=md.body.decode("utf-8", errors="replace"),
            task_id=task_id,
            meta={"state": "done"},
        )

    # ------------------------------------------------------------ token 档

    def _token_headers(self) -> Dict[str, str]:
        if not self._token:
            raise MinerUAuthError("MINERU_API_TOKEN not configured")
        return {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "Authorization": f"Bearer {self._token}",
        }

    def _token_common(self, is_ocr, enable_table, enable_formula) -> Dict[str, object]:
        return {
            "is_ocr": is_ocr,
            "enable_table": enable_table,
            "enable_formula": enable_formula,
            "language": "ch",
            "model_version": self._model_version,
        }

    def _token_file(self, p, page_range, language, is_ocr, enable_table, enable_formula):
        common = self._token_common(is_ocr, enable_table, enable_formula)
        if language:
            common["language"] = language
        entry: Dict[str, object] = {"name": p.name}
        if page_range:
            entry["page_ranges"] = page_range
        payload = {**common, "files": [entry]}
        raw = self._request(
            "POST", f"{self._token_base}/file-urls/batch", self._token_headers(), _json_body(payload)
        )
        data = self._expect_json(raw, "token batch submit")
        file_urls = data.get("file_urls") or []
        if not file_urls:
            raise MinerUError("token batch submit returned no file_urls")
        put = self._request("PUT", file_urls[0], {"Content-Type": "application/octet-stream"}, p.read_bytes())
        if not 200 <= put.status < 300:
            raise MinerUError(f"token upload failed: HTTP {put.status}")
        batch_id = data["batch_id"]
        done = self._token_poll_batch(batch_id, expect_name=p.name)
        return self._doc_from_zip_url(done["full_zip_url"], mode="token", task_id=batch_id)

    def _token_url(self, url, page_range, language, is_ocr, enable_table, enable_formula):
        common = self._token_common(is_ocr, enable_table, enable_formula)
        if language:
            common["language"] = language
        entry: Dict[str, object] = {"url": url}
        if page_range:
            entry["page_ranges"] = page_range
        payload = {**common, "files": [entry]}
        raw = self._request(
            "POST", f"{self._token_base}/extract/task/batch", self._token_headers(), _json_body(payload)
        )
        data = self._expect_json(raw, "token url batch submit")
        batch_id = data["batch_id"]
        done = self._token_poll_batch(batch_id, expect_name=None)
        return self._doc_from_zip_url(done["full_zip_url"], mode="token", task_id=batch_id)

    def _token_poll_batch(self, batch_id: str, expect_name: Optional[str]) -> Dict[str, object]:
        url = f"{self._token_base}/extract-results/batch/{batch_id}"
        deadline = time.monotonic() + self._poll_timeout
        while True:
            data = self._expect_json(self._request("GET", url, self._token_headers(), None), "token poll")
            results = data.get("extract_result") or []
            target = None
            for item in results:
                if expect_name is None or item.get("file_name") == expect_name or len(results) == 1:
                    target = item
                    break
            if target is None:
                raise MinerUError(f"token batch result missing entry for {expect_name!r}")
            state = target.get("state", "")
            if state == "done":
                if not target.get("full_zip_url"):
                    raise MinerUError("token done but no full_zip_url")
                return target
            if state == "failed":
                raise MinerUTaskFailed(
                    f"token task failed: {target.get('err_msg', 'unknown')}",
                    err_code=target.get("err_code"),
                )
            if time.monotonic() > deadline:
                raise MinerUError(f"token poll timeout ({self._poll_timeout}s): {batch_id}")
            self._sleeper(self._poll_interval)

    def _doc_from_zip_url(self, zip_url: str, mode: str, task_id: str) -> MinerUDoc:
        raw = self._request("GET", zip_url, {}, None)
        if raw.status != 200:
            raise MinerUError(f"result zip download failed: HTTP {raw.status}")
        return _doc_from_zip_bytes(raw.body, mode=mode, task_id=task_id)

    # ------------------------------------------------------------ local 档

    def _local_file(self, p, language, is_ocr, enable_table, enable_formula):
        """magic-pdf v2 惯用入口（懒加载）。任何导入/调用失败 → MinerUError。"""
        try:
            from magic_pdf.data.data_reader_writer import FileBasedDataReader, FileBasedDataWriter
            from magic_pdf.data.dataset import PymuDocDataset
            from magic_pdf.model.doc_analyze_by_custom_model import doc_analyze
        except ImportError as exc:
            raise MinerUError(f"magic-pdf not installed: {exc}") from exc

        try:
            with tempfile.TemporaryDirectory(prefix="langhuan-mineru-") as tmp:
                tmp_path = Path(tmp)
                name = p.stem
                md_writer = FileBasedDataWriter(str(tmp_path))
                image_writer = FileBasedDataWriter(str(tmp_path / "images"))
                reader = FileBasedDataReader(str(p.parent))
                pdf_bytes = reader.read(p.name)
                ds = PymuDocDataset(pdf_bytes)
                infer_result = doc_analyze(ds, ocr=is_ocr, show_log=False)
                pipe_result = infer_result.pipe_ocr_mode(image_writer) if is_ocr else infer_result.pipe_txt_mode(image_writer)
                pipe_result.dump_md(md_writer, f"{name}.md", image_dir="images")
                md_file = tmp_path / f"{name}.md"
                markdown = md_file.read_text(encoding="utf-8") if md_file.exists() else ""
                images: Dict[str, bytes] = {}
                images_dir = tmp_path / "images"
                if images_dir.exists():
                    for img in sorted(images_dir.iterdir()):
                        if img.is_file():
                            images[f"images/{img.name}"] = img.read_bytes()
                if not markdown.strip():
                    raise MinerUError("magic-pdf produced empty markdown")
                return MinerUDoc(mode="local", markdown=markdown, images=images, meta={"engine": "magic-pdf"})
        except MinerUError:
            raise
        except Exception as exc:  # magic-pdf 版本漂移面大：统一收敛为档不可用
            raise MinerUError(f"magic-pdf parse failed: {exc}") from exc

    # ------------------------------------------------------------------ HTTP

    def _request(
        self, method: str, url: str, headers: Mapping[str, str], body: Optional[bytes]
    ) -> RawResponse:
        raw = self._transport(method, url, headers, body, self._request_timeout)
        if raw.status == 429:
            raise MinerURateLimited(f"HTTP 429: {method} {url}")
        if raw.status in (401, 403):
            raise MinerUAuthError(f"HTTP {raw.status}: {method} {url}")
        return raw

    def _expect_json(self, raw: RawResponse, what: str) -> Dict[str, object]:
        if not 200 <= raw.status < 300:
            raise MinerUError(f"{what} failed: HTTP {raw.status}: {raw.body[:200]!r}")
        try:
            payload = json.loads(raw.body.decode("utf-8", errors="replace"))
        except json.JSONDecodeError as exc:
            raise MinerUError(f"{what} returned non-JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise MinerUError(f"{what} returned unexpected payload")
        code = payload.get("code", 0)
        if code not in (0, "0"):
            msg = str(payload.get("msg", "unknown"))
            if code in ("A0202", "A0211") or code in (-60018, -60019):
                raise MinerUAuthError(f"{what}: code={code} msg={msg}")
            raise MinerUError(f"{what}: code={code} msg={msg}")
        data = payload.get("data")
        if not isinstance(data, dict):
            raise MinerUError(f"{what} returned no data")
        return data


def _pages_from_meta(meta: Mapping[str, object]) -> Optional[int]:
    total = meta.get("total_pages")
    return int(total) if isinstance(total, int) and total > 0 else None


def _doc_from_zip_bytes(body: bytes, mode: str, task_id: str) -> MinerUDoc:
    """token 档 zip → MinerUDoc：full.md 为主，content_list.json 与图片随附。"""
    import io

    markdown = ""
    content_list_json: Optional[str] = None
    images: Dict[str, bytes] = {}
    meta: Dict[str, object] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as zf:
            for name in zf.namelist():
                base = name.rsplit("/", 1)[-1]
                if base.endswith(".md") and not markdown:
                    markdown = zf.read(name).decode("utf-8", errors="replace")
                elif base.endswith("_content_list.json") and content_list_json is None:
                    content_list_json = zf.read(name).decode("utf-8", errors="replace")
                elif base.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    images[name] = zf.read(name)
    except zipfile.BadZipFile as exc:
        raise MinerUError(f"result zip corrupt: {exc}") from exc
    if not markdown.strip():
        raise MinerUError("result zip contains no markdown")
    return MinerUDoc(
        mode=mode,
        markdown=markdown,
        task_id=task_id,
        images=images,
        content_list_json=content_list_json,
        meta=meta,
    )


__all__ = [
    "FLASH_BASE_URL",
    "FLASH_MAX_BYTES",
    "MinerUAuthError",
    "MinerUClient",
    "MinerUDoc",
    "MinerUError",
    "MinerURateLimited",
    "MinerUTaskFailed",
    "RawResponse",
    "TOKEN_BASE_URL",
    "default_transport",
]
