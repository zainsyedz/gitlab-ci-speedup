# Implementation playbook

Use this only after the user has approved a specific finding. The audit is read-only; this playbook is the opt-in action layer.

This document is deliberately language-, framework-, and project-neutral. Discover the repository's own install, lint, build, test, and deploy commands. The `<...>` placeholders must be replaced with what the repository actually uses.

## Change protocol

1. Restate the finding and the measured baseline in one or two lines.
2. Check protected branches, required checks, and approval rules before changing gate semantics.
3. Show the smallest diff that addresses the finding.
4. State the invariants: which checks still run, which artifacts still exist, which deploy gates remain.
5. Validate locally with `scripts/verify-local.sh` and the repository's own checks.
6. Apply the change only after approval. Commit or push only if asked.
7. Re-collect and compare with `scripts/compare.py` after 3-5 new pipelines.
8. Revert if p50/p90 wait or runner minutes regress, or if a required check stops running.

Do not combine unrelated findings in one diff. If a change needs an infrastructure change, a data migration, or a policy exception, stop and ask.

## A. Cache keyed on lockfiles

**Use when:** dependency installation dominates multiple job durations, or install steps repeat across jobs.

**Diff shape:**

```yaml
cache:
  key:
    files:
      - <lockfile>
  paths:
    - <dependency-dir>/
  policy: pull-push
```

**Verify:** run the install job twice locally or in a branch pipeline. Confirm the cache is restored and the lockfile hash changes the key. Check the job log for a cache hit.

**Rollback:** restore the old cache key or policy. Delete stale caches if a bad key is poisoning builds.

**Caveats:** `cache:key:files` supports a small number of files; use one lockfile per dependency manager or a combined hash. Shared runners do not guarantee cache locality. Do not cache generated code that must be rebuilt.

## B. Add `needs:` for real DAG parallelism

**Use when:** a later-stage job waits on an entire earlier stage but only consumes one upstream job.

**Diff shape:**

```yaml
<test-job>:
  stage: <test-stage>
  needs: ["<build-job>"]
  script:
    - <test-command>
```

**Verify:** `glab ci lint --dry-run` and `gitlab-ci-local --list`. Confirm the job starts before unrelated earlier-stage jobs finish and that its artifacts are available.

**Rollback:** remove the `needs:` entry; the stage barrier returns.

**Caveats:** `needs:` bypasses stage ordering. Do not add it when the job depends on environment state or on a stage-level guarantee. `needs: []` is only for truly independent jobs.

## C. Mark cancellable jobs `interruptible`

**Use when:** superseded pipelines keep running checks after a newer commit.

**Diff shape:**

```yaml
<test-job>:
  interruptible: true
  script:
    - <test-command>
```

**Verify:** push two commits in quick succession on a test branch; confirm the older pipeline is canceled and the new one is not. Do not test this on the default branch.

**Rollback:** remove `interruptible`.

**Caveats:** never mark deploy, migration, release, or anything with external side effects interruptible. Jobs with `resource_group` may not behave as expected. Check `workflow:auto_cancel` settings.

## D. Remove duplicate push/MR pipelines

**Use when:** the same commit creates both a branch pipeline and an MR pipeline with the same jobs.

**Diff shape:**

```yaml
workflow:
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
    - if: '$CI_COMMIT_TAG'
```

**Verify:** check `glab ci list` after pushing a branch and opening an MR. Confirm exactly one gating pipeline exists and branch protection still finds its required checks.

**Rollback:** restore the previous `workflow:rules`.

**Caveats:** a required check may be attached to the pipeline you remove. Confirm with the project's branch protection and approval settings first.

## E. Trim artifacts and add expiry

**Use when:** large artifacts are uploaded or downloaded but only a small report is consumed.

**Diff shape:**

```yaml
<test-job>:
  artifacts:
    when: always
    expire_in: 1 week
    paths:
      - <test-report>
      - <coverage-report>
```

**Verify:** run the job, confirm downstream jobs still receive the files they need, and check the artifact size in the job page.

**Rollback:** restore the previous `artifacts` block.

**Caveats:** failure logs and test reports are often required. Keep the small machine-readable reports even when trimming HTML output. `dependencies` and `needs:artifacts` can narrow downloads.

