import tomllib
import time
from pathlib import Path

REPO = "/Users/hazemeissa/Projects/lumen"


def load_cfg() -> dict:
    with open(f"{REPO}/harness/config.toml", "rb") as f:
        return tomllib.load(f)


def new_run_dir() -> Path:
    rid = time.strftime("%Y%m%d-%H%M%S")
    d = Path(REPO) / "harness" / "reports" / rid
    d.mkdir(parents=True, exist_ok=True)
    return d
