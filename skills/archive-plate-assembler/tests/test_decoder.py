"""decoder（APA × MinerU 对接层）单元测试：fake MinerUClient，零网络零视觉。"""

from __future__ import annotations

import json

import pytest

from agentkit.mineru import MinerUDoc, MinerUError

from archive_plate_assembler.decoder import (
    decode_documents,
    decoder_mode,
    text_only_enabled,
)
from archive_plate_assembler.decoder.core import DecodedDoc, mineru_tier_for
from archive_plate_assembler.extractor import build_extract_prompt, extract_page
from archive_plate_assembler.model_gateway import MockGateway
from archive_plate_assembler.schemas.internal import LayoutRules, PageAsset


def _rules() -> LayoutRules:
    return LayoutRules(
        template_description="左文右图",
        entry_boundary_rules=["以加粗中文名开始新条目"],
    )


def _page(n: int = 12) -> PageAsset:
    return PageAsset(page_number=n, image_path="data/samples/p.png", source_pdf="book.pdf")


CONTENT_LIST = [
    {"type": "text", "text": "虎纹蛙 描述正文……", "page_idx": 0},
    {"type": "image", "img_path": "images/x.png", "page_idx": 0},
    {"type": "text", "text": "纯文字页内容……", "page_idx": 1},
    {"type": "table", "text": "量度表 头体长 50mm", "page_idx": 2},
]


def _decoded_doc(tmp_path, content_list=None) -> DecodedDoc:
    src = tmp_path / "book.pdf"
    src.write_bytes(b"%PDF-fake")
    cl = content_list if content_list is not None else CONTENT_LIST
    mineru_doc = MinerUDoc(
        mode="token",
        markdown="# md",
        task_id="t1",
        content_list_json=json.dumps(cl, ensure_ascii=False),
    )
    fake_client = type("FC", (), {"parse_file": lambda self, path, mode="auto": mineru_doc})()
    out = decode_documents([str(src)], mode="auto", client=fake_client)
    return out[str(src)]


class TestModeSwitch:
    def test_default_auto(self, monkeypatch):
        monkeypatch.delenv("APA_DECODER", raising=False)
        assert decoder_mode() == "auto"

    def test_local_and_aliases(self, monkeypatch):
        monkeypatch.setenv("APA_DECODER", "local")
        assert decoder_mode() == "local"
        monkeypatch.setenv("APA_DECODER", "mineru-local")
        assert mineru_tier_for(decoder_mode()) == "local"
        monkeypatch.setenv("APA_DECODER", "Flash")
        assert decoder_mode() == "flash"

    def test_invalid_falls_back_auto(self, monkeypatch):
        monkeypatch.setenv("APA_DECODER", "bogus")
        assert decoder_mode() == "auto"

    def test_text_only_flag(self, monkeypatch):
        monkeypatch.delenv("APA_DECODER_TEXT_ONLY", raising=False)
        assert text_only_enabled() is False
        monkeypatch.setenv("APA_DECODER_TEXT_ONLY", "1")
        assert text_only_enabled() is True

    def test_local_mode_zero_requests(self, tmp_path, monkeypatch):
        def _boom(*a, **k):
            raise AssertionError("local 模式不应构造 MinerUClient")

        monkeypatch.setattr("archive_plate_assembler.decoder.core.MinerUClient", _boom)
        assert decode_documents(["whatever.pdf"], mode="local") == {}


class TestDecodeDocuments:
    def test_happy_path_returns_decoded(self, tmp_path):
        doc = _decoded_doc(tmp_path)
        assert doc.mode == "token"
        assert doc.task_id == "t1"

    def test_default_client_receives_quota_state_dir(self, tmp_path, monkeypatch):
        src = tmp_path / "book.pdf"
        src.write_bytes(b"%PDF-fake")
        seen = {}

        class FakeClient:
            def parse_file(self, path, mode="auto"):
                return MinerUDoc(mode="flash", markdown="# md")

        def factory(state_dir=None):
            seen["state_dir"] = state_dir
            return FakeClient()

        monkeypatch.setattr("archive_plate_assembler.decoder.core.MinerUClient", factory)
        out = decode_documents([str(src)], mode="auto", state_dir=tmp_path / "quota_state")
        assert out[str(src)].mode == "flash"
        assert seen["state_dir"] == tmp_path / "quota_state"

    def test_failure_falls_back_to_none(self, tmp_path):
        src = tmp_path / "book.pdf"
        src.write_bytes(b"%PDF-fake")
        failing = type("FC", (), {
            "parse_file": lambda self, path, mode="auto": (_ for _ in ()).throw(MinerUError("all tiers failed"))
        })()
        assert decode_documents([str(src)], mode="auto", client=failing) == {}

    def test_page_mapping(self, tmp_path):
        doc = _decoded_doc(tmp_path)
        texts = doc.page_texts()
        assert "虎纹蛙 描述正文……" in texts[0]
        assert "images/x.png" not in texts[0]  # 图片块不进文本
        assert texts[1] == "纯文字页内容……"
        assert "量度表" in texts[2]
        book = doc.book_page_map()
        assert book[doc.page_start] == texts[0]

    def test_page_has_images(self, tmp_path):
        doc = _decoded_doc(tmp_path)
        assert doc.page_has_images(0) is True
        assert doc.page_has_images(1) is False
        # 无 content_list 时保守为 True
        bare = DecodedDoc(source_pdf="x", mode="flash", markdown="# m")
        assert bare.page_has_images(0) is True
        assert bare.page_texts() == {}


class TestExtractPageIntegration:
    def _gateway(self):
        return MockGateway(lambda prompt: {"entries": [], "page_has_species": False})

    def test_ocr_text_reaches_prompt_and_keeps_image(self):
        gw = self._gateway()
        extract_page(gw, _page(), _rules(), ocr_text="虎纹蛙 描述正文……")
        prompt = gw.calls[0]["prompt"]
        assert "【本页 OCR 参考文本】" in prompt
        assert "虎纹蛙 描述正文……" in prompt
        assert gw.calls[0]["image_count"] == 1

    def test_prompt_unchanged_without_ocr_text(self):
        gw = self._gateway()
        extract_page(gw, _page(), _rules())
        baseline = build_extract_prompt(_rules(), 12)
        assert gw.calls[-1]["prompt"] == baseline

    def test_text_only_skips_image(self):
        gw = self._gateway()
        extract_page(gw, _page(), _rules(), ocr_text="纯文字", text_only=True)
        assert gw.calls[0]["image_count"] == 0
