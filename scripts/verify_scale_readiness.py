from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED_DIMENSIONS = (
    "pages",
    "semantic_spans",
    "assets",
    "footnote_links",
)


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path.name} must contain a JSON object")
    return payload


def verify_run(run_dir: Path) -> list[str]:
    ledger_path = run_dir / "integrity-ledger.json"
    if not ledger_path.exists():
        return ["missing integrity-ledger.json"]
    try:
        ledger = _load_json(ledger_path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"invalid integrity-ledger.json: {exc}"]

    errors: list[str] = []
    dimensions = ledger.get("dimensions")
    dimensions = dimensions if isinstance(dimensions, dict) else {}
    for name in REQUIRED_DIMENSIONS:
        dimension = dimensions.get(name)
        ratio = dimension.get("ratio") if isinstance(dimension, dict) else None
        if ratio != 1.0:
            errors.append(f"{name} coverage is {ratio!r}")

    failures = ledger.get("failures")
    failures = failures if isinstance(failures, dict) else {}
    for key, values in failures.items():
        if key == "unresolved_review":
            continue
        if not isinstance(values, list) or not values:
            continue
        preview = ", ".join(str(value) for value in values[:8])
        errors.append(f"{key}: {preview}")
    technical_ready = ledger.get("technical_ready", ledger.get("ready"))
    if technical_ready is not True and not errors:
        errors.append("integrity ledger is not ready")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify that Phase A runs satisfy the corpus-scale integrity contract."
    )
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    results = [
        {
            "run_dir": str(run_dir.expanduser().resolve()),
            "ready": not (errors := verify_run(run_dir.expanduser().resolve())),
            "errors": errors,
        }
        for run_dir in args.run_dirs
    ]
    if args.as_json:
        print(json.dumps({"runs": results}, ensure_ascii=False, indent=2))
    else:
        for result in results:
            status = "READY" if result["ready"] else "BLOCKED"
            print(f"{status}\t{result['run_dir']}")
            for error in result["errors"]:
                print(f"  - {error}")
    return 0 if all(result["ready"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
