"""Compress raw snapshots into one permanent row per game per completed UTC day.

Raw snapshots are pruned after RETENTION_DAYS and are too large to reread every 15 minutes.
Daily rows are tiny, survive pruning, and carry long-horizon analysis.

The daily peak is the primary measure. Collection has overnight holes, so a daily average
depends on which hours were sampled. A peak mostly does not, and it also removes the
time-of-day cycle that makes two point-in-time counts hard to compare.
"""
import csv
from collections import defaultdict
from pathlib import Path

import config
from collect import atomic_write, csv_text, read_csv

DAILY_FIELDS = ["date", "game_id", "peak", "avg", "samples"]
INDEX_FIELDS = ["date", "snapshots"]


def rollup(data_dir=config.DATA_DIR):
    """Roll up every completed day not yet recorded. Returns the dates added."""
    data_dir = Path(data_dir)
    stamps = [row["timestamp"] for row in read_csv(data_dir / "collections.csv")]
    if not stamps:
        return []
    index = {row["date"]: row for row in read_csv(data_dir / "daily_index.csv")}
    # The newest date may still gain snapshots, so it is never treated as complete.
    newest = max(stamp[:10] for stamp in stamps)
    counts = defaultdict(int)
    for stamp in stamps:
        if stamp[:10] < newest and stamp[:10] not in index:
            counts[stamp[:10]] += 1
    if not counts:
        return []
    valid = set(stamps)  # Rows outside the manifest are orphans from an interrupted write.
    months = {date[:7] for date in counts}
    totals = defaultdict(lambda: [0, 0, 0])  # (date, game) -> peak, sum, samples
    for path in sorted((data_dir / "snapshots").glob("*.csv")):
        if path.stem[:7] not in months:
            continue
        with path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                date = row["timestamp"][:10]
                if date in counts and row["timestamp"] in valid:
                    players = int(row["players"])
                    total = totals[(date, row["game_id"])]
                    total[0] = max(total[0], players)
                    total[1] += players
                    total[2] += 1
    by_month = defaultdict(list)
    for (date, game_id), (peak, total, samples) in totals.items():
        by_month[date[:7]].append(dict(date=date, game_id=game_id, peak=peak,
                                       avg=round(total / samples), samples=samples))
    for month, rows in by_month.items():
        path = data_dir / "daily" / f"{month}.csv"
        merged = read_csv(path) + rows
        merged.sort(key=lambda row: (row["date"], int(row["game_id"])))
        atomic_write(path, csv_text(DAILY_FIELDS, merged))
    index.update({date: dict(date=date, snapshots=count) for date, count in counts.items()})
    atomic_write(data_dir / "daily_index.csv", csv_text(INDEX_FIELDS, [index[key] for key in sorted(index)]))
    return sorted(counts)


def read_daily(data_dir=config.DATA_DIR):
    """Return (sorted dates, {game_id: {date: peak}}). A date is present only if collection ran."""
    data_dir = Path(data_dir)
    dates = sorted(row["date"] for row in read_csv(data_dir / "daily_index.csv"))
    peaks = defaultdict(dict)
    for path in sorted((data_dir / "daily").glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                peaks[row["game_id"]][row["date"]] = int(row["peak"])
    return dates, peaks


def main():
    added = rollup()
    print(f"Rolled up {len(added)} completed days")


if __name__ == "__main__":
    main()
