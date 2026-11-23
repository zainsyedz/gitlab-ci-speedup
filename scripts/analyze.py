#!/usr/bin/env python3
"""Analyze GitLab CI history collected by collect.sh.

Reads pipelines-details.json (or pipelines.json) and jobs.ndjson from a data
directory and reports developer wait, queue delay, runner seconds, terminal
jobs, and an observed long-pole chain.

This is read-only and dependency-free (Python 3.9+ standard library).
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def seconds(value: float | int | None) -> float:
    if value is None:
        return 0.0
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def percentile(values: Iterable[float], p: float) -> float | None:
    data = sorted(float(v) for v in values)
    if not data:
        return None
    if len(data) == 1:
        return data[0]
    k = (len(data) - 1) * p
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return data[int(k)]
    return data[lo] + (data[hi] - data[lo]) * (k - lo)


def fmt_seconds(value: float | None) -> str:
    if value is None:
        return "-"
    value = max(0.0, value)
    if value < 60:
        return f"{value:.1f}s"
    minutes, secs = divmod(value, 60)
    if minutes < 60:
        return f"{int(minutes)}m {secs:04.1f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes):02d}m {secs:04.1f}s"


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_jobs(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            records.append(json.loads(line))
    return records


def load_pipelines(data_dir: Path) -> list[dict[str, Any]]:
    details = data_dir / "pipelines-details.json"
    listing = data_dir / "pipelines.json"
    if details.exists():
        pipelines = load_json(details)
    elif listing.exists():
        pipelines = load_json(listing)
    else:
        raise FileNotFoundError("expected pipelines-details.json or pipelines.json")
    if not isinstance(pipelines, list):
        raise ValueError("pipeline data must be a JSON array")
    return [p for p in pipelines if isinstance(p, dict)]


def pipeline_jobs(jobs: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for job in jobs:
        pid = job.get("pipeline_id")
        if isinstance(pid, (int, float)):
            grouped[int(pid)].append(job)
    return grouped


def latest_attempt_per_name(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """For critical-path/terminal analysis, use the latest finished attempt per job name."""
    best: dict[str, dict[str, Any]] = {}
    for job in jobs:
        name = str(job.get("name") or "")
        finished = parse_ts(job.get("finished_at"))
        if finished is None:
            continue
        previous = best.get(name)
        previous_finished = parse_ts(previous.get("finished_at")) if previous else None
        if previous is None or previous_finished is None or finished > previous_finished:
            best[name] = job
    return list(best.values())


def observed_chain(
    jobs: list[dict[str, Any]], terminal: dict[str, Any]
) -> list[dict[str, Any]]:
    """Approximate the serialized chain that led to the terminal job.

    This is an observed chain, not a proof from the needs: DAG. It picks the
    job that finished most recently at or before the current job's start.
    """
    chain = [terminal]
    seen = {terminal.get("job_id")}
    current = terminal
    while len(chain) < 50:
        current_start = parse_ts(current.get("started_at"))
        if current_start is None:
            break
        candidates = []
        for job in jobs:
            if job.get("job_id") in seen:
                continue
            finished = parse_ts(job.get("finished_at"))
            if finished is not None and finished <= current_start:
                candidates.append((finished, job))
        if not candidates:
            break
        _, nxt = max(candidates, key=lambda item: item[0])
        chain.append(nxt)
        seen.add(nxt.get("job_id"))
        current = nxt
    return chain


def analyze(data_dir: Path, top: int) -> dict[str, Any]:
    pipelines = load_pipelines(data_dir)
    jobs_path = data_dir / "jobs.ndjson"
    if not jobs_path.exists():
        raise FileNotFoundError("expected jobs.ndjson; run collect.sh first")
    jobs = load_jobs(jobs_path)
    by_pipeline = pipeline_jobs(jobs)

    pipeline_rows: list[dict[str, Any]] = []
    skipped_pipelines: list[dict[str, Any]] = []
    job_stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "runs": 0,
            "durations": [],
            "queues": [],
            "start_offsets": [],
            "finish_offsets": [],
            "terminal": 0,
            "chain": 0,
            "stages": Counter(),
        }
    )

    for pipeline in pipelines:
        pid_raw = pipeline.get("id")
        if not isinstance(pid_raw, (int, float)):
            continue
        pid = int(pid_raw)
        status = str(pipeline.get("status") or "")
        source = str(pipeline.get("source") or "")
        created = parse_ts(pipeline.get("created_at"))

        if status not in {"success", "failed"} or created is None:
            skipped_pipelines.append(
                {
                    "id": pid,
                    "status": status,
                    "source": source,
                    "reason": "not a completed success/failed pipeline",
                }
            )
            continue

        raw_jobs = by_pipeline.get(pid, [])
        runnable = [
            job
            for job in raw_jobs
            if job.get("status") not in {"skipped", "manual", "created"}
            and parse_ts(job.get("started_at")) is not None
            and parse_ts(job.get("finished_at")) is not None
        ]
        if not runnable:
            skipped_pipelines.append(
                {
                    "id": pid,
                    "status": status,
                    "source": source,
                    "reason": "no runnable jobs with timestamps",
                }
            )
            continue

        latest = latest_attempt_per_name(runnable)
        required = [
            job
            for job in latest
            if not bool(job.get("allow_failure"))
            and str(job.get("status") or "") in {"success", "failed"}
        ]
        gating_jobs = required or latest
        terminal = max(
            gating_jobs,
            key=lambda job: parse_ts(job.get("finished_at")) or datetime.min.replace(tzinfo=timezone.utc),
        )
        terminal_finished = parse_ts(terminal.get("finished_at"))
        starts = [
            parse_ts(job.get("started_at"))
            for job in runnable
            if parse_ts(job.get("started_at")) is not None
        ]
        finishes = [
            parse_ts(job.get("finished_at"))
            for job in runnable
            if parse_ts(job.get("finished_at")) is not None
        ]
        if not starts or not finishes or terminal_finished is None:
            skipped_pipelines.append(
                {
                    "id": pid,
                    "status": status,
                    "source": source,
                    "reason": "incomplete timestamps",
                }
            )
            continue

        first_start = min(starts)
        last_finish = max(finishes)
        developer_wait = max(0.0, (terminal_finished - created).total_seconds())
        run_span = max(0.0, (last_finish - first_start).total_seconds())
        queue_delay = max(0.0, (first_start - created).total_seconds())
        runner_seconds = sum(seconds(job.get("duration")) for job in raw_jobs)
        retry_count = sum(1 for job in raw_jobs if bool(job.get("retried")))

        chain = observed_chain(latest, terminal)
        chain_ids = {job.get("job_id") for job in chain}

        for job in latest:
            name = str(job.get("name") or "")
            stats = job_stats[name]
            stats["runs"] += 1
            stats["stages"][str(job.get("stage") or "")] += 1
            stats["durations"].append(seconds(job.get("duration")))
            stats["queues"].append(seconds(job.get("queued_duration")))
            started = parse_ts(job.get("started_at"))
            finished = parse_ts(job.get("finished_at"))
            if started is not None:
                stats["start_offsets"].append(
                    max(0.0, (started - created).total_seconds())
                )
            if finished is not None:
                stats["finish_offsets"].append(
                    max(0.0, (finished - created).total_seconds())
                )
            if job.get("job_id") == terminal.get("job_id"):
                stats["terminal"] += 1
            if job.get("job_id") in chain_ids:
                stats["chain"] += 1

        pipeline_rows.append(
            {
                "id": pid,
                "iid": pipeline.get("iid"),
                "source": source,
                "ref": pipeline.get("ref"),
                "status": status,
                "created_at": pipeline.get("created_at"),
                "developer_wait": developer_wait,
                "run_span": run_span,
                "queue_delay": queue_delay,
                "runner_seconds": runner_seconds,
                "retry_count": retry_count,
                "job_count": len(latest),
                "terminal_job": terminal.get("name"),
                "terminal_job_id": terminal.get("job_id"),
                "terminal_stage": terminal.get("stage"),
                "chain": [
                    {
                        "name": job.get("name"),
                        "stage": job.get("stage"),
                        "duration": seconds(job.get("duration")),
                    }
                    for job in reversed(chain)
                ],
            }
        )

    if not pipeline_rows:
        raise ValueError(
            "no completed pipelines with usable job timestamps; "
            "try a larger --limit or a different ref/source"
        )

    waits = [row["developer_wait"] for row in pipeline_rows]
    spans = [row["run_span"] for row in pipeline_rows]
    queues = [row["queue_delay"] for row in pipeline_rows]
    runner_minutes = [row["runner_seconds"] / 60.0 for row in pipeline_rows]

    per_job = []
    for name, stats in job_stats.items():
        runs = stats["runs"]
        per_job.append(
            {
                "name": name,
                "stage": stats["stages"].most_common(1)[0][0] if stats["stages"] else "",
                "runs": runs,
                "p50_duration": percentile(stats["durations"], 0.50),
                "p90_duration": percentile(stats["durations"], 0.90),
                "p50_queue": percentile(stats["queues"], 0.50),
                "p50_start_offset": percentile(stats["start_offsets"], 0.50),
                "p50_finish_offset": percentile(stats["finish_offsets"], 0.50),
                "terminal_count": stats["terminal"],
                "chain_count": stats["chain"],
                "total_seconds": sum(stats["durations"]),
            }
        )

    long_poles = sorted(
        per_job,
        key=lambda item: (item["p50_duration"] or 0.0),
        reverse=True,
    )[:top]
    terminal_jobs = sorted(
        per_job,
        key=lambda item: (item["terminal_count"], item["p50_duration"] or 0.0),
        reverse=True,
    )[:top]
    chain_jobs = sorted(
        per_job,
        key=lambda item: (item["chain_count"], item["p50_duration"] or 0.0),
        reverse=True,
    )[:top]

    return {
        "data_dir": str(data_dir),
        "pipeline_count": len(pipeline_rows),
        "pipelines_skipped": skipped_pipelines,
        "summary": {
            "developer_wait_p50": percentile(waits, 0.50),
            "developer_wait_p90": percentile(waits, 0.90),
            "run_span_p50": percentile(spans, 0.50),
            "run_span_p90": percentile(spans, 0.90),
            "queue_delay_p50": percentile(queues, 0.50),
            "queue_delay_p90": percentile(queues, 0.90),
            "runner_minutes_p50": percentile(runner_minutes, 0.50),
            "runner_minutes_p90": percentile(runner_minutes, 0.90),
            "total_runner_minutes": sum(runner_minutes),
            "median_jobs_per_pipeline": statistics.median(
                row["job_count"] for row in pipeline_rows
            ),
        },
        "source_counts": dict(Counter(row["source"] for row in pipeline_rows)),
        "pipelines": sorted(pipeline_rows, key=lambda row: row["id"], reverse=True),
        "long_poles": long_poles,
        "terminal_jobs": terminal_jobs,
        "chain_jobs": chain_jobs,
        "per_job": sorted(
            per_job, key=lambda item: (item["total_seconds"]), reverse=True
        ),
    }


def render_markdown(result: dict[str, Any]) -> str:
    summary = result["summary"]
    lines: list[str] = []
    lines.append("# GitLab CI Speedup - Measurements")
    lines.append("")
    lines.append(f"- Data directory: `{result['data_dir']}`")
    lines.append(f"- Completed pipelines analyzed: **{result['pipeline_count']}**")
    sources = ", ".join(
        f"{source or '(unknown)'}={count}"
        for source, count in sorted(result["source_counts"].items())
    )
    lines.append(f"- Pipeline sources: {sources or '-'}")
    lines.append("")
    lines.append("## Wall clock and cost")
    lines.append("")
    lines.append("| Metric | p50 | p90 |")
    lines.append("| --- | ---: | ---: |")
    lines.append(
        f"| Developer wait | {fmt_seconds(summary['developer_wait_p50'])} | "
        f"{fmt_seconds(summary['developer_wait_p90'])} |"
    )
    lines.append(
        f"| Run span (first start - last finish) | {fmt_seconds(summary['run_span_p50'])} | "
        f"{fmt_seconds(summary['run_span_p90'])} |"
    )
    lines.append(
        f"| Queue delay (first start - created) | {fmt_seconds(summary['queue_delay_p50'])} | "
        f"{fmt_seconds(summary['queue_delay_p90'])} |"
    )
    lines.append(
        f"| Runner minutes | {summary['runner_minutes_p50']:.1f} | "
        f"{summary['runner_minutes_p90']:.1f} |"
    )
    lines.append("")
    lines.append(
        f"Total runner minutes across the sample: **{summary['total_runner_minutes']:.1f}**. "
        f"Median jobs per pipeline: **{summary['median_jobs_per_pipeline']:.0f}**."
    )
    lines.append("")
    lines.append("## Long poles by p50 duration")
    lines.append("")
    lines.append(
        "| Job | Stage | Runs | p50 | p90 | Queue p50 | Terminal | On observed chain |"
    )
    lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for item in result["long_poles"]:
        lines.append(
            f"| `{item['name']}` | {item['stage']} | {item['runs']} | "
            f"{fmt_seconds(item['p50_duration'])} | {fmt_seconds(item['p90_duration'])} | "
            f"{fmt_seconds(item['p50_queue'])} | {item['terminal_count']} | "
            f"{item['chain_count']} |"
        )
    lines.append("")
    lines.append("## Jobs that most often finish last")
    lines.append("")
    lines.append("| Job | Stage | Terminal count | p50 duration | p50 finish offset |")
    lines.append("| --- | --- | ---: | ---: | ---: |")
    for item in result["terminal_jobs"]:
        lines.append(
            f"| `{item['name']}` | {item['stage']} | {item['terminal_count']} | "
            f"{fmt_seconds(item['p50_duration'])} | "
            f"{fmt_seconds(item['p50_finish_offset'])} |"
        )
    lines.append("")
    lines.append("## Per-pipeline detail")
    lines.append("")
    lines.append(
        "| Pipeline | Source | Status | Developer wait | Run span | Queue | Runner min | Terminal job |"
    )
    lines.append("| ---: | --- | --- | ---: | ---: | ---: | ---: | --- |")
    for row in result["pipelines"]:
        lines.append(
            f"| {row['id']} | {row['source']} | {row['status']} | "
            f"{fmt_seconds(row['developer_wait'])} | {fmt_seconds(row['run_span'])} | "
            f"{fmt_seconds(row['queue_delay'])} | {row['runner_seconds'] / 60.0:.1f} | "
            f"`{row['terminal_job']}` |"
        )
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    lines.append(
        "- Developer wait is measured from pipeline creation to the latest required job finish; "
        "it includes runner queue time."
    )
    lines.append(
        "- The observed chain is approximate. Use `needs:` and stage barriers in the merged YAML "
        "for a structural critical path."
    )
    lines.append(
        "- Retries are included in runner minutes. Terminal/long-pole statistics use the latest "
        "attempt per job name."
    )
    if result["pipelines_skipped"]:
        lines.append(
            f"- Skipped {len(result['pipelines_skipped'])} pipelines without completed "
            "success/failure status or usable timestamps."
        )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze GitLab CI history collected by collect.sh"
    )
    parser.add_argument("data_dir", type=Path, help="Directory produced by collect.sh")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of Markdown",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=10,
        help="Number of jobs to show in long-pole tables (default: 10)",
    )
    args = parser.parse_args()

    try:
        result = analyze(args.data_dir, max(1, args.top))
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(render_markdown(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
