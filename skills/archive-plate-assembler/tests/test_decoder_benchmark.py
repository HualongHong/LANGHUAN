"""Decoder A/B benchmark with fake MinerU and model gateway."""

from __future__ import annotations

import json

import pytest

from agentkit.mineru import MinerUDoc, MinerUError
from archive_plate_assembler.model_gateway import MockGateway
from archive_plate_assembler.schemas.internal import LayoutRules, PageAsset
from archive_plate_assembler.layout_analyzer import save_layout_rules
from scripts import decoder_benchmark as benchmark


class FakeDecoder:
    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def parse_file(self, path, mode="auto"):
        self.calls.append((path, mode))
        if self.error:
            raise self.error
        return MinerUDoc(
            mode="token",
            markdown="full text",
            content_list_json=json.dumps([
                {"type": "text", "text": "第一页文本", "page_idx": 0},
                {"type": "text", "text": "第二页文本", "page_idx": 1},
                {"type": "image", "img_path": "images/x.png", "page_idx": 1},
            ], ensure_ascii=False),
        )


def _gateway_factory(snapshot_dir):
    state = {"calls": 0}

    def respond(prompt):
        state["calls"] += 1
        first = state["calls"] == 1
        return {
            "page_has_species": True,
            "entries": [{
                "chinese_name": "甲" if first else "乙",
                "scientific_name": "Species alpha" if first else "Species beta",
                "description": "合成描述。",
            }],
        }

    return MockGateway(respond)


def _job(tmp_path):
    job = tmp_path / "job"
    job.mkdir()
    save_layout_rules(LayoutRules(template_description="合成版式", entry_boundary_rules=[]), str(job))
    pdf = tmp_path / "book_1.pdf"
    pdf.write_bytes(b"%PDF-fake")
    pages = [
        PageAsset(page_number=1, image_path=str(job / "page_001.png"), source_pdf=str(pdf)),
        PageAsset(page_number=2, image_path=str(job / "page_002.png"), source_pdf=str(pdf)),
    ]
    (job / "pages_index.json").write_text(
        json.dumps([page.model_dump(mode="json") for page in pages], ensure_ascii=False),
        encoding="utf-8",
    )
    gold = tmp_path / "corrected_species.json"
    gold.write_text(json.dumps([
        {"chinese_name": "甲", "scientific_name": "Species alpha", "page": 1,
         "description": "合成描述。", "source_book": "job"},
        {"chinese_name": "乙", "scientific_name": "Species beta", "page": 2,
         "description": "合成描述。", "source_book": "job"},
    ], ensure_ascii=False), encoding="utf-8")
    return job, pdf, gold


def test_text_only_skips_only_verified_text_page(tmp_path):
    job, pdf, gold = _job(tmp_path)
    report = benchmark.run_benchmark(
        str(job), [str(pdf)], str(gold), str(tmp_path / "benchmark"),
        mode="token", gateway_factory=_gateway_factory, decoder_client=FakeDecoder(),
    )
    assert report["decoder"]["ocr_pages"] == [1, 2]
    assert report["decoder"]["text_only_eligible_pages"] == [1]
    assert report["modes"]["vision"]["vision_calls"] == 2
    assert report["modes"]["text_only"]["vision_calls"] == 1
    assert report["comparison"]["vision_calls_saved"] == 1
    assert report["comparison"]["vision_reduction_rate"] == 0.5
    assert report["comparison"]["overall_f1_delta"] == 0.0
    assert (tmp_path / "benchmark/vision/species.json").exists()
    assert (tmp_path / "benchmark/text_only/species.json").exists()
    assert (tmp_path / "benchmark/decoder_benchmark.json").exists()


def test_decoder_failure_does_not_claim_vision_savings(tmp_path):
    job, pdf, gold = _job(tmp_path)
    report = benchmark.run_benchmark(
        str(job), [str(pdf)], str(gold), str(tmp_path / "benchmark"),
        mode="token", gateway_factory=_gateway_factory,
        decoder_client=FakeDecoder(error=MinerUError("offline")),
    )
    assert report["decoder"]["documents"] == 0
    assert report["decoder"]["text_only_eligible_pages"] == []
    assert report["comparison"]["vision_calls_saved"] == 0
    assert report["modes"]["vision"]["vision_calls"] == 2
    assert report["modes"]["text_only"]["vision_calls"] == 2


def test_invalid_gold_is_rejected_before_model_or_decoder(tmp_path):
    job, pdf, gold = _job(tmp_path)
    gold.write_text(json.dumps([{"chinese_name": "缺拉丁名", "page": 1}]), encoding="utf-8")
    decoder = FakeDecoder()
    with pytest.raises(ValueError, match="黄金记录"):
        benchmark.run_benchmark(
            str(job), [str(pdf)], str(gold), str(tmp_path / "benchmark"),
            mode="token", gateway_factory=_gateway_factory, decoder_client=decoder,
        )
    assert decoder.calls == []
