#!/usr/bin/env python3
"""Verify DeepL API key from pdf-translator/.env."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / ".env"


def _load_env() -> None:
    if not ENV_PATH.is_file():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def main() -> int:
    _load_env()
    auth_key = os.getenv("DEEPL_AUTH_KEY") or os.getenv("DEEPL_API_KEY")
    if not auth_key:
        print("ERROR: DEEPL_AUTH_KEY is not set in .env", file=sys.stderr)
        return 1
    base_url = os.getenv("DEEPL_BASE_URL", "https://api.deepl.com").rstrip("/")
    response = requests.get(
        f"{base_url}/v2/usage",
        headers={"Authorization": f"DeepL-Auth-Key {auth_key}"},
        timeout=(10, 30),
    )
    if response.status_code != 200:
        print(f"ERROR: DeepL usage check failed HTTP {response.status_code}: {response.text[:200]}", file=sys.stderr)
        return 1
    usage = response.json()
    print("OK: DeepL configured")
    print(f"  character_count: {usage.get('character_count')}")
    print(f"  character_limit: {usage.get('character_limit')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
