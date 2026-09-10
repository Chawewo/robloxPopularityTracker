"""Compare real observations around each target time; never impute missing counts."""
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import csv
import json
from pathlib import Path

import config
from collect import atomic_write, read_csv, utc_text


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def compare(points, latest, hours):
    target = latest - timedelta(hours=hours)
    times = [point[0] for point in points]
    index = bisect_left(times, target)
    candidates = [points[i] for i in (index - 1, index) if 0 <= i < len(points)
                  and points[i][0] < latest]
    if not candidates:
        return None
    match = min(candidates, key=lambda point: (abs(point[0] - target), point[0]))
    if abs(match[0] - target) > timedelta(minutes=config.TOLERANCE_MINUTES):
        return None
    return match


def analyze(data_dir=config.DATA_DIR, now=None):
    now = now or datetime.now(timezone.utc)
    data_dir = Path(data_dir)
    manifest = read_csv(data_dir / "collections.csv")
    complete = {parse_time(row["timestamp"]): int(row["games"]) for row in manifest}
    latest = max(complete) if complete else None
    earliest = min(complete) if complete else None
    previous_snapshot = sorted(complete)[-2] if len(complete) >= 2 else None
    cutoff = latest - timedelta(hours=max(config.WINDOWS), minutes=config.TOLERANCE_MINUTES) if latest else now
    if previous_snapshot:
        cutoff = min(cutoff, previous_snapshot)
    cutoff_text = utc_text(cutoff)
    recent = {stamp: count for stamp, count in complete.items() if stamp >= cutoff}
    metadata = {row["game_id"]: row for row in read_csv(data_dir / "games.csv")}
    points = defaultdict(dict)
    snapshot_counts = defaultdict(int)
    for path in sorted((data_dir / "snapshots").glob("*.csv")):
        if path.stem[:7] < cutoff_text[:7]:
            continue
        with path.open(encoding="utf-8", newline="") as stream:
            for row in csv.DictReader(stream):
                if row["timestamp"] < cutoff_text:
                    continue
                timestamp = parse_time(row["timestamp"])
                if timestamp in recent:
                    points[row["game_id"]][timestamp] = int(row["players"])
    for history in points.values():
        for timestamp in history:
            snapshot_counts[timestamp] += 1
    if any(snapshot_counts[stamp] != count for stamp, count in recent.items()):
        raise ValueError("Snapshot rows do not match the completed collection manifest")
    rows = []
    for game_id, history in points.items():
        # Missing from the newest complete collection means not currently eligible.
        if latest not in history or history[latest] < config.MIN_PLAYERS:
            continue
        current = history[latest]
        previous_players = history.get(previous_snapshot)
        since_last = (dict(delta=current - previous_players,
            pct=round((current - previous_players) / max(previous_players, 1) * 100, 2),
            baseline_players=previous_players, baseline_at=utc_text(previous_snapshot),
            elapsed_minutes=round((latest - previous_snapshot).total_seconds() / 60, 2))
            if previous_players is not None else None)
        ordered = sorted(history.items())
        windows = {}
        for hours in config.WINDOWS:
            previous = compare(ordered, latest, hours)
            windows[str(hours)] = (dict(delta=current - previous[1],
                pct=round((current - previous[1]) / max(previous[1], 1) * 100, 2),
                baseline_players=previous[1], baseline_at=utc_text(previous[0]))
                if previous else None)
        meta = metadata[game_id]
        cutoff = latest - timedelta(hours=24)
        first_seen = parse_time(meta["first_seen"])
        crossed = any(cutoff <= stamp < latest and value < config.MIN_PLAYERS
                      for stamp, value in ordered)
        observed_new = cutoff <= first_seen <= latest
        available = [window for window in windows.values() if window is not None]
        rows.append(dict(game_id=game_id, name=meta["name"], icon_url=meta["icon_url"],
            players=current, windows=windows, since_last=since_last, first_seen=meta["first_seen"],
            new_entrant=observed_new or crossed, crossed_floor=crossed,
            sustained=bool(available) and all(window["delta"] > 0 for window in available),
            available_windows=len(available)))
    rows.sort(key=lambda row: (-row["players"], row["game_id"]))
    return dict(generated_at=utc_text(now), latest_snapshot=utc_text(latest) if latest else None,
        previous_snapshot=utc_text(previous_snapshot) if previous_snapshot else None,
        last_interval_minutes=round((latest - previous_snapshot).total_seconds() / 60, 2) if previous_snapshot else None,
        history_hours=round((latest - earliest).total_seconds() / 3600, 2) if latest else 0,
        snapshot_count=len(complete), eligible_count=len(rows), min_players=config.MIN_PLAYERS,
        collect_floor=config.COLLECT_FLOOR, top_n=config.TOP_N,
        tolerance_minutes=config.TOLERANCE_MINUTES, stale_minutes=config.STALE_MINUTES,
        games=rows)


def main():
    result = analyze()
    atomic_write(config.OUTPUT, json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    print(f"Analyzed {result['eligible_count']} eligible games; {result['history_hours']}h history")


if __name__ == "__main__":
    main()
