#!/usr/bin/env bash
# Increment the application version by 0.001, commit it, then push.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "Working tree has uncommitted tracked changes. Commit or stash them before running this script." >&2
  git status --short >&2
  exit 1
fi

CURRENT_VERSION="$(tr -d '[:space:]' < VERSION)"
NEXT_VERSION="$(python3 - "$CURRENT_VERSION" <<'PY'
from decimal import Decimal
import sys

current = Decimal(sys.argv[1])
next_version = current + Decimal("0.001")
text = format(next_version.normalize(), "f")
print(text)
PY
)"

printf '%s\n' "$NEXT_VERSION" > VERSION
python3 - "$NEXT_VERSION" <<'PY'
from pathlib import Path
import sys

version = sys.argv[1]
path = Path("frontend-react/src/config/version.ts")
path.write_text(f"export const APP_VERSION = '{version}'\n", encoding="utf-8")
PY

git add VERSION frontend-react/src/config/version.ts
git commit -m "Bump version to ${NEXT_VERSION}"
git push origin "$(git branch --show-current)"
