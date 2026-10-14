#!/usr/bin/env python3
"""Download and configure a fastText language-identification model.

Usage:
    python scripts/download_fasttext_model.py
    python scripts/download_fasttext_model.py --model bin --timeout 120
    python scripts/download_fasttext_model.py --verify --update-config config/default.yaml
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from pathlib import Path
from typing import Optional
from urllib.request import urlopen
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lit_scout_harvester.core.config_loader import load_config

logger = logging.getLogger("download_fasttext_model")

MODELS = {
    "ftz": {
        "filename": "lid.176.ftz",
        "size_mb": 1,
        "url": "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.ftz",
    },
    "bin": {
        "filename": "lid.176.bin",
        "size_mb": 126,
        "url": "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin",
    },
}


def download_file(url: str, dest: Path, timeout: int = 60, chunk_size: int = 8192) -> None:
    """Download ``url`` to ``dest`` with progress logging."""
    logger.info("Downloading %s -> %s", url, dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        with urlopen(url, timeout=timeout) as response, dest.open("wb") as out:
            total = 0
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                out.write(chunk)
                total += len(chunk)
                if total % (256 * 1024) == 0:
                    logger.info("Downloaded %.2f MB", total / 1024 / 1024)
    except URLError as exc:
        logger.error("Download failed: %s", exc)
        if dest.exists():
            dest.unlink()
        raise


def verify_model(path: Path) -> None:
    """Try to load the downloaded model with fasttext."""
    try:
        import fasttext

        model = fasttext.load_model(str(path))
        labels, _ = model.predict("This is a test.")
        label = labels[0]
        if isinstance(label, bytes):
            label = label.decode("utf-8")
        logger.info("Verification OK: predicted language '%s'", label.replace("__label__", ""))
    except Exception as exc:
        logger.error("Verification failed: %s", exc)
        raise


def update_config(config_path: Path, model_path: Path) -> None:
    """Update the skeleton.language_model_path line in a YAML config file.

    The update is text-based so that environment-variable placeholders
    (e.g. ``${DEEPSEEK_API_KEY}``) are not resolved and written back to disk.
    """
    import re

    text = config_path.read_text(encoding="utf-8")
    pattern = re.compile(r"^(\s*language_model_path:\s*).*?$", re.MULTILINE)
    if pattern.search(text):
        new_text = pattern.sub(r"\1" + model_path.as_posix(), text)
    else:
        # Insert under skeleton section if not present.
        skeleton_pattern = re.compile(r"^(skeleton:.*?)(\n  [a-z_]+:)", re.MULTILINE | re.DOTALL)
        match = skeleton_pattern.search(text)
        if match:
            insert_pos = match.end(1) + 1
            new_text = (
                text[:insert_pos]
                + f"  language_model_path: {model_path.as_posix()}\n"
                + text[insert_pos:]
            )
        else:
            raise ValueError("Could not locate 'skeleton' section in config")
    config_path.write_text(new_text, encoding="utf-8")
    logger.info("Updated %s: language_model_path = %s", config_path, model_path)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download a fastText language-identification model."
    )
    parser.add_argument(
        "--model",
        choices=["ftz", "bin"],
        default="ftz",
        help="Model variant to download (default: ftz, small and fast)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data/cache"),
        help="Directory where the model will be saved",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=120,
        help="Download timeout in seconds",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Verify the downloaded model can be loaded by fasttext",
    )
    parser.add_argument(
        "--update-config",
        type=Path,
        default=None,
        help="Update a YAML config file to point to the downloaded model",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if the file already exists",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    info = MODELS[args.model]
    dest = args.output_dir / info["filename"]

    if dest.exists() and not args.force:
        logger.info("Model already exists at %s; use --force to re-download", dest)
    else:
        logger.info(
            "Downloading fastText language model '%s' (~%d MB)",
            info["filename"],
            info["size_mb"],
        )
        try:
            download_file(info["url"], dest, timeout=args.timeout)
        except URLError:
            return 1

    if args.verify:
        try:
            verify_model(dest)
        except Exception:
            return 1

    if args.update_config:
        try:
            update_config(args.update_config, dest)
        except Exception as exc:
            logger.error("Failed to update config: %s", exc)
            return 1

    logger.info("Model ready at %s", dest)
    logger.info("To enable fastText language detection, make sure 'language_detection: true' in your config.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
