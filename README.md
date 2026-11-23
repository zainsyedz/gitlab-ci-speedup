# GitLab CI Speedup

An OpenCode skill for auditing GitLab CI with real pipeline data, then making measured fixes.

It samples recent runs with `glab`, separates developer wait from runner cost, finds the long-pole jobs on the merge-gating path, checks a GitLab-specific anti-pattern catalog, and either reports or implements one verified fix at a time.

The default mode is **read-only**. Implementation is an explicit opt-in.

The skill is language-, framework-, and project-agnostic. It targets GitLab CI, and discovers each repository's own install, lint, build, test, and deploy commands. Examples use `<placeholders>` rather than assuming a package manager, test runner, database, or directory layout.

## Modes

| Mode | Trigger | Behavior |
| --- | --- | --- |
| Audit | “Why is CI slow?”, “audit the pipeline” | Collect, measure, diagnose, report, stop |
| Implement | “Implement finding F2” | One finding, smallest diff, approval, verify, measure |
| Compare | “Compare before and after” | Re-collect and diff the metrics |

## Install

### From GitHub (recommended for sharing)

Public repository: <https://github.com/zainsyedz/gitlab-ci-speedup>

```bash
git clone git@github.com:zainsyedz/gitlab-ci-speedup.git \
  ~/.config/opencode/skills/gitlab-ci-speedup
```

Update later with:

```bash
git -C ~/.config/opencode/skills/gitlab-ci-speedup pull --ff-only
```

### From a local checkout

```bash
mkdir -p ~/.config/opencode/skills
cp -R gitlab-ci-speedup ~/.config/opencode/skills/
```

### OpenCode (project-local)

Not recommended if you do not want the skill versioned with a repository. If you do:

```bash
mkdir -p .opencode/skills
cp -R gitlab-ci-speedup .opencode/skills/
```

OpenCode also discovers compatibility locations: `~/.claude/skills`, `~/.agents/skills`, and project `.claude/skills` / `.agents/skills`.

The directory name is the skill ID. Keep it as `gitlab-ci-speedup`.

## Requirements

- `glab`, authenticated against the GitLab host that owns the project
- `jq`
- `python3` 3.9+
- Optional: `gitlab-ci-local` or `glci` for local YAML validation, and a container engine (Docker/Podman) for container parity

Check auth:

```bash
glab auth status
```

## Use

Ask for a CI audit in a repository clone:

```text
Why is our GitLab CI slow? Audit the merge-gating path.
```

Or invoke the skill by name and follow its workflow.

The skill will:

1. Confirm the project, host, and target pipeline source.
2. Collect recent runs read-only.
3. Measure p50/p90 developer wait, queue delay, runner minutes, long poles, and an observed critical chain.
4. Rank findings with evidence and confidence.
5. Offer to implement one fix at a time, with local validation and before/after measurement.

It does not edit pipeline YAML during the initial audit unless you explicitly ask it to implement fixes.

## Manual workflow

```bash
# Run from the repository you want to audit.
SKILL_DIR="$HOME/.config/opencode/skills/gitlab-ci-speedup"   # or the project-local path
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT=".gitlab-ci-speedup/data/$STAMP"
bash "$SKILL_DIR/scripts/collect.sh" --limit 20 --out "$OUT"
python3 "$SKILL_DIR/scripts/analyze.py" "$OUT"
python3 "$SKILL_DIR/scripts/analyze.py" "$OUT" --json > "$OUT/metrics.json"
```

Filter to one pipeline source or ref:

```bash
bash "$SKILL_DIR/scripts/collect.sh" --limit 30 --source merge_request_event --out /tmp/ci-audit
bash "$SKILL_DIR/scripts/collect.sh" --limit 20 --ref main --out /tmp/ci-main
```

## Files

| Path | Purpose |
| --- | --- |
| `SKILL.md` | The workflow the agent follows |
| `references/measurement.md` | API fields, sampling, formulas, critical-path method, traps |
| `references/pattern-catalog.md` | GitLab-specific anti-patterns with safe fixes |
| `references/implementation-playbook.md` | Opt-in diffs, verification, rollback per pattern |
| `references/local-first.md` | How to move fast feedback local without weakening gates |
| `scripts/collect.sh` | Read-only pipeline/job collection via `glab api` |
| `scripts/analyze.py` | p50/p90 wait, queue, runner minutes, long poles |
| `scripts/compare.py` | Before/after metrics comparison |
| `scripts/verify-local.sh` | Lint, dry-run, and optional local job execution |

## Notes

- The collector is read-only. It never retries, cancels, or runs pipelines.
- The analyzer uses observed timestamps for its long-pole chain. Treat it as evidence, not a formal DAG proof.
- Reports default to `.gitlab-ci-speedup/report.md`. Add that directory to `.gitignore` only if the user agrees.
- The local-first recommendation keeps merge-blocking and compliance checks remote. It is not a license to delete tests.

## Attribution

Based on the `gitlab-ci-speedup` skill concept and the original README/SKILL pair, with inspiration from:

- `starslingdev/skills` `ci-speedup`
- `mesilov/gitlab-ci-performance-skill`
- GitLab Well-Architected `gitlab-ci-skill`

License: MIT.
