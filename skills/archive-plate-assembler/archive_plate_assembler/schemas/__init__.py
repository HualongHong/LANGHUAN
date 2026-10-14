"""全链路 JSON Schema 定义与校验（单一事实来源）。

对外 Schema 见《Archive-Plate-Assembler 技能初始设定书》§2.2；
中间态为其扩展内部结构，导出前裁剪为对外 Schema。
"""

from .species import (
    ImageType,
    SpeciesImage,
    SpeciesRecord,
    SpeciesTable,
    Taxonomy,
    export_json,
    validate_species_list,
)

__all__ = [
    "ImageType",
    "SpeciesImage",
    "SpeciesRecord",
    "SpeciesTable",
    "Taxonomy",
    "export_json",
    "validate_species_list",
]
