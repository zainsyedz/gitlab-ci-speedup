#!/usr/bin/env bash
# Read-only GitLab CI history collector.
# Requires: glab (authenticated), jq, git.
set -euo pipefail

LIMIT=20
OUT=""
REF=""
SOURCE=""

usage() {
  cat <<'EOF'
Usage: collect.sh [--limit N] [--out DIR] [--ref REF] [--source SOURCE]

Collects recent pipelines and their jobs for the current GitLab repository.
Writes pipelines.json, pipelines-details.json, jobs.ndjson, jobs/<id>.json,
pipeline-<id>.json, and meta.json into DIR.

Options:
  --limit N       Number of recent pipelines to sample (default: 20)
  --out DIR       Output directory (default: .gitlab-ci-speedup/data/<UTC timestamp>)
  --ref REF       Only sample pipelines for this ref
  --source TYPE   Only sample this pipeline source (push, merge_request_event,
                  schedule, web, parent_pipeline, pipeline)
  -h, --help      Show this help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --limit)
      LIMIT="${2:?--limit requires a value}"
      shift 2
      ;;
    --out)
      OUT="${2:?--out requires a value}"
      shift 2
      ;;
    --ref)
      REF="${2:?--ref requires a value}"
      shift 2
      ;;
    --source)
      SOURCE="${2:?--source requires a value}"
      shift 2
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

command -v glab >/dev/null 2>&1 || {
  echo "error: glab is not installed or not on PATH" >&2
  exit 1
}
command -v jq >/dev/null 2>&1 || {
  echo "error: jq is not installed or not on PATH" >&2
  exit 1
}

if ! glab auth status >/dev/null 2>&1; then
  echo "error: glab is not authenticated. Run 'glab auth login' first." >&2
  exit 1
fi

PROJECT_JSON="$(glab repo view --output json 2>/dev/null)" || {
  echo "error: could not read the current GitLab project. Run this inside a GitLab clone." >&2
  exit 1
}

PROJECT_ID="$(jq -r '.id // empty' <<<"$PROJECT_JSON")"
PROJECT_PATH="$(jq -r '.path_with_namespace // empty' <<<"$PROJECT_JSON")"
DEFAULT_BRANCH="$(jq -r '.default_branch // empty' <<<"$PROJECT_JSON")"

if [[ -z "$PROJECT_ID" || -z "$PROJECT_PATH" ]]; then
  echo "error: glab did not return a project id/path" >&2
  exit 1
fi

if [[ -z "$OUT" ]]; then
  OUT=".gitlab-ci-speedup/data/$(date -u +%Y%m%dT%H%M%SZ)"
fi

mkdir -p "$OUT/jobs"

QUERY="projects/${PROJECT_ID}/pipelines?per_page=${LIMIT}&order_by=updated_at&sort=desc"
if [[ -n "$REF" ]]; then
  QUERY+="&ref=$(jq -rn --arg v "$REF" '$v|@uri')"
fi
if [[ -n "$SOURCE" ]]; then
  QUERY+="&source=$(jq -rn --arg v "$SOURCE" '$v|@uri')"
fi

echo "Collecting up to ${LIMIT} pipelines from ${PROJECT_PATH}..." >&2
glab api "$QUERY" > "$OUT/pipelines.json"

if ! jq -e 'type == "array" and length > 0' "$OUT/pipelines.json" >/dev/null 2>&1; then
  echo "error: no pipelines returned. Check the ref/source filters and permissions." >&2
  exit 1
fi

PIPELINE_IDS="$(jq -r '.[].id' "$OUT/pipelines.json")"

for pid in $PIPELINE_IDS; do
  echo "  pipeline ${pid}" >&2
  glab api "projects/${PROJECT_ID}/pipelines/${pid}" > "$OUT/pipeline-${pid}.json"
  glab api "projects/${PROJECT_ID}/pipelines/${pid}/jobs?per_page=100&include_retried=true" \
    > "$OUT/jobs/${pid}.json"
done

# Full pipeline details, one JSON array.
jq -s '.' "$OUT"/pipeline-*.json > "$OUT/pipelines-details.json"

# Normalized job records, one JSON object per line.
: > "$OUT/jobs.ndjson"
for f in "$OUT"/jobs/*.json; do
  [[ -e "$f" ]] || continue
  pid="$(basename "$f" .json)"
  jq -c --argjson pid "$pid" '
    .[] | {
      pipeline_id: $pid,
      job_id: .id,
      name: .name,
      stage: .stage,
      status: .status,
      duration: (.duration // 0),
      queued_duration: (.queued_duration // 0),
      started_at: .started_at,
      finished_at: .finished_at,
      allow_failure: (.allow_failure // false),
      retried: (.retried // false),
      tag_list: (.tag_list // []),
      runner: (.runner.description // ""),
      artifacts_expire_at: (.artifacts_expire_at // null)
    }
  ' "$f" >> "$OUT/jobs.ndjson"
done

GIT_SHA=""
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  GIT_SHA="$(git rev-parse HEAD 2>/dev/null || true)"
fi

jq -n \
  --arg project_id "$PROJECT_ID" \
  --arg project_path "$PROJECT_PATH" \
  --arg default_branch "$DEFAULT_BRANCH" \
  --arg ref "$REF" \
  --arg source "$SOURCE" \
  --arg git_sha "$GIT_SHA" \
  --arg generated_at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --argjson limit "$LIMIT" \
  --slurpfile pipelines "$OUT/pipelines.json" \
  '{
    project_id: $project_id,
    project_path: $project_path,
    default_branch: $default_branch,
    ref_filter: (if $ref == "" then null else $ref end),
    source_filter: (if $source == "" then null else $source end),
    git_sha: (if $git_sha == "" then null else $git_sha end),
    generated_at: $generated_at,
    requested_limit: $limit,
    pipelines_returned: ($pipelines[0] | length),
    data_files: {
      pipelines: "pipelines.json",
      pipeline_details: "pipelines-details.json",
      jobs: "jobs.ndjson",
      raw_jobs_dir: "jobs/"
    }
  }' > "$OUT/meta.json"

echo "Wrote $OUT" >&2
echo "  pipelines: $(jq length "$OUT/pipelines.json")" >&2
echo "  jobs: $(wc -l < "$OUT/jobs.ndjson" | tr -d ' ')" >&2
echo "Next: python3 analyze.py \"$OUT\"" >&2
