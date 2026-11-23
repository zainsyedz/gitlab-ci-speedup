# Measurement reference

How to turn GitLab pipeline history into defensible numbers.

## API surface

All examples assume an authenticated `glab` in the repository. `:id` is replaced by the current project.

```bash
# Project identity
glab repo view --output json | jq '{id,path_with_namespace,default_branch,web_url}'

# Recent pipelines
glab api "projects/:id/pipelines?per_page=20&order_by=updated_at&sort=desc"

# Pipeline detail (created_at, started_at, finished_at, duration, queued_duration)
glab api "projects/:id/pipelines/<pipeline-id>"

# Jobs for a pipeline, including retried job entries
glab api "projects/:id/pipelines/<pipeline-id>/jobs?per_page=100&include_retried=true"

# Child pipelines / bridge jobs
glab api "projects/:id/pipelines/<pipeline-id>/bridges"

# Job trace (only if step-level timing is needed)
glab api "projects/:id/jobs/<job-id>/trace"
```

Use pagination for large pipelines. The jobs endpoint returns up to 100 per page by default on most GitLab versions; check the `x-next-page` response header or use `--paginate` where supported.

## Sampling design

- Sample 20-30 recent pipelines, not one.
- Keep pipeline sources separate: `push`, `merge_request_event`, `schedule`, `web`, `parent_pipeline`, `pipeline`.
- Separate the default branch from feature branches and MRs.
- Exclude `skipped` and `canceled` pipelines from wait statistics.
- Exclude superseded pipelines when the team routinely cancels old runs; otherwise they distort the queue picture.
- For merge-gating analysis, use only pipelines that can block a merge. A scheduled security scan is cost, not merge wait.
- Report the exact sample size and window. If a ref has fewer than 10 gating runs, mark confidence low.

## Definitions

Let `pipeline.created_at` be `C`.

- **Developer wait** = `max(finished_at of required jobs) - C`.
  - Required = not `allow_failure`, not `manual`, status in `success` or `failed`.
  - This includes runner queue time and is the number a developer experiences.
- **Run span** = `max(all job finished_at) - min(all job started_at)`.
- **Queue delay** = `min(all job started_at) - C`.
- **Runner seconds** = `sum(job.duration)` across jobs. If retries are included, report retry seconds separately rather than hiding them in the total.
- **Job duration** = `job.finished_at - job.started_at`, or `job.duration` when present.
- **Job queue time** = `job.queued_duration`, or `job.started_at - C` when absent.

Use **p50** for the typical experience and **p90** for the bad days. A mean over five runs is not evidence.

## Critical path

Two complementary methods:

### Structural (what should determine the path)

1. Parse the merged CI configuration and its local includes.
2. Build a graph:
   - Job with `needs:` depends on the named jobs.
   - Job without `needs:` depends on all jobs in earlier stages (stage barrier).
   - `needs: []` starts immediately.
3. Earliest start = max(finish of dependencies). Add measured job duration plus queue time.
4. The path with the largest total duration is the critical path.

Remote includes and dynamic child pipelines can make this incomplete. Say so.

### Observed (what actually determined the path)

1. For each pipeline, find the last required job to finish.
2. Walk backward: from the current job's `started_at`, pick the job with the latest `finished_at` at or before that start.
3. Repeat until no predecessor remains.
4. Count how often each job appears in these chains and how often it is the terminal job.

This is an observed serialized chain, not a proof. It is good at finding long poles and bad at proving a DAG-level optimization. Label it accordingly.

## Common traps

- **`duration` is not wall clock.** `job.duration` is execution time. `queued_duration` is time waiting for a runner. Pipeline wall clock also includes creation and downstream overhead.
- **Retries inflate both wait and cost.** A retried job appears more than once with `include_retried=true`. Deduplicate by job name for critical-path analysis, but count retry minutes for cost.
- **`allow_failure: true` does not mean free.** It does not gate the pipeline, but it still consumes runner minutes. Keep it out of the gating path and in the cost total.
- **Manual jobs are not wait.** They may never run. Exclude them from wall-clock unless the user explicitly counts human approval time.
- **Skipped jobs are not zero-duration work.** They can indicate over-broad `rules:` or a pipeline that did nothing useful.
- **Failed pipelines are real waits.** Report successful and failed separately; a fast failure is not a speed win.
- **Child pipelines double-count.** The parent bridge job and the downstream jobs can both appear. Count the downstream wall clock once and label the bridge separately.
- **Timezone.** GitLab timestamps are UTC ISO-8601. Parse as UTC and do not mix local time.
- **Runner queue is infrastructure.** If queue delay dominates, the fix is runner capacity, tags, or concurrency, not a YAML micro-optimization.
- **Shared-runner cache is not guaranteed.** A cache hit on one runner may be a miss on another. Measure hit rate from job logs before claiming a cache win.

## Confidence labels

| Label | Minimum evidence |
| --- | --- |
| Measured | 10+ comparable pipelines, same source/ref, timestamps inspected |
| Estimated | 3-9 comparable pipelines or a clear static defect with a measured job duration |
| Speculative | Static pattern only, no representative run data |

Use the weakest label that applies to the claim, not the strongest label in the report.

## Minimal jq snippets

```bash
# Pipeline list with source and status
glab api "projects/:id/pipelines?per_page=20" \
  | jq -r '.[] | [.id,.source,.ref,.status,.created_at] | @tsv'

# Job durations for one pipeline
glab api "projects/:id/pipelines/<id>/jobs?per_page=100&include_retried=true" \
  | jq -r '.[] | [.id,.name,.stage,.status,(.duration//0),(.queued_duration//0),.started_at,.finished_at] | @tsv'

# Latest required job finish for a pipeline
glab api "projects/:id/pipelines/<id>/jobs?per_page=100&include_retried=true" \
  | jq -r '[.[] | select(.allow_failure == false and .status != "manual" and .status != "skipped") | .finished_at] | max'
```

The bundled `scripts/analyze.py` implements these calculations and emits both Markdown and JSON.
