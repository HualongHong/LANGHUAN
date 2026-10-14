"""贯穿层：人工复核与修正界面（Web）。

M4a：并排对比、字段编辑、采纳模型 B 建议、操作日志与导出。

用法（job_dir 为管道产出目录，如 output/e2e_beilei_600dpi_v4/）：
    streamlit run archive_plate_assembler/review_ui/app.py -- <job_dir>
    python -m archive_plate_assembler.review_ui <job_dir>

也可用环境变量 APA_JOB_DIR 指定 job 目录。
store.py 为纯逻辑存储层，不依赖 streamlit，可独立单测。
"""
