"""Charts and a per-endpoint table from a Locust --csv run.

    python loadtest/charts.py loadtest/results/run docs/perf

Writes throughput.png and latency.png into the output directory and prints
the per-endpoint table as Markdown.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

P95_MS, P99_MS = 200, 500


def number(value: str) -> float | None:
    return None if value in ("", "N/A") else float(value)


def history(prefix: str) -> list[dict]:
    with open(f"{prefix}_stats_history.csv", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["Name"] == "Aggregated"]
    t0 = int(rows[0]["Timestamp"])
    return [{**r, "t": int(r["Timestamp"]) - t0} for r in rows]


def throughput(rows: list[dict], out: Path) -> None:
    t = [r["t"] for r in rows]
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.plot(t, [number(r["Requests/s"]) for r in rows], label="requests/s")
    ax.plot(t, [number(r["Failures/s"]) for r in rows], label="failures/s", color="tab:red")
    ax.set_xlabel("seconds")
    ax.set_ylabel("requests per second")
    ax.set_ylim(bottom=0)
    users = ax.twinx()
    users.plot(t, [int(r["User Count"]) for r in rows], color="tab:gray", linestyle=":", label="users")
    users.set_ylabel("virtual users")
    users.set_ylim(0, 60)
    lines = ax.get_legend_handles_labels()[0] + users.get_legend_handles_labels()[0]
    ax.legend(lines, [line.get_label() for line in lines], loc="lower right")
    ax.set_title("Throughput, 50 users, 3 minutes")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out / "throughput.png", dpi=110)


def latency(rows: list[dict], out: Path) -> None:
    rows = [r for r in rows if number(r["95%"]) is not None]
    t = [r["t"] for r in rows]
    fig, ax = plt.subplots(figsize=(9, 4))
    for column in ("50%", "95%", "99%"):
        ax.plot(t, [number(r[column]) for r in rows], label=f"p{column[:-1]}")
    ax.axhline(P95_MS, color="tab:orange", linestyle="--", linewidth=1, label=f"p95 target {P95_MS} ms")
    ax.axhline(P99_MS, color="tab:red", linestyle="--", linewidth=1, label=f"p99 target {P99_MS} ms")
    ax.set_yscale("log")
    ax.set_xlabel("seconds")
    ax.set_ylabel("response time, ms (log)")
    ax.legend(loc="upper right")
    # Locust history percentiles are cumulative from the start of the run.
    ax.set_title("Response time percentiles, cumulative from start")
    ax.grid(alpha=0.3, which="both")
    fig.tight_layout()
    fig.savefig(out / "latency.png", dpi=110)


def table(prefix: str) -> str:
    with open(f"{prefix}_stats.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    lines = [
        "| Endpoint | Requests | Failures | p50 ms | p95 ms | p99 ms | req/s |",
        "|---|--:|--:|--:|--:|--:|--:|",
    ]
    for r in rows:
        name = f"**{r['Name']}**" if r["Name"] == "Aggregated" else f"`{r['Type']} {r['Name']}`"
        lines.append(
            f"| {name} | {int(r['Request Count']):,} | {r['Failure Count']} | {r['50%']} "
            f"| {r['95%']} | {r['99%']} | {float(r['Requests/s']):.1f} |"
        )
    return "\n".join(lines)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    prefix, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    rows = history(prefix)
    throughput(rows, out)
    latency(rows, out)
    print(table(prefix))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
