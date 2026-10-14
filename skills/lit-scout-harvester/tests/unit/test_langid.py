"""Tests for src.lang.langid."""

from __future__ import annotations

from lit_scout_harvester.lang.langid import LanguageIdentifier


def test_detects_english_with_langdetect():
    lid = LanguageIdentifier(model_path="nonexistent/model.ftz", fallback=True)
    assert lid.identify("This is an English sentence.") == "en"


def test_detects_chinese_with_langdetect():
    lid = LanguageIdentifier(model_path="nonexistent/model.ftz", fallback=True)
    assert lid.identify("这是一段中文文本。") == "zh-cn"


def test_empty_text_returns_unknown():
    lid = LanguageIdentifier(model_path="nonexistent/model.ftz", fallback=True)
    assert lid.identify("") == "unknown"
    assert lid.identify("   ") == "unknown"


def test_batch_identification():
    lid = LanguageIdentifier(model_path="nonexistent/model.ftz", fallback=True)
    texts = ["Hello world.", "Bonjour le monde."]
    results = lid.identify_batch(texts)
    assert results[0] == "en"
    assert results[1] == "fr"
