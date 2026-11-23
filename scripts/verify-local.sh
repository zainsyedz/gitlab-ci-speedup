#!/usr/bin/env bash
# Local verification for GitLab CI changes.
# Read-only by default: lint and list jobs. Use --run to execute one job locally.
set -euo pipefail

FILE=".gitlab-ci.yml"
JOB=""
RUN_JOB=0
SKIP_DRY_RUN=0

usage() {
  cat <<'EOF'
Usage: verify-local.sh [--file PATH] [--job JOB] [--run] [--skip-dry-run]

Validates a GitLab CI configuration locally without touching the remote project.

Options:
  --file PATH       CI config to validate (default: .gitlab-ci.yml)
  --job JOB         Job to execute when --run is used
  --run             Execute JOB with gitlab-ci-local after validation
  --skip-dry-run    Skip the pipeline-creation simulation
  -h, --help        Show this help

Notes:
  - glab ci lint --dry-run asks GitLab to simulate pipeline creation. It is
    read-only but requires network access and project permissions.
  - --run executes job scripts on this machine. Review it before using it.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --file)
      FILE="${2:?--file requires a value}"
      shift 2
      ;;
    --job)
      JOB="${2:?--job requires a value}"
      shift 2
      ;;
    --run)
      RUN_JOB=1
      shift
      ;;
    --skip-dry-run)
      SKIP_DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -f "$FILE" ]]; then
  echo "error: CI config not found: $FILE" >&2
  exit 1
fi

if [[ "$RUN_JOB" -eq 1 && -z "$JOB" ]]; then
  echo "error: --run requires --job JOB" >&2
  exit 2
fi

FAILED=0

step() {
  local label="$1"
  shift
  echo
  echo "== ${label} =="
  if "$@"; then
    echo "ok: ${label}"
  else
    echo "FAILED: ${label}" >&2
    FAILED=1
  fi
}

if command -v glab >/dev/null 2>&1; then
  step "glab ci lint $FILE" glab ci lint "$FILE"
  if [[ "$SKIP_DRY_RUN" -eq 0 ]]; then
    step "glab ci lint --dry-run $FILE" glab ci lint "$FILE" --dry-run
  fi
else
  echo "warning: glab is not installed; skipping remote lint" >&2
fi

if command -v gitlab-ci-local >/dev/null 2>&1; then
  step "gitlab-ci-local --list" gitlab-ci-local --list
  if [[ "$RUN_JOB" -eq 1 ]]; then
    echo
    echo "warning: running '$JOB' executes its script locally" >&2
    step "gitlab-ci-local $JOB" gitlab-ci-local "$JOB"
  fi
else
  echo "warning: gitlab-ci-local is not installed; skipping local job listing" >&2
fi

if command -v docker >/dev/null 2>&1 || command -v podman >/dev/null 2>&1; then
  echo
  echo "info: a container engine is available; use the same image as CI for parity"
else
  echo "warning: no container engine found; local parity may be incomplete" >&2
fi

echo
if [[ "$FAILED" -ne 0 ]]; then
  echo "verification failed; fix the errors above before committing" >&2
  exit 1
fi
echo "verification passed"
