---
name: GitLab CI Speedup
description: Audit real GitLab CI runs to find long-pole jobs and wasted runner minutes, then plan or implement measured fixes to .gitlab-ci.yml. Use when pipelines are slow, merges wait too long, CI cost is high, or the user asks to optimize GitLab CI. Do not use to author a pipeline from scratch or for security-only audits.
license: MIT
metadata:
  version: 0.2.0
  upstream: starslingdev/skills ci-speedup, mesilov/gitlab-ci-performance-skill, GitLab Well-Architected gitlab-ci-skill
---

# GitLab CI Speedup

Diagnose from real runs, change one thing at a time, and verify with the same measurement.

## Operating contract

- **Read-only by default.** Do not retry, cancel, run, or mutate pipelines, runners, or project settings until the user approves a specific fix.
- **Evidence or it did not happen.** Every finding cites a pipeline ID, job ID, or YAML line. Mark each number as measured or estimated and state confidence.
- **Never weaken a gate silently.** Check protected branches, required checks, approval rules, and compliance scans before proposing removal or relocation.
- **Redact secrets** from logs, variables, URLs, and reports.
- **Say when data is thin.** Fewer than 10 merge-gating runs means low confidence; fewer than 3 means static analysis only.
- If `glab` or network access is unavailable, do a static pass over the YAML and label it as such.

## Modes

- **Audit (default):** read-only. Collect, measure, diagnose, report, and stop.
- **Implement (explicit opt-in):** only when the user says to implement/apply/fix a named finding. Follow `references/implementation-playbook.md`, one finding at a time.
- **Compare:** re-collect after a change and compare with `scripts/compare.py`.

A request such as “why is CI slow?” or “audit the pipeline” is audit mode, not permission to edit YAML. Ask before switching modes.

## 1. Preflight

Identify the repository, GitLab host, and the wait that actually matters.

```bash
git remote -v
glab auth status
glab repo view --output json | jq '{id,path_with_namespace,default_branch,web_url}'
command -v glab jq python3 docker gitlab-ci-local glci
```

Locate the CI entrypoint and its local includes:

```bash
ls -la .gitlab-ci.yml
grep -nE '^[[:space:]]*(include|stages|workflow|default|variables):' .gitlab-ci.yml
find . -maxdepth 3 \( -name '.gitlab-ci.yml' -o -name '*.gitlab-ci*.yml' \) -print
```

Remote `include:` files are resolved by GitLab. You may need `glab api` or `glab ci lint` to see the merged configuration.

Confirm the target before sampling:

- Merge-gating wait: MR pipelines (`merge_request_event`) that block merging.
- Main-branch wait: `push` pipelines on the default branch.
- Cost: all pipelines, including schedules and child/downstream pipelines.
- A specific pipeline source or ref if the user named one.

## 2. Collect real runs

Prefer the bundled collector. It is read-only and uses `glab api`.

First resolve `SKILL_DIR` as the directory containing this `SKILL.md` (the skill tool reports it as the base directory). Then run from the repository root:

```bash
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT=".gitlab-ci-speedup/data/$STAMP"
bash "$SKILL_DIR/scripts/collect.sh" --limit 20 --out "$OUT"
python3 "$SKILL_DIR/scripts/analyze.py" "$OUT" --json > "$OUT/metrics.json"
python3 "$SKILL_DIR/scripts/analyze.py" "$OUT"
```

Useful options:

- `--ref <branch>` to focus on one ref.
- `--source merge_request_event|push|schedule|web|parent_pipeline` to focus on one pipeline type.
- `--limit <n>` to increase the sample (20 is a reasonable default; 30+ for noisy pipelines).

If the scripts cannot run, fall back to the commands in `references/measurement.md`. Do not guess durations from YAML alone.

## 3. Measure the right things

Read `references/measurement.md` for field definitions and traps. In short:

- **Developer wait**: `pipeline.created_at` to the finish of the last required (non-`allow_failure`) job. This includes queue time, because the developer waits through it.
- **Run span**: first job start to last job finish. Useful when queue time is owned by a different team.
- **Queue delay**: first job start minus pipeline creation. High queue time means runner capacity or tag problems, not slow scripts.
- **Runner minutes**: sum of job `duration`; report retries separately. Sharding can improve wait while increasing runner minutes.
- **Critical path / long poles**: the chain of jobs that determines the finish time. Use `needs:` plus stage barriers for the structural graph, and timestamped job runs to validate which jobs actually sit on the path.
- Report **p50 and p90**, not averages. Exclude `skipped`, `canceled`, `manual`, and superseded pipelines from wait statistics; include their cost only when relevant.
- Analyze pipeline sources separately. Mixing scheduled, push, and MR pipelines produces meaningless averages.
- Child and downstream pipelines: include the bridge job and downstream wall clock, but do not double-count nested job durations.

## 4. Diagnose

Read `references/pattern-catalog.md`. It contains the detection method, likely impact, safe fix, and GitLab-specific caveats for each pattern.

