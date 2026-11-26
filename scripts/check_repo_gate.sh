#!/usr/bin/env bash
# Repository gate for jcheng-applications. Run locally or from CI.
#
#   bash scripts/check_repo_gate.sh
#
# It checks the required document set, the absence of a LICENSE file, secrets,
# run databases, operator env files, model weights and recordings, oversized
# files, relative links and the tests. It touches no robot, opens no device,
# joins no DDS domain and makes no network request.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail=0
require_file() {
  if [[ ! -f "$1" ]]; then
    echo "MISSING: $1" >&2
    fail=1
  fi
}

for f in \
  README.md \
  README.zh-CN.md \
  ROADMAP.md \
  CHANGELOG.md \
  ACCEPTANCE.md \
  LICENSE-REVIEW.md \
  VERSION \
  .gitignore \
  pyproject.toml \
  capability.yaml \
  docs/ARCHITECTURE.md \
  docs/INTEGRATION.md \
  docs/RECIPES.md \
  recipes/example-supervised-transfer.yaml \
  src/jcheng_applications/__init__.py \
  src/jcheng_applications/model.py \
  src/jcheng_applications/gateway.py \
  src/jcheng_applications/runner.py \
  src/jcheng_applications/cli.py \
  tests/test_contract.py \
  tests/test_no_hardware.py \
  tests/test_recipe.py \
  tests/test_gateway.py \
  tests/test_runner.py \
  tests/test_cli.py \
  scripts/check_repo_gate.sh \
  evidence/20261126_scaffold/README.md \
  .github/CODEOWNERS \
  .github/workflows/ci.yml
do
  require_file "$f"
done

# The organization license review is open: no LICENSE file yet.
if [[ -e LICENSE ]]; then
  echo "ERROR: LICENSE must not exist before the license review is approved." >&2
  fail=1
fi

# No credentials. This script is excluded because it contains the patterns.
if grep -RIqE \
  --exclude-dir=.git \
  --exclude='check_repo_gate.sh' \
  'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----' \
  . ; then
  echo "ERROR: possible secret material found." >&2
  fail=1
fi

# No model weights, run databases, recordings or operator env files.
if find . -path ./.git -prune -o \
    \( -name '*.pt' -o -name '*.onnx' -o -name '*.engine' \
       -o -name '*_openvino_model' -o -name '*.bag' -o -name '*.mcap' \
       -o -name '*.db3' -o -name '*.mp4' -o -name '*.avi' \
       -o -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \
       -o -name '*.env' \) -print \
    | grep -q .; then
  echo "ERROR: model weights, run data or operator configuration found; keep them outside the repository." >&2
  fail=1
fi

# No dataset, model, run or export store inside the repository.
for d in datasets models data runs exports; do
  if [[ -d "$d" ]] && find "$d" -type f -print | grep -q .; then
    echo "ERROR: $d/ must stay outside the repository." >&2
    fail=1
  fi
done

# No oversized files (catches accidental blobs before they enter history).
big="$(find . -path ./.git -prune -o -type f -size +5M -print)"
if [[ -n "$big" ]]; then
  echo "ERROR: files larger than 5 MB:" >&2
  echo "$big" >&2
  fail=1
fi

# Relative Markdown links must resolve from the file that contains them.
while IFS= read -r f; do
  dir="$(dirname "$f")"
  while IFS= read -r link; do
    case "$link" in
      http://*|https://*|mailto:*|'#'*|'') continue ;;
    esac
    target="${link%%#*}"
    [[ -z "$target" ]] && continue
    if [[ ! -e "$dir/$target" ]]; then
      echo "BROKEN LINK in $f: $link" >&2
      fail=1
    fi
  done < <(grep -oE '\]\([^)]+\)' "$f" | sed -e 's/^](//' -e 's/)$//')
done < <(find . -path ./.git -prune -o -name '*.md' -print)

if [[ "$fail" -ne 0 ]]; then
  echo "Repository gate FAILED" >&2
  exit 1
fi

echo "== tests"
python3 -m pytest -q

echo "Repository gate OK: document set, manifest, no LICENSE, no secrets, no weights, no run data, links resolve"
