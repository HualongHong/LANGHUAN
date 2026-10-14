"""宿主 Agent 磁盘工单队列协议（技能无关实现，P0a 抽取自 LSH agent_bridge）。

让宿主 Agent 模型代答 LLM 请求，零 API key、零网络 I/O。核心机制：

* 每个请求归约为确定性 ``job_id``（对 model/messages/temperature/
  response_format/max_tokens 的规范化 JSON 取 SHA256 前 16 hex）；
* 答案库（``answers.jsonl`` / ``answers.json``）中已有该 ``job_id`` 则原样返回；
* 否则把请求去重追加到 ``requests.jsonl``，并返回占位符，让 collect 遍跑完、
  报告出全部待答工单。

两段式工作流：

1. ``AGENTKIT_LLM_MODE=collect``：跑管线，填满 ``requests.jsonl``；
2. 宿主模型把答案写进 ``answers.jsonl``（或 ``answers.json``）；
3. ``AGENTKIT_LLM_MODE=apply``：重跑，相同请求直接从磁盘供给。

环境变量（决策 D6，新前缀优先、旧前缀兜底）：

* ``AGENTKIT_LLM_DIR``：队列目录（默认 ``.agentkit_llm``）；
* ``AGENTKIT_LLM_MODE``：``collect``（默认）/ ``apply`` / ``strict``
  （strict 在无答案时抛 ``MissingAnswerError`` 而非返回占位符）；
* 兼容别名 ``LIT_SCOUT_LLM_DIR`` / ``LIT_SCOUT_LLM_MODE``
  （compat, remove after one release）。

kind 注册表：agentkit 内置仅有通用 ``chat`` 兜底；领域 kind（翻译、抽取、
预筛等）由技能侧通过 ``register_kind(...)`` 注册，提供分类匹配器与
占位符工厂。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_QUEUE_DIR = ".agentkit_llm"
ENV_QUEUE_DIR = "AGENTKIT_LLM_DIR"
ENV_MODE = "AGENTKIT_LLM_MODE"
# compat, remove after one release
LEGACY_ENV_QUEUE_DIR = "LIT_SCOUT_LLM_DIR"
LEGACY_ENV_MODE = "LIT_SCOUT_LLM_MODE"

REQUESTS_FILE = "requests.jsonl"
ANSWERS_JSONL = "answers.jsonl"
ANSWERS_JSON = "answers.json"
PLACEHOLDER = "{}"
DEFAULT_KIND = "chat"

_ANSWER_KEYS = ("response", "content", "answer", "text")
_MODE_COLLECT = "collect"
_MODE_APPLY = "apply"
_MODE_STRICT = "strict"


class MissingAnswerError(Exception):
    """strict 模式下工单尚无答案时抛出。"""


# ---------------------------------------------------------------------------
# kind 注册表
# ---------------------------------------------------------------------------


@dataclass
class KindSpec:
    """一种工单类型的分类与占位符策略。

    Attributes:
        name: kind 标签（写入 ``requests.jsonl``，仅供报告与应答参考）。
        match: 分类匹配器，输入为小写化后的拼接 prompt，返回是否属于该 kind。
        placeholder: 占位符工厂，输入原始拼接 prompt，返回占位响应字符串；
            返回 ``None`` 表示该 kind 不提供占位符，继续询问后续 kind。
    """

    name: str
    match: Callable[[str], bool]
    placeholder: Optional[Callable[[str], Optional[str]]] = None


_KINDS: List[KindSpec] = []


def register_kind(
    name: str,
    match: Callable[[str], bool],
    placeholder: Optional[Callable[[str], Optional[str]]] = None,
) -> None:
    """注册一个工单 kind；同名覆盖，注册顺序即匹配优先级。"""
    _KINDS[:] = [kind for kind in _KINDS if kind.name != name]
    _KINDS.append(KindSpec(name=name, match=match, placeholder=placeholder))


def unregister_kind(name: str) -> None:
    """移除一个已注册的 kind（不存在则静默忽略）。"""
    _KINDS[:] = [kind for kind in _KINDS if kind.name != name]


def clear_kinds() -> None:
    """清空注册表（测试用）。"""
    _KINDS.clear()


def registered_kinds() -> Tuple[str, ...]:
    """按注册顺序返回全部 kind 名。"""
    return tuple(kind.name for kind in _KINDS)


# ---------------------------------------------------------------------------
# 环境解析
# ---------------------------------------------------------------------------


def resolve_queue_dir(queue_dir: Optional[str] = None, *, default: str = DEFAULT_QUEUE_DIR) -> Path:
    """返回工单队列目录：显式参数 > 新环境变量 > 旧环境变量（compat） > 默认值。

    Args:
        queue_dir: 显式覆盖；``None`` 时回落到环境变量。
        default: 环境变量也未设置时的默认目录。技能侧（如 LSH）应显式传入
            自己的历史默认值，避免 agentkit 默认值改变既有行为。
    """
    candidate = (
        queue_dir
        or os.environ.get(ENV_QUEUE_DIR)
        or os.environ.get(LEGACY_ENV_QUEUE_DIR)  # compat, remove after one release
        or default
    )
    return Path(candidate)


def resolve_mode(mode: Optional[str] = None) -> str:
    """返回队列模式（``collect`` / ``apply`` / ``strict``）：显式参数 > 新环境变量 > 旧环境变量（compat）。"""
    value = (
        mode
        or os.environ.get(ENV_MODE)
        or os.environ.get(LEGACY_ENV_MODE)  # compat, remove after one release
        or _MODE_COLLECT
    ).strip().lower()
    if value not in (_MODE_COLLECT, _MODE_APPLY, _MODE_STRICT):
        logger.warning(
            "Unknown bridge mode %r (%s/%s); falling back to %s",
            value, ENV_MODE, LEGACY_ENV_MODE, _MODE_COLLECT,
        )
        return _MODE_COLLECT
    return value


# ---------------------------------------------------------------------------
# job_id / 占位符 / 分类
# ---------------------------------------------------------------------------


def job_id_for(
    messages: List[Dict[str, str]],
    response_format: Optional[Dict[str, str]] = None,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    model: str = "",
) -> str:
    """Return the deterministic job id for one chat request.

    The id depends only on the request payload, so the same pipeline run in a
    later pass produces the same ids as long as the prompts and settings match.
    """
    payload: Dict[str, Any] = {"model": model, "messages": messages}
    if temperature is not None:
        payload["temperature"] = temperature
    if response_format:
        payload["response_format"] = response_format
    if max_tokens:
        payload["max_tokens"] = max_tokens
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _join_prompt(messages: List[Dict[str, str]]) -> str:
    return " ".join(str(message.get("content", "")) for message in messages)


def placeholder_for(messages: List[Dict[str, str]]) -> str:
    """Return a shape-compatible stand-in response for an unanswered request.

    Consults each registered kind's placeholder factory in registration order
    and returns the first non-``None`` result; falls back to the neutral
    ``"{}"`` placeholder. The placeholder keeps the calling code on its happy
    path so that a ``collect`` pass discovers exactly the jobs an ``apply``
    pass will issue.
    """
    prompt = _join_prompt(messages)
    for kind in _KINDS:
        if kind.placeholder is None:
            continue
        result = kind.placeholder(prompt)
        if result is not None:
            return result
    return PLACEHOLDER


def classify_request(messages: List[Dict[str, str]]) -> str:
    """Best-effort label for a request, used only for reporting.

    Returns the name of the first registered kind whose matcher accepts the
    (lowercased) prompt, else the generic ``chat`` fallback.
    """
    text = _join_prompt(messages).lower()
    for kind in _KINDS:
        if kind.match(text):
            return kind.name
    return DEFAULT_KIND


# ---------------------------------------------------------------------------
# 答案库 / 请求日志
# ---------------------------------------------------------------------------


def load_answers(queue_dir: Path) -> Dict[str, str]:
    """Load the answer store from ``queue_dir``.

    Args:
        queue_dir: Directory holding ``answers.jsonl`` / ``answers.json``.

    Returns:
        Mapping of job id to the raw response string. Unreadable or malformed
        entries are skipped with a warning rather than failing the run.
        ``answers.jsonl`` wins on conflict with ``answers.json``.
    """
    answers: Dict[str, str] = {}
    jsonl_path = queue_dir / ANSWERS_JSONL
    if jsonl_path.is_file():
        try:
            for line_no, line in enumerate(jsonl_path.read_text(encoding="utf-8").splitlines(), 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    logger.warning("%s:%d is not valid JSON; skipped", jsonl_path, line_no)
                    continue
                entry = _answer_entry(record)
                if entry is not None:
                    answers[entry[0]] = entry[1]
        except OSError as exc:
            logger.warning("Could not read %s: %s", jsonl_path, exc)

    json_path = queue_dir / ANSWERS_JSON
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read %s: %s", json_path, exc)
            data = None
        if isinstance(data, dict):
            for key, value in data.items():
                entry = _entry_from_mapping(key, value)
                if entry is not None:
                    answers.setdefault(entry[0], entry[1])
        elif isinstance(data, list):
            for record in data:
                entry = _answer_entry(record)
                if entry is not None:
                    answers.setdefault(entry[0], entry[1])
    return answers


def _entry_from_mapping(job_id: str, value: Any) -> Optional[Tuple[str, str]]:
    """Normalize one ``answers.json`` mapping entry into ``(job_id, response)``.

    ``{"<job_id>": "raw json string"}``, ``{"<job_id>": {"response": ...}}`` and
    ``{"<job_id>": {<field>: <value>}}`` are all accepted; in the last form the
    whole object is the response payload.
    """
    if isinstance(value, dict):
        entry = _answer_entry(value, job_id)
        if entry is not None:
            return entry
        return job_id, json.dumps(value, ensure_ascii=False)
    return _answer_entry({"response": value}, job_id)


def _answer_entry(record: Any, fallback_id: Optional[str] = None) -> Optional[Tuple[str, str]]:
    """Normalize one answer record into ``(job_id, response)``."""
    if not isinstance(record, dict):
        return None
    job_id = record.get("job_id") or record.get("id") or fallback_id
    if not isinstance(job_id, str) or not job_id:
        return None
    for key in _ANSWER_KEYS:
        value = record.get(key)
        if isinstance(value, str):
            return job_id, value
        if value is not None and not isinstance(value, (dict, list)):
            return job_id, str(value)
        if isinstance(value, (dict, list)):
            return job_id, json.dumps(value, ensure_ascii=False)
    return None


def read_requests(queue_dir: Path) -> List[Dict[str, Any]]:
    """Return the recorded requests (one dict per line, malformed lines skipped)."""
    path = queue_dir / REQUESTS_FILE
    if not path.is_file():
        return []
    records: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
    return records


def queue_stats(queue_dir: Path) -> Dict[str, int]:
    """Return ``requested`` / ``answered`` / ``pending`` job counts."""
    requests = read_requests(queue_dir)
    requested = {str(r.get("job_id")) for r in requests if r.get("job_id")}
    answers = load_answers(queue_dir)
    answered = {job_id for job_id in requested if job_id in answers}
    return {
        "requested": len(requested),
        "answered": len(answered),
        "pending": len(requested) - len(answered),
        "answer_records": len(answers),
    }


# ---------------------------------------------------------------------------
# 队列引擎
# ---------------------------------------------------------------------------


class BridgeQueue:
    """On-disk request/answer queue engine (skill-agnostic).

    Encapsulates the queue state machine: answer-store caching, request
    recording (deduplicated, thread-safe) and the collect/apply/strict
    response policy. Provider adapters subclass or compose this and translate
    ``MissingAnswerError`` into their own error type.
    """

    def __init__(
        self,
        model: str = "host-agent",
        queue_dir: Optional[str] = None,
        mode: Optional[str] = None,
        *,
        default_queue_dir: str = DEFAULT_QUEUE_DIR,
    ):
        self.model = model
        self.queue_dir = resolve_queue_dir(queue_dir, default=default_queue_dir)
        self.mode = resolve_mode(mode)
        self.requests_path = self.queue_dir / REQUESTS_FILE
        self._lock = threading.Lock()
        self._recorded: Optional[set] = None
        self._answers: Dict[str, str] = {}
        self._answers_stamp: Optional[Tuple[float, float]] = None
        self.missing_jobs: List[str] = []

    # -- answer store ----------------------------------------------------
    def _current_answers(self) -> Dict[str, str]:
        """Reload the answer store when its files changed on disk."""
        stamp = self._answers_mtime()
        if self._answers_stamp != stamp:
            self._answers = load_answers(self.queue_dir)
            self._answers_stamp = stamp
        return self._answers

    def _answers_mtime(self) -> Tuple[float, float]:
        def mtime(name: str) -> float:
            try:
                return (self.queue_dir / name).stat().st_mtime
            except OSError:
                return 0.0

        return (mtime(ANSWERS_JSONL), mtime(ANSWERS_JSON))

    # -- request log -----------------------------------------------------
    def _known_ids(self) -> set:
        if self._recorded is None:
            self._recorded = {
                str(record.get("job_id"))
                for record in read_requests(self.queue_dir)
                if record.get("job_id")
            }
        return self._recorded

    def _record_request(self, record: Dict[str, Any]) -> None:
        """Append one request to ``requests.jsonl`` exactly once per process."""
        job_id = str(record["job_id"])
        with self._lock:
            if job_id in self._known_ids():
                return
            try:
                self.queue_dir.mkdir(parents=True, exist_ok=True)
                with self.requests_path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
                self._known_ids().add(job_id)
            except OSError as exc:
                logger.warning(
                    "bridge: cannot record LLM request in %s: %s", self.queue_dir, exc
                )

    # -- request handling -------------------------------------------------
    def handle_chat(
        self,
        messages: List[Dict[str, str]],
        response_format: Optional[Dict[str, str]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Serve one request from the answer store, recording it when unanswered.

        Raises:
            MissingAnswerError: in ``strict`` mode when the job has no answer yet.
        """
        job_id = job_id_for(
            messages=messages,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
            model=self.model,
        )
        answer = self._current_answers().get(job_id)
        if answer is not None:
            return answer

        self._record_request(
            {
                "job_id": job_id,
                "kind": classify_request(messages),
                "model": self.model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": response_format,
                "messages": messages,
                "requested_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
        )
        self.missing_jobs.append(job_id)

        if self.mode == _MODE_STRICT:
            raise MissingAnswerError(
                f"agent_bridge: job {job_id} has no answer in {self.queue_dir} "
                f"(mode=strict). Answer {len(set(self.missing_jobs))} pending job(s) first."
            )

        logger.debug("bridge: job %s unanswered; returning placeholder", job_id)
        return placeholder_for(messages)
