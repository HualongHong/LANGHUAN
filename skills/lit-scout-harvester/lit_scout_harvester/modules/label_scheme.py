"""Label-scheme statistics for multi-label fields (feedback capsule C7).

For each multi-label field, the number of labels N per record should come from
the **corpus statistics**, never from the model (asking the model "how many
labels" is empirically uncorrelated with the truth). This module computes,
from gold / historical annotations:

* the distribution of label counts N,
* the mode N* and its share of the corpus,
* a three-tier recommendation:
    - ``mode``     — mode share >= ``mode_threshold`` (default 0.60): use the
                     mode N* directly;
    - ``degraded`` — share >= ``degraded_threshold`` (default 0.45): the mode
                     degrades; prefer a probability threshold or per-subgroup
                     modes;
    - ``unfit``    — share < ``degraded_threshold``: the field is unfit for
                     mode-N truncation.

Thresholds are constructor parameters, not hard-coded constants, so each
corpus can calibrate its own decision line (feedback capsule C7: the 60/45
cuts are empirical defaults from one project, not laws).

Also provides :meth:`fill_mode_n` to write the computed ``modeN`` back into a
schema's ``multiLabel`` fields, so stage B (``decision_select``) can read N*
straight from the schema.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from lit_scout_harvester.core.context import PipelineContext
from lit_scout_harvester.modules.base import ExtractionModule


class LabelSchemeAnalyzer:
    """Compute label-count distributions and mode-N recommendations."""

    name = "label_scheme"
    version = "0.1.0"

    def __init__(
        self,
        mode_threshold: float = 0.60,
        degraded_threshold: float = 0.45,
        separator: str = ";",
    ):
        if not 0.0 < degraded_threshold <= mode_threshold <= 1.0:
            raise ValueError(
                "Thresholds must satisfy 0 < degraded <= mode <= 1, got "
                f"mode={mode_threshold}, degraded={degraded_threshold}."
            )
        self.mode_threshold = mode_threshold
        self.degraded_threshold = degraded_threshold
        self.separator = separator

    # ------------------------------------------------------------------ #
    # value parsing
    # ------------------------------------------------------------------ #

    def _labels_of(self, value: Any) -> List[str]:
        """Extract the label list from a record value (list or separator string)."""
        if value is None:
            return []
        if isinstance(value, list):
            items = value
        else:
            items = str(value).split(self.separator)
        return [str(item).strip() for item in items if str(item).strip()]

    # ------------------------------------------------------------------ #
    # analysis
    # ------------------------------------------------------------------ #

    def analyze(
        self, records: Sequence[Dict[str, Any]], field: str
    ) -> Dict[str, Any]:
        """Compute the N distribution, mode N* and tier for ``field``.

        Only records carrying at least one label enter the N distribution
        (N* describes how many labels a *labeled* record has); records with
        zero labels are counted separately.

        Ties in the mode are broken toward the smaller N, deterministically.
        """
        distribution: Dict[int, int] = {}
        records_empty = 0
        for record in records:
            labels = self._labels_of(record.get(field))
            if not labels:
                records_empty += 1
                continue
            n = len(labels)
            distribution[n] = distribution.get(n, 0) + 1

        total = sum(distribution.values())
        if total == 0:
            return {
                "field": field,
                "records_total": len(records),
                "records_empty": records_empty,
                "distribution": {},
                "mode_n": None,
                "mode_count": 0,
                "mode_share": 0.0,
                "tier": "unfit",
                "recommendation": (
                    f"字段 '{field}' 没有任何带标签的记录，无法统计众数 N*；"
                    "请先准备 gold/历史标注。"
                ),
            }

        mode_n = min(n for n, c in distribution.items() if c == max(distribution.values()))
        mode_count = distribution[mode_n]
        mode_share = mode_count / total

        if mode_share >= self.mode_threshold:
            tier = "mode"
            recommendation = (
                f"字段 '{field}': N*={mode_n}, 众数占比 {mode_share:.1%} "
                f">= {self.mode_threshold:.0%}，可直接用众数 N* 截断。"
            )
        elif mode_share >= self.degraded_threshold:
            tier = "degraded"
            recommendation = (
                f"字段 '{field}': N*={mode_n}, 众数占比 {mode_share:.1%} 处于 "
                f"[{self.degraded_threshold:.0%}, {self.mode_threshold:.0%}) 退化档；"
                "建议改用概率阈值或按子群取众数。"
            )
        else:
            tier = "unfit"
            recommendation = (
                f"字段 '{field}': N*={mode_n}, 众数占比 {mode_share:.1%} < "
                f"{self.degraded_threshold:.0%}，标签数分布过于分散，"
                "不适合众数截断；瓶颈可能在概率排序或标签体系本身。"
            )

        return {
            "field": field,
            "records_total": len(records),
            "records_empty": records_empty,
            "distribution": dict(sorted(distribution.items())),
            "mode_n": mode_n,
            "mode_count": mode_count,
            "mode_share": mode_share,
            "tier": tier,
            "recommendation": recommendation,
        }

    def analyze_schema(
        self,
        schema: Dict[str, Any],
        records: Sequence[Dict[str, Any]],
        fields: Optional[Sequence[str]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Analyze every ``multiLabel`` field of a schema (or an explicit list)."""
        if fields is not None:
            targets = list(fields)
        else:
            targets = [
                f["name"]
                for f in schema.get("fields", [])
                if f.get("multiLabel") is True
            ]
        return {field: self.analyze(records, field) for field in targets}

    def fill_mode_n(
        self,
        schema: Dict[str, Any],
        records: Sequence[Dict[str, Any]],
        fields: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        """Write the computed ``modeN`` into the schema's multi-label fields.

        Mutates and returns ``schema`` so it can be persisted as the new
        schema version. Only fields with ``multiLabel: true`` are touched
        unless ``fields`` is given.
        """
        reports = self.analyze_schema(schema, records, fields=fields)
        by_name = {f["name"]: f for f in schema.get("fields", [])}
        for field, report in reports.items():
            spec = by_name.get(field)
            if spec is None:
                continue
            spec["multiLabel"] = True
            if report["mode_n"] is not None:
                spec["modeN"] = report["mode_n"]
        return schema


class LabelSchemeModule(ExtractionModule):
    """Pipeline adapter: runs the analyzer over the corpus before stage B.

    Not intended for the per-record pipeline loop; use ``LabelSchemeAnalyzer``
    directly on gold / historical annotations, then persist the annotated
    schema. ``process`` here simply analyzes the current record's raw fields
    for ad-hoc inspection.
    """

    name = "label_scheme"
    version = "0.1.0"

    def __init__(self, analyzer: Optional[LabelSchemeAnalyzer] = None):
        self.analyzer = analyzer or LabelSchemeAnalyzer()

    async def process(
        self, ctx: PipelineContext, config: Dict[str, Any]
    ) -> Dict[str, Any]:
        fields = config.get("fields", [])
        return {
            field: {"value": self.analyzer._labels_of(ctx.raw.get(field)), "confidence": 1.0}
            for field in fields
        }