## F. Path-based rules for monorepos

**Use when:** changes to one component or documentation run jobs for unrelated components.

**Diff shape:**

```yaml
<component>:<check>:
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
      changes:
        - "<component>/**/*"
        - "<lockfile>"
        - ".gitlab-ci.yml"
```

**Verify:** use `glab ci lint --dry-run` with representative refs, then open a documentation-only MR and confirm the expected jobs are present or absent. Check that a required check is not skipped.

**Rollback:** remove the `changes:` rule.

**Caveats:** `changes` compares against the previous commit on push pipelines, against the target branch on MR pipelines, and treats new branches as all-changed. Use `compare_to` if the default is wrong. Never skip security or compliance checks by path without owner approval.

## G. Shard a long test job

**Use when:** one test job dominates p50 wait and the suite partitions cleanly.

**Diff shape:**

```yaml
<test-job>:
  parallel: 4
  script:
    - <test-command-with-sharding>
```

Or, when the runner supports a shard matrix:

```yaml
<test-job>:
  parallel:
    matrix:
      - SHARD: ["1/4", "2/4", "3/4", "4/4"]
  script:
    - <test-command> "$SHARD"
```

Replace `<test-command>` with the repository's real runner invocation. Use the shard syntax that runner actually supports, not a guessed flag.

**Verify:** compare per-shard duration and the aggregated test result. Confirm setup time does not dominate and that all tests still run exactly once.

**Rollback:** remove `parallel` and restore the original command.

**Caveats:** sharding increases runner minutes. Use it to reduce wait, not to reduce cost. If setup dominates or shards are unbalanced, it can make both wait and cost worse.

## H. Move the fast feedback loop local

**Use when:** developers and agents wait on remote checks that can run in the same environment locally.

**Diff shape:**

```make
.PHONY: ci-local
ci-local:
	<lint-command>
	<unit-test-command>
```

Or use a single script such as `bin/ci`.

**Verify:** run the command from a clean checkout. Confirm versions and services match CI, and that the remote required check still exists.

**Rollback:** remove the target or script and restore the previous developer instructions.

**Caveats:** keep merge-blocking, security, compliance, and environment-dependent checks remote. Do not remove a remote check until local parity has been proven. See `local-first.md`.

## I. Container image build cache

**Use when:** the project builds container images and every pipeline rebuilds all layers.

**Diff shape:**

```yaml
<image-build-job>:
  script:
    - <image-build-command-with-registry-cache>
```

Use the image builder and cache backend the repository already uses. Do not introduce a new builder just for caching.

**Verify:** build twice with a small source change. Confirm the second build reuses layers. Check registry credentials and cache cleanup.

**Rollback:** remove the cache configuration.

**Caveats:** registry cache needs credentials and periodic cleanup. A stale cache can hide dependency updates; invalidate on lockfile changes.

## J. Build dependencies once

**Use when:** many jobs run the same dependency install command in `before_script`.

**Diff shape:**

```yaml
<install-job>:
  stage: .pre
  script:
    - <install-command>
  cache:
    key:
      files: ["<lockfile>"]
    paths: ["<dependency-dir>/"]
    policy: pull-push

<test-job>:
  needs: ["<install-job>"]
  script:
    - <unit-test-command>
```

**Verify:** confirm dependent jobs restore the cache or artifact and do not reinstall. Compare total runner minutes, not just wall clock.

**Rollback:** restore the per-job install.

**Caveats:** artifacts are downloaded by every consumer and can be slower than a warm cache for large dependency trees. Prefer cache for dependencies, artifacts for build outputs.

## Final diff checklist

- [ ] The finding is named and linked to evidence.
- [ ] Protected branches and required checks were checked.
- [ ] The diff is limited to one finding.
- [ ] `glab ci lint` passes.
- [ ] `glab ci lint --dry-run` passes or the limitation is explained.
- [ ] `gitlab-ci-local --list` passes if available.
- [ ] The affected job was run locally in the same environment, if feasible.
- [ ] Rollback is a single revert or a documented edit.
- [ ] A before/after measurement plan exists.
