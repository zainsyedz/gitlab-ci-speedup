# Implementation playbook

Use this only after the user has approved a specific finding. The audit is read-only; this playbook is the opt-in action layer.

## Change protocol

1. Restate the finding and the measured baseline in one or two lines.
2. Check protected branches, required checks, and approval rules before changing gate semantics.
3. Show the smallest diff that addresses the finding.
4. State the invariants: which tests still run, which artifacts still exist, which deploy gates remain.
5. Validate locally with `scripts/verify-local.sh`.
6. Apply the change only after approval. Commit or push only if asked.
7. Re-collect and compare with `scripts/compare.py` after 3-5 new pipelines.
8. Revert if p50/p90 wait or runner minutes regress, or if a required check stops running.

Do not combine unrelated findings in one diff. If a change needs a migration, a runner change, or a policy exception, stop and ask.

## A. Cache keyed on lockfiles

**Use when:** dependency installation dominates multiple job durations, or install steps repeat across jobs.

**Diff shape:**

```yaml
cache:
  key:
    files:
      - composer.lock
      - package-lock.json
  paths:
    - vendor/
    - node_modules/
  policy: pull-push
```

**Verify:** run the install job twice locally or in a branch pipeline. Confirm the cache is restored and the lockfile hash changes the key. Check the job log for a cache hit.

**Rollback:** restore the old cache key or policy. Delete stale caches if a bad key is poisoning builds.

**Caveats:** `cache:key:files` supports a small number of files; use one lockfile per ecosystem or a combined hash. Shared runners do not guarantee cache locality. Do not cache generated code that must be rebuilt.

## B. Add `needs:` for real DAG parallelism

**Use when:** a later-stage job waits on an entire earlier stage but only consumes one upstream job.

**Diff shape:**

```yaml
test:unit:
  stage: test
  needs: ["build:assets"]
  script: ...
```

**Verify:** `glab ci lint --dry-run` and `gitlab-ci-local --list`. Confirm the job starts before unrelated earlier-stage jobs finish and that its artifacts are available.

**Rollback:** remove the `needs:` entry; the stage barrier returns.

**Caveats:** `needs:` bypasses stage ordering. Do not add it when the job depends on environment state or on a stage-level guarantee. `needs: []` is only for truly independent jobs.

## C. Mark cancellable jobs `interruptible`

**Use when:** superseded pipelines keep running tests after a newer commit.

**Diff shape:**

```yaml
test:unit:
  interruptible: true
  script: ...
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

**Use when:** large artifacts are uploaded/downloaded but only a small report is consumed.

**Diff shape:**

```yaml
test:unit:
  artifacts:
    when: always
    expire_in: 1 week
    paths:
      - junit.xml
      - coverage/cobertura.xml
```

**Verify:** run the job, confirm downstream jobs still receive the files they need, and check the artifact size in the job page.

**Rollback:** restore the previous `artifacts` block.

**Caveats:** failure logs and test reports are often required. Keep small JUnit/coverage files even when trimming HTML reports. `dependencies` and `needs:artifacts` can narrow downloads.

## F. Path-based rules for monorepos

**Use when:** docs-only or frontend-only changes run backend, database, and deploy jobs.

**Diff shape:**

```yaml
backend:test:
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
      changes:
        - "backend/**/*"
        - "composer.lock"
        - ".gitlab-ci.yml"
```

**Verify:** use `glab ci lint --dry-run` with representative refs, then open a docs-only MR and confirm the expected jobs are present or absent. Check that a required check is not skipped.

**Rollback:** remove the `changes:` rule.

**Caveats:** `changes` compares against the previous commit on push pipelines, against the target branch on MR pipelines, and treats new branches as all-changed. Use `compare_to` if the default is wrong. Never skip security or compliance checks by path without owner approval.

## G. Shard a long test job

**Use when:** one test job dominates p50 wait and the suite partitions cleanly.

**Diff shape:**

```yaml
test:unit:
  parallel: 4
  script:
    - vendor/bin/phpunit --configuration phpunit.xml --testsuite unit
```

Or, when the runner supports it:

```yaml
test:unit:
  parallel:
    matrix:
      - SHARD: ["1/4", "2/4", "3/4", "4/4"]
  script:
    - vendor/bin/phpunit --shard "$SHARD"
```

**Verify:** compare per-shard duration and the aggregated JUnit result. Confirm setup time does not dominate and that all tests still run exactly once.

**Rollback:** remove `parallel` and restore the original command.

**Caveats:** sharding increases runner minutes. Use it to reduce wait, not to reduce cost. If setup dominates or shards are unbalanced, it can make both wait and cost worse.

## H. Move the fast feedback loop local

**Use when:** developers and agents wait on remote lint/unit tests that can run in the same container locally.

**Diff shape:**

```make
.PHONY: ci-local
ci-local:
	docker compose run --rm app composer validate --strict
	docker compose run --rm app vendor/bin/phpunit --testsuite=unit
	docker compose run --rm app npm run lint
```

**Verify:** run `make ci-local` from a clean checkout. Confirm versions and services match CI, and that the remote required check still exists.

**Rollback:** remove the target and restore the previous developer instructions.

**Caveats:** keep merge-blocking, security, compliance, and environment-dependent checks remote. Do not remove a remote test until local parity has been proven. See `local-first.md`.

## I. Docker build cache

**Use when:** Docker builds rebuild all layers on every pipeline.

**Diff shape:**

```yaml
build:image:
  script:
    - docker buildx build
        --cache-from "$CI_REGISTRY_IMAGE:cache"
        --cache-to "$CI_REGISTRY_IMAGE:cache,mode=max"
        --tag "$CI_REGISTRY_IMAGE:$CI_COMMIT_SHA"
        --push .
```

**Verify:** build twice with a small source change. Confirm the second build reuses layers. Check registry credentials and cache cleanup.

**Rollback:** remove the cache flags.

**Caveats:** registry cache needs credentials and periodic cleanup. A stale cache can hide dependency updates; invalidate on lockfile changes.

## J. Build dependencies once

**Use when:** many jobs run the same install command in `before_script`.

**Diff shape:**

```yaml
install:
  stage: .pre
  script:
    - composer install --no-interaction --prefer-dist
  cache:
    key:
      files: ["composer.lock"]
    paths: ["vendor/"]
    policy: pull-push

test:unit:
  needs: ["install"]
  script:
    - vendor/bin/phpunit --testsuite unit
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
- [ ] The affected job was run locally in the same image, if feasible.
- [ ] Rollback is a single revert or a documented edit.
- [ ] A before/after measurement plan exists.
