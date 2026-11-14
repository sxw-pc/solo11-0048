"""Generate deterministic fixtures without importing the CLI."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jitterlens.fixturegen import generate_fixtures  # noqa: E402


def main() -> int:
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "fixtures" / "generated"
    paths = generate_fixtures(target)
    print(f"generated {len(paths)} fixtures in {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
