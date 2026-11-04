"""Load the three supplied templates locally; no generation or remote calls."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.generation.presets import load_editorial_prompts

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/source_articles.db")
    args = parser.parse_args()
    print(json.dumps({"loaded": load_editorial_prompts(args.db), "execution_enabled": False}, ensure_ascii=False))
