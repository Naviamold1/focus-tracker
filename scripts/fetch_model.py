"""Download the local-tier embedding model (all-MiniLM-L6-v2 ONNX + tokenizer) into models/."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from huggingface_hub import hf_hub_download

REPO = "sentence-transformers/all-MiniLM-L6-v2"
FILES = {"onnx/model.onnx": "model.onnx", "tokenizer.json": "tokenizer.json"}


def fetch(dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for remote, local in FILES.items():
        shutil.copyfile(hf_hub_download(REPO, remote), dest / local)
        print(f"ok {dest / local}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dest", type=Path, default=Path(__file__).resolve().parents[1] / "models"
    )
    fetch(parser.parse_args().dest)
