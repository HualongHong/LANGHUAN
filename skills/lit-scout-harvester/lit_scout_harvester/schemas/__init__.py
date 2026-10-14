"""Schema JSON helpers and the field-level key allow-list.

Schemas are plain JSON consumed by ``TermExtractorModule`` and the two-stage
decision architecture (``option_set_builder`` / ``decision_select``). Loading
is lenient — modules read field keys with ``dict.get`` — but this module is
the canonical place that enumerates which field keys exist, so strict
validators and schema builders share one allow-list.

Field keys:

* required: ``name``, ``type``, ``description``
* optional: ``guidelines``, ``examples``, ``max_items``, ``term_style``,
  ``max_words_per_term``
* optional, two-stage decision architecture (feedback capsule C7):
    - ``multiLabel`` (bool) — the field is a multi-label choice field; stage B
      truncates the probability ranking to the top N* instead of asking the
      model how many labels apply;
    - ``modeN`` (int) — the corpus mode label count N*, computed by
      ``modules/label_scheme.py`` and written back into the schema.
"""

from __future__ import annotations

from typing import Any, Dict, List

REQUIRED_FIELD_KEYS = ("name", "type", "description")

OPTIONAL_FIELD_KEYS = (
    "guidelines",
    "examples",
    "max_items",
    "term_style",
    "max_words_per_term",
    "multiLabel",
    "modeN",
)

ALLOWED_FIELD_KEYS = REQUIRED_FIELD_KEYS + OPTIONAL_FIELD_KEYS


def validate_schema(schema: Dict[str, Any]) -> List[str]:
    """Validate a schema dict and return a list of problems (empty = valid).

    Unknown field keys are reported as problems rather than silently ignored,
    so additions like ``multiLabel`` / ``modeN`` are registered here first.
    """
    problems: List[str] = []
    if not isinstance(schema, dict):
        return ["schema is not a JSON object"]
    if not schema.get("name"):
        problems.append("schema is missing 'name'")
    fields = schema.get("fields")
    if not isinstance(fields, list) or not fields:
        problems.append("schema is missing a non-empty 'fields' list")
        return problems
    for idx, field in enumerate(fields):
        if not isinstance(field, dict):
            problems.append(f"fields[{idx}] is not an object")
            continue
        for key in REQUIRED_FIELD_KEYS:
            if not field.get(key):
                problems.append(f"fields[{idx}] is missing required key '{key}'")
        for key in field:
            if key not in ALLOWED_FIELD_KEYS:
                problems.append(
                    f"fields[{idx}] has unknown key '{key}' "
                    f"(allowed: {', '.join(ALLOWED_FIELD_KEYS)})"
                )
        if field.get("multiLabel") is not None and not isinstance(
            field.get("multiLabel"), bool
        ):
            problems.append(f"fields[{idx}].multiLabel must be a boolean")
        mode_n = field.get("modeN")
        if mode_n is not None and (not isinstance(mode_n, int) or mode_n < 1):
            problems.append(f"fields[{idx}].modeN must be a positive integer")
    return problems
