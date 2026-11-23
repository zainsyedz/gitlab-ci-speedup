# GitLab CI pattern catalog

Each entry has a detection method, the cost it creates, a safe fix, and GitLab-specific caveats. Do not apply a fix without evidence; use measured job durations and pipeline sources.

## P1. Duplicate push and merge-request pipelines

**Detect:** Both `push` and `merge_request_event` pipelines run for the same commit. Look for `workflow:rules` that allow both `$CI_PIPELINE_SOURCE == "push"` and `$CI_PIPELINE_SOURCE == "merge_request_event"` without an exclusion, or jobs with separate `rules` for both.

**Cost:** Double runner minutes and duplicate status checks.

**Fix:** Use `workflow:rules` to run branch pipelines on the default branch and MR pipelines for MRs, or explicitly exclude the duplicate source. Keep required checks attached to the pipeline that actually gates the merge.

**Caveat:** Some teams rely on push pipelines for branch protection or environments. Confirm which pipeline is the required check before removing the other.

```yaml
workflow:
  rules:
    - if: '$CI_PIPELINE_SOURCE == "merge_request_event"'
    - if: '$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH'
    - if: '$CI_COMMIT_TAG'
```

## P2. Stage barriers instead of `needs:`

**Detect:** Multiple stages where jobs in a later stage do not consume artifacts from every earlier job, yet wait for the entire earlier stage. No `needs:` keys, or only a few.

**Cost:** Long wall clock from serialization that is not required by data dependencies.

**Fix:** Add `needs:` to jobs that only depend on one or two upstream jobs. Use `needs: []` only for jobs that truly have no prerequisites.

**Caveat:** `needs:` bypasses stage ordering. A job can start before unrelated earlier-stage jobs finish. That is the point, but it can surprise people who use stages as a safety gate. Check artifact availability and environment protection rules.

## P3. Long test job that is not sharded

**Detect:** One test job dominates p50 duration while other jobs finish early. Job log shows a single serial test process.

**Cost:** Wall clock equal to the whole suite.

**Fix:** Split by test directory, test group, or `parallel:matrix` if the test runner reports per-shard results. Aggregate the test reports so the pipeline still shows one coherent result.

**Caveat:** Sharding multiplies setup time and runner minutes. It helps wait, not cost. If the suite cannot be partitioned safely, or setup dominates, sharding can make things worse. Do not shard a 4-minute job into eight 3-minute jobs.

## P4. Missing `interruptible` and auto-cancel

**Detect:** Jobs have no `interruptible: true`, or only some do. Check `workflow:auto_cancel` settings.

**Cost:** Superseded pipelines continue consuming runners after a newer commit lands.

**Fix:** Set `interruptible: true` on jobs that are safe to cancel, usually tests and lint. Keep deploys, migrations, and anything with side effects non-interruptible.

**Caveat:** Jobs with `resource_group` are not interruptible in the same way, and cancellation safety is workload-specific. Never mark a deploy or data migration interruptible without explicit approval.

## P5. Cache not keyed on lockfiles

**Detect:** `cache.key` is a branch name, a constant, or missing. Dependency install jobs download everything on every run.

**Cost:** Repeated install time and network traffic.

**Fix:** Key on the lockfile(s) that determine the dependency set, and keep a fallback key.

```yaml
cache:
  key:
    files:
      - <lockfile>
  paths:
    - <dependency-dir>/
  policy: pull-push
```

**Caveat:** GitLab supports a limited number of files in `cache:key:files` (historically two). Use one lockfile per dependency manager or combine hashes. Shared runners do not guarantee cache locality; a cache hit on one runner may miss on another. Measure hit rate before claiming a win. Do not cache generated artifacts that must be fresh.

## P6. Dependencies installed in every job

**Detect:** `before_script` runs the project's dependency install command in many jobs.

**Cost:** Repeated minutes multiplied by job count.

**Fix:** Build dependencies once in a setup job, cache them, and let dependent jobs use `needs:` plus cache/artifacts. Or use a prebuilt image that already contains dependencies.

**Caveat:** Artifacts are downloaded by every consumer and can be slower than a warm cache for large trees. Prefer cache for dependency directories and artifacts for build outputs.

## P7. Oversized or immortal artifacts

**Detect:** `artifacts.paths` includes dependency directories, build outputs, logs, coverage HTML, or entire workspace directories; no `expire_in`; `when: always` on large outputs.

**Cost:** Storage, upload/download time, and runner minutes.

**Fix:** Publish only what downstream jobs or humans need. Set `expire_in`. Use `when: on_success` unless failure logs are required. Use `dependencies` or `needs:artifacts` to narrow downloads.

**Caveat:** Failure logs and test reports are often required for debugging. Keep the small machine-readable test/coverage summaries even when trimming the rest.

## P8. Container image builds without layer cache

**Detect:** A container image is built in CI with no build cache, or the container definition copies the whole repository before installing dependencies.

**Cost:** Full rebuild every pipeline.

**Fix:** Use a registry or local build cache, reorder the container definition to copy lockfiles first, and use multi-stage builds.

**Caveat:** Registry cache needs credentials and cleanup. A stale cache can hide dependency updates; invalidate on lockfile changes.

## P9. `rules:changes` missing or too broad

**Detect:** Changes to one component or documentation trigger jobs for unrelated components. Or `rules:changes` is used in a way that skips required checks unexpectedly.

**Cost:** Wasted runner minutes and unnecessary pipeline wait.

**Fix:** Add path-based `rules:changes` for monorepos and docs. Pair with `workflow:rules` so the pipeline still exists when needed.

