### Config file

import os


def _read_env_value(path, key):
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip("'").strip('"')
    raise KeyError(f"'{key}' not found in {path}")


_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")

usda_key = _read_env_value(_ENV_PATH, "usda_key")
