"""Tests for the CNKI plaintext parser."""

from __future__ import annotations

import pytest

from lit_scout_harvester.parsers.cnki_parser import (
    CNKIParseError,
    _detect_format,
    _parse_custom,
    _parse_refworks,
    parse_cnki_plaintext_to_list,
)


@pytest.fixture
def refworks_text():
    return """RT Journal Article
SR 1
A1 张明华
A1 李强
AD 北京大学教育学院
AD 清华大学教育研究院
T1 科学教育中学生概念转变的实证研究
JF 教育研究
YR 2022
FD 2022-05-15
K1 科学教育; 概念转变; 中学生; 实证研究
AB 本研究通过前后测实验，探讨了探究式教学对中学生科学概念转变的影响。
LA 中文
SN 1002-5731
CN 11-1281/G4
CF 国家自然科学基金(62077001)
CLC G633.7

RT Journal Article
SR 1
A1 王晓东
AD 南京师范大学地理科学学院
T1 过去一千年东亚季风区石笋记录的气候突变事件
JF 第四纪研究
YR 2021
K1 石笋; 东亚季风; 气候突变; 全新世
AB 本文综述了过去一千年东亚季风区高分辨率石笋氧同位素记录。
LA 中文
CLC P467
"""


@pytest.fixture
def custom_text():
    return """【题名】科学教育中学生概念转变的实证研究
【作者】张明华; 李强
【机构】北京大学教育学院; 清华大学教育研究院
【来源】教育研究
【年】2022
【关键词】科学教育; 概念转变; 中学生; 实证研究
【摘要】本研究通过前后测实验，探讨了探究式教学对中学生科学概念转变的影响。
【基金】国家自然科学基金(62077001)
【中图分类号】G633.7

【题名】过去一千年东亚季风区石笋记录的气候突变事件
【作者】王晓东
【机构】南京师范大学地理科学学院
【来源】第四纪研究
【年】2021
"""


def test_detect_format_refworks(refworks_text):
    assert _detect_format(refworks_text.splitlines()) == "refworks"


def test_detect_format_custom(custom_text):
    assert _detect_format(custom_text.splitlines()) == "custom"


def test_parse_refworks(refworks_text):
    records = list(_parse_refworks(refworks_text.splitlines()))
    assert len(records) == 2
    rec = records[0]
    assert rec["TI"] == "科学教育中学生概念转变的实证研究"
    assert rec["AU"] == ["张明华", "李强"]
    assert rec["AF"] == ["北京大学教育学院", "清华大学教育研究院"]
    assert rec["SO"] == "教育研究"
    assert rec["PY"] == "2022"
    assert rec["DE"] == ["科学教育", "概念转变", "中学生", "实证研究"]
    assert rec["LA"] == "zh"
    assert rec["raw_fund"] == "国家自然科学基金(62077001)"
    assert rec["raw_clc_class"] == "G633.7"

    rec2 = records[1]
    assert rec2["raw_clc_class"] == "P467"


def test_parse_custom(custom_text):
    records = list(_parse_custom(custom_text.splitlines()))
    assert len(records) == 2
    rec = records[0]
    assert rec["TI"] == "科学教育中学生概念转变的实证研究"
    assert rec["AU"] == ["张明华", "李强"]
    assert rec["SO"] == "教育研究"
    assert rec["PY"] == "2022"
    assert rec["DE"] == ["科学教育", "概念转变", "中学生", "实证研究"]
    assert rec["raw_fund"] == "国家自然科学基金(62077001)"
    assert rec["raw_clc_class"] == "G633.7"


def test_parse_cnki_plaintext_to_list(tmp_path, refworks_text):
    path = tmp_path / "sample.cnki.txt"
    path.write_text(refworks_text, encoding="utf-8")
    records = parse_cnki_plaintext_to_list(str(path))
    assert len(records) == 2
    assert records[0]["TI"]


def test_parse_cnki_file_not_found():
    with pytest.raises(CNKIParseError):
        parse_cnki_plaintext_to_list("nonexistent.cnki.txt")
