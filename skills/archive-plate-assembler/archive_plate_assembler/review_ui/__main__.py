"""`python -m archive_plate_assembler.review_ui <job_dir>` 启动复核界面。"""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> None:
    if len(sys.argv) < 2:
        print("用法: python -m archive_plate_assembler.review_ui <job_dir>")
        sys.exit(1)
    job_dir = sys.argv[1]
    from streamlit.web.cli import main_run

    app_path = Path(__file__).parent / "app.py"
    sys.argv = ["streamlit", "run", str(app_path), "--", job_dir]
    main_run()


if __name__ == "__main__":
    main()
