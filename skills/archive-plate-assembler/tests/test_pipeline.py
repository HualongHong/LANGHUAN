"""管道编排的导入级冒烟测试（不调模型、不渲染 PDF）。

名字解析错误（如漏导入）只有在模块执行路径被走到时才暴露，
这里至少保证模块可导入且关键符号齐全。
"""

import archive_plate_assembler.pipeline as pipeline
from archive_plate_assembler.__main__ import main  # noqa: F401


def test_pipeline_public_symbols():
    assert callable(pipeline.run_pipeline)
    assert callable(pipeline.main)
    # 编排依赖的各节点函数必须已被正确导入
    for name in ("render_pdf", "analyze_layout", "extract_page",
                 "group_entries", "associate_orphan_images", "build_records",
                 "process_all", "verify_entry", "export_json",
                 "validate_species_list"):
        assert hasattr(pipeline, name), f"pipeline 缺少导入: {name}"