Rank findings in this order:

1. Measured p50 wall-clock impact on the merge-gating path.
2. Measured runner-minute or cost impact.
3. Cheap, low-risk hygiene with static evidence.
4. Speculative ideas with low confidence. Label these clearly and keep them out of the top three.

For each finding, require:

- **Pattern**: name from the catalog.
- **Evidence**: pipeline/job IDs, timestamps, YAML file and line.
- **Impact**: wall clock and runner minutes, with measured/estimated label and confidence.
- **Fix sketch**: the smallest safe change.
- **Risk and rollback**: what could break, and how to undo it.
- **Verification**: the exact local and remote checks.

Do not recommend a change merely because it is a best practice. Tie it to measured data or a concrete static defect.

## 5. Write the report

Default path: `.gitlab-ci-speedup/report.md`.

```markdown
# GitLab CI Speedup Report

- Project:
- Ref / commit:
- Date and sample window:
- Pipelines sampled: N (source breakdown)
- Overall confidence:

## Summary
Typical merge-gating wait (p50/p90), top long poles, estimated waste.

## Long poles
| Job | Stage | p50 | p90 | Queue p50 | On critical path | Evidence |

## Findings (ranked)
### F1. <pattern>
- Measured/estimated impact:
- Confidence:
- Evidence:
- Proposed change:
- Risk and rollback:
- Verification:

## Local-first plan
What can move local without weakening merge gates.

## Appendix
Commands used, pipeline IDs, raw data path, caveats.
```

Keep the raw collected data and metrics JSON so another session can reproduce the analysis. Add `.gitlab-ci-speedup/` to `.gitignore` only after asking.

## 6. Implement fixes (only after approval)

Implement mode is opt-in. Read `references/implementation-playbook.md` before editing; it has concrete diff shapes, verification steps, rollback, and caveats for the common patterns.

Work one finding at a time. For each change:

1. Restate the finding and the measured baseline.
2. Check protection and required checks:
   ```bash
   glab api "projects/:id/protected_branches"
   glab api "projects/:id/approval_rules"
   ```
3. Show the smallest diff and the invariants it preserves: the same tests still run, artifacts are still produced, deploy is still gated, cache keys stay correct.
4. Validate locally:
   ```bash
   bash "$SKILL_DIR/scripts/verify-local.sh"
   ```
   Use `--job <name> --run` only when you intend to execute a job locally.
5. Run the affected job in the same container image when possible.
6. Apply only after approval. Commit or push only if the user asks.
7. Re-collect 3-5 new pipelines and compare:
   ```bash
   python3 "$SKILL_DIR/scripts/analyze.py" "$AFTER_DIR" --json > "$AFTER_DIR/metrics.json"
   python3 "$SKILL_DIR/scripts/compare.py" "$BEFORE_METRICS" "$AFTER_DIR/metrics.json"
   ```
8. Revert on regression or if a required check stops running.

Do not batch unrelated pipeline changes. If a change cannot be verified locally and has no rollback, say so before editing. Never treat a CI speedup as a reason to delete tests, hide failures with `allow_failure`, or move compliance checks out of the required path.

## 7. Local-first loop

Read `references/local-first.md` before recommending that tests move local.

The short version: keep the remote pipeline thin, but never remove merge-blocking, compliance, or environment-dependent checks. Move the fast-feedback loop local, mirror the CI image and versions, keep a thin remote smoke test, and document one command such as `make ci-local` or `bin/ci` in the README and agent instructions.

For agentic workflows, the goal is a fast local loop, not a weaker remote gate.

## 8. Handoff

End with:

- The top three findings ranked by measured impact.
- What is uncertain or needs more samples.
- A direct question asking which finding to implement first.

Do not edit `.gitlab-ci.yml` in the same response as the initial audit unless the user explicitly asked for implementation.

## Bundled files

- `references/measurement.md` — API fields, formulas, sampling, critical-path method.
- `references/pattern-catalog.md` — GitLab-specific anti-patterns and safe fixes.
- `references/implementation-playbook.md` — opt-in diffs, verification, and rollback per pattern.
- `references/local-first.md` — safe local/remote split and guardrails.
- `scripts/collect.sh` — read-only pipeline/job collection via `glab api`.
- `scripts/analyze.py` — wall-clock, queue, runner-minute, and long-pole statistics.
- `scripts/compare.py` — before/after metrics comparison.
- `scripts/verify-local.sh` — lint, dry-run, and optional local job execution.

## Limitations

- Exact critical-path reconstruction needs the full `needs:` graph, including remote includes. Timestamp analysis gives an observed chain, which is usually enough to find long poles but is not a formal DAG proof.
- Step-level timing requires job logs with timestamps. Do not infer step costs from total job duration.
- Runner queue behavior depends on runner tags, capacity, and project settings. The skill can show queue time but cannot change runner infrastructure without explicit approval.
- This skill does not perform security posture review, runner billing analysis, or brand-new pipeline authoring.
