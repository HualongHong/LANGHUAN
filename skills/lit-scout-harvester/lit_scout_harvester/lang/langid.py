"""Language identification for titles and abstracts.

The module prefers a fastText supervised model (e.g. ``lid.176.ftz``) when it
is available. If the model file is missing, it falls back to ``langdetect`` so
that development can continue without downloading large model files.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional, Union


class LanguageIdentificationError(Exception):
    """Raised when language identification cannot be performed."""


class LanguageIdentifier:
    """Detect the language of a text.

    Args:
        model_path: Path to a fastText ``.bin`` or ``.ftz`` model. If the file
            does not exist and ``fallback`` is True, ``langdetect`` is used.
        fallback: Whether to fall back to ``langdetect`` when fastText is not
            available.
    """

    def __init__(
        self,
        model_path: Optional[Union[str, Path]] = None,
        fallback: bool = True,
    ):
        self.model_path = Path(model_path) if model_path else None
        self._model: Optional[Any] = None
        self._use_langdetect = False

        if self.model_path and self.model_path.exists():
            try:
                import fasttext

                self._model = fasttext.load_model(str(self.model_path))
            except Exception as exc:
                if not fallback:
                    raise LanguageIdentificationError(
                        f"Failed to load fastText model {self.model_path}: {exc}"
                    ) from exc
                self._use_langdetect = True
        elif fallback:
            self._use_langdetect = True

    def identify(self, text: str) -> str:
        """Return the ISO 639-1 language code for ``text``.

        Returns ``"unknown"`` if detection fails or the input is empty.
        """
        if not text or not text.strip():
            return "unknown"

        cleaned = text.strip().replace("\n", " ")

        if self._model is not None:
            try:
                labels, _ = self._model.predict(cleaned)
                label = labels[0]
                if isinstance(label, bytes):
                    label = label.decode("utf-8")
                return label.replace("__label__", "")
            except Exception:
                # fastText can fail at predict time (e.g. fasttext-wheel is
                # incompatible with NumPy>=2); degrade to the fallback instead
                # of returning "unknown" for every record.
                self._model = None
                self._use_langdetect = True

        if self._use_langdetect:
            try:
                from langdetect import detect

                return detect(cleaned)
            except Exception:
                return "unknown"

        return "unknown"

    def identify_batch(self, texts: List[str]) -> List[str]:
        """Detect languages for a batch of texts."""
        return [self.identify(t) for t in texts]


