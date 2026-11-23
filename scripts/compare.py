#!/usr/bin/env python3
"""Compare two GitLab CI metrics files produced by analyze.py --json.

Usage:
    python3 compare.py before.json after.json
    python3 compare.py before.json after.json --json

The comparison is intentionally conservative: medians are compared directly,
and sample-size or pipeline-source differences are called out. A total-runner-
minutes change is reported for context, not as proof of a win.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def load_metrics(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict) or "summary" not in data:
        raise ValueError(f"{path} does not look like analyze.py --json output")
    return data


def fmt_seconds(value: float | None) -> str:
    if value is None:
        return "-"
    value = max(0.0, float(value))
    if value < 60:
        return f"{value:.1f}s"
    minutes, seconds = divmod(value, 60)
    if minutes < 60:
        return f"{int(minutes)}m {seconds:04.1f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h {int(minutes):02d}m {seconds:04.1f}s"


def fmt_minutes(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{float(value):.1f} min"


def pct_delta(before: float | None, after: float | None) -> str:
    if before is None or after is None:
        return "n/a"
    if before == 0:
        return "n/a (before=0)"
    delta = (after - before) / before * 100.0
    return f"{delta:+.1f}%"


def abs_delta(before: float | None, after: float | None, fmt) -> str:
    if before is None or after is None:
        return "n/a"
    delta = after - before
    sign = "+" if delta >= 0 else "-"
    return f"{sign}{fmt(abs(delta))}"


def metric_row(
    label: str,
    before: float | None,
    after: float | None,
    fmt,
) -> str:
    return (
        f"| {label} | {fmt(before)} | {fmt(after)} | "
        f"{abs_delta(before, after, fmt)} | {pct_delta(before, after)} |"
    )


def job_map(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(job.get("name")): job
        for job in metrics.get("per_job", [])
        if job.get("name")
    }


def render_markdown(
    before: dict[str, Any],
    after: dict[str, Any],
    before_label: str,
    after_label: str,
) -> str:
    b = before["summary"]
    a = after["summary"]
    lines: list[str] = []
    lines.append("# GitLab CI Speedup - Before/After Comparison")
    lines.append("")
    lines.append(
        f"Comparing **{before_label}** (`{before.get('data_dir', '?')}`, "
        f"{before.get('pipeline_count', '?')} pipelines) against "
        f"**{after_label}** (`{after.get('data_dir', '?')}`, "
        f"{after.get('pipeline_count', '?')} pipelines)."
    )
    lines.append("")

    before_sources = before.get("source_counts", {})
    after_sources = after.get("source_counts", {})
    if before_sources != after_sources:
        lines.append(
            "> **Warning:** the pipeline-source mix differs between samples "
            f"(`{before_sources}` vs `{after_sources}`). Treat the comparison as directional."
        )
        lines.append("")

    if before.get("pipeline_count") != after.get("pipeline_count"):
        lines.append(
            "> **Warning:** sample sizes differ. Compare p50/p90 values, not "
            "total runner minutes, as the primary signal."
        )
        lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append("| Metric | Before | After | Absolute delta | Relative delta |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    lines.append(
        metric_row(
            "Developer wait p50",
            b.get("developer_wait_p50"),
            a.get("developer_wait_p50"),
            fmt_seconds,
        )
    )
    lines.append(
        metric_row(
            "Developer wait p90",
            b.get("developer_wait_p90"),
            a.get("developer_wait_p90"),
            fmt_seconds,
        )
    )
    lines.append(
        metric_row(
            "Run span p50",
            b.get("run_span_p50"),
            a.get("run_span_p50"),
            fmt_seconds,
        )
    )
    lines.append(
        metric_row(
            "Run span p90",
            b.get("run_span_p90"),
            a.get("run_span_p90"),
            fmt_seconds,
        )
    )
    lines.append(
        metric_row(
            "Queue delay p50",
            b.get("queue_delay_p50"),
            a.get("queue_delay_p50"),
            fmt_seconds,
        )
    )
    lines.append(
        metric_row(
            "Queue delay p90",
            b.get("queue_delay_p90"),
            a.get("queue_delay_p90"),
            fmt_seconds,
        )
    )
    lines.append(
        metric_row(
            "Runner minutes p50",
            b.get("runner_minutes_p50"),
            a.get("runner_minutes_p50"),
            fmt_minutes,
        )
    )
    lines.append(
        metric_row(
            "Runner minutes p90",
            b.get("runner_minutes_p90"),
            a.get("runner_minutes_p90"),
            fmt_minutes,
        )
    )
    lines.append(
        f"| Total runner minutes (context) | {fmt_minutes(b.get('total_runner_minutes'))} | "
        f"{fmt_minutes(a.get('total_runner_minutes'))} | "
        f"{abs_delta(b.get('total_runner_minutes'), a.get('total_runner_minutes'), fmt_minutes)} | "
        f"{pct_delta(b.get('total_runner_minutes'), a.get('total_runner_minutes'))} |"
    )
    lines.append("")

    before_jobs = job_map(before)
    after_jobs = job_map(after)
    common = sorted(set(before_jobs) & set(after_jobs))

    if common:
        rows = []
        for name in common:
            bj = before_jobs[name]
            aj = after_jobs[name]
            bdur = bj.get("p50_duration")
            adur = aj.get("p50_duration")
            if bdur is None or adur is None:
                continue
            rows.append((abs(adur - bdur), name, bj, aj, adur - bdur))
        rows.sort(reverse=True)

        lines.append("## Jobs with the largest p50 duration change")
        lines.append("")
        lines.append("| Job | Before p50 | After p50 | Delta | Before runs | After runs |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        for _, name, bj, aj, delta in rows[:10]:
            sign = "+" if delta >= 0 else "-"
            lines.append(
                f"| `{name}` | {fmt_seconds(bj.get('p50_duration'))} | "
                f"{fmt_seconds(aj.get('p50_duration'))} | "
                f"{sign}{fmt_seconds(abs(delta))} | {bj.get('runs', 0)} | {aj.get('runs', 0)} |"
            )
        lines.append("")

    only_before = sorted(set(before_jobs) - set(after_jobs))
    only_after = sorted(set(after_jobs) - set(before_jobs))
    if only_before or only_after:
        lines.append("## Jobs present in only one sample")
        lines.append("")
        if only_before:
            lines.append(f"- Removed or renamed: {', '.join(f'`{n}`' for n in only_before)}")
        if only_after:
            lines.append(f"- Added or renamed: {', '.join(f'`{n}`' for n in only_after)}")
        lines.append("")

    lines.append("## Caveats")
    lines.append("")
    lines.append(
        "- Medians are sensitive to sample size and pipeline mix. Compare the same "
        "source (MR vs push) and similar refs."
    )
    lines.append(
        "- Total runner minutes scale with the number of pipelines sampled; use p50/p90 "
        "per-pipeline values for a fair comparison."
    )
    lines.append(
        "- A local YAML edit cannot prove a win. Confirm with at least three new remote "
        "pipelines, and revert on regression."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare two analyze.py --json metrics files"
    )
    parser.add_argument("before", type=Path, help="Baseline metrics JSON")
    parser.add_argument("after", type=Path, help="Post-change metrics JSON")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of Markdown")
    parser.add_argument("--before-label", default="before", help="Label for baseline")
    parser.add_argument("--after-label", default="after", help="Label for post-change")
    args = parser.parse_args()

    try:
        before = load_metrics(args.before)
        after = load_metrics(args.after)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        payload = {
            "before": {
                "label": args.before_label,
                "data_dir": before.get("data_dir"),
                "pipeline_count": before.get("pipeline_count"),
                "summary": before.get("summary"),
                "source_counts": before.get("source_counts"),
            },
            "after": {
                "label": args.after_label,
                "data_dir": after.get("data_dir"),
                "pipeline_count": after.get("pipeline_count"),
                "summary": after.get("summary"),
                "source_counts": after.get("source_counts"),
            },
        }
        print(json.dumps(payload, indent=2, default=str))
    else:
        print(render_markdown(before, after, args.before_label, args.after_label))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
