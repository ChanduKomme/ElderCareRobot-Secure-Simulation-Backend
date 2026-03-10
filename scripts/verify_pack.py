#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.evidence import verify_evidence_payload  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python scripts/verify_pack.py <evidence_pack.json>")
        return 1

    path = Path(sys.argv[1])
    if not path.exists():
        print(f"File not found: {path}")
        return 1

    payload = json.loads(path.read_text())
    result = verify_evidence_payload(payload)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("valid") else 2


if __name__ == "__main__":
    raise SystemExit(main())