**Caveat:** `changes` compares against the previous commit on branch pipelines, against the target branch on MR pipelines, and treats new branches as all-changed. Scheduled pipelines may behave differently. Use `compare_to` when the default is not what you want. Never let path filters skip a required security or compliance check.

## P10. Runner queue dominates

**Detect:** `job.queued_duration` is a large fraction of developer wait, often with specific `tags:`.

**Cost:** Developers wait for capacity, not for tests.

**Fix:** Review runner tags, concurrency, and autoscaling. Add capacity for the bottleneck tag. Reduce oversized runner requests.

**Caveat:** This is infrastructure, not YAML. Do not "fix" it by removing jobs. Get approval before changing runner configuration or tags.

## P11. `only` / `except` legacy syntax

**Detect:** `only:` or `except:` keys.

**Cost:** Harder to reason about, often causes duplicate or missing pipelines.

**Fix:** Migrate to `rules:` with explicit pipeline sources and conditions.

**Caveat:** A mechanical rewrite can change behavior. Convert one job at a time and compare pipeline creation with `glab ci lint --dry-run`.

## P12. Retries masking flaky jobs

**Detect:** `retry:` on broad failures, high retry counts in recent pipelines, or flaky test reports.

**Cost:** Extra runner minutes and longer tail latency.

**Fix:** Fix the flake or narrow `retry:when` to transient infrastructure errors. Do not retry deterministic test failures.

**Caveat:** A retry can be a legitimate mitigation for network-bound jobs. Measure the retry rate before removing it.

## P13. Expensive clone strategy

**Detect:** Full clone with full history on every job; submodules fetched recursively when unused; no `GIT_DEPTH`.

**Cost:** Clone time multiplies across jobs.

**Fix:** Set `GIT_DEPTH` to a small value when history is not needed. Use `GIT_STRATEGY: fetch` or `none` where appropriate. Fetch submodules only in jobs that need them.

**Caveat:** Release jobs, changelog generation, and some linters need history. Do not set `GIT_DEPTH: 1` globally without checking.

## P14. Oversized runners for tiny jobs

**Detect:** Jobs request large runners or high CPU/memory for tasks that finish in seconds.

**Cost:** Cost and scheduling pressure.

**Fix:** Match runner size to the job. Use separate tags or runner classes for heavy and light jobs.

**Caveat:** Moving a job to a smaller runner can increase duration if it is CPU-bound. Measure before and after.

## P15. `resource_group` serialization

**Detect:** Many jobs share a `resource_group`, especially deploys or environment jobs.

**Cost:** Artificial serialization and queue time.

**Fix:** Limit `resource_group` to jobs that truly must not overlap (same environment, same infrastructure). Use `resource_group` per environment or per branch rather than a single global group.

**Caveat:** Removing a resource group can cause concurrent deploys or migrations. Treat this as a correctness change, not a speed tweak.

## P16. Scheduled pipelines run the full suite

**Detect:** `schedule` pipelines execute the same heavy jobs as merge-gating pipelines.

**Cost:** Runner minutes with no developer-wait benefit.

**Fix:** Use `rules` to run only the jobs that need a schedule (nightly e2e, dependency audits, cleanup), or move heavy scans to a scheduled pipeline and keep MR checks thin.

**Caveat:** Scheduled security scans may be required for compliance. Do not remove them; move or tune them with owner approval.

## P17. `allow_failure` used to hide failures

**Detect:** `allow_failure: true` on test, lint, or security jobs.

**Cost:** False green pipelines and hidden quality debt. It does not reduce runner minutes.

**Fix:** Remove `allow_failure` from required checks, or convert the job to a non-gating report if that is the intent.

**Caveat:** Some jobs are intentionally advisory. Confirm with owners before changing gate semantics.

## P18. Inefficient child or downstream pipelines

**Detect:** Parent pipeline waits on child pipelines that rerun the same setup, or triggers downstream projects on every commit.

**Cost:** Duplicated setup, queue time, and cross-project runner minutes.

**Fix:** Trigger downstream pipelines only for relevant paths or versions. Share artifacts or images instead of rebuilding. Use `strategy: depend` only when the parent truly needs the result.

**Caveat:** Cross-project dependencies may be required for integration tests. Do not remove them without checking release and integration requirements.

## P19. Overly broad `before_script` / `after_script`

**Detect:** Global `before_script` installs tools or dependencies that only a few jobs need.

**Cost:** Every job pays the setup cost.

**Fix:** Move setup into job-specific scripts, a setup job with `needs:`, or a prebuilt image.

**Caveat:** Duplicating setup can increase maintenance. Prefer a shared image or a small reusable setup job.

## P20. Wasteful `parallel` usage

**Detect:** `parallel:` or `parallel:matrix` on jobs where setup dominates or shards are unbalanced.

**Cost:** More runner minutes with little wall-clock gain.

**Fix:** Shard only long, evenly partitionable jobs. Measure per-shard duration and setup overhead.

**Caveat:** `parallel` is not a cost optimization. It trades money for wait time.

## Do not recommend

- Deleting or disabling tests to make the pipeline green or fast.
- `allow_failure: true` as a speed fix.
- A cache key based only on the branch name.
- `interruptible: true` on deploy, migration, or release jobs.
- `needs: []` for jobs that consume artifacts or environment state.
- Moving compliance or security scans out of the required path without owner approval.
- Sharding every job "because parallelism is faster."
- Broad YAML rewrites without a before/after measurement.
