"""Compare real observations around each target time; never impute missing counts."""
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import csv
import json
from pathlib import Path

import backtest
import config
from collect import atomic_write, read_csv, utc_text
from rollup import read_daily
from signals import market_index, score


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
    if abs(match[0] - target) > timedelta(minutes=config.WINDOW_TOLERANCE_MINUTES.get(hours, config.TOLERANCE_MINUTES)):
        return None
    return match


def describe(detail, latest):
    """Release and engagement facts from Roblox. Everything is None until a game is enriched."""
    fields = dict(created=None, updated=None, age_days=None, updated_days=None, genre=None,
                  subgenre=None, creator=None, verified=False, visits=None, favorites=None, like_pct=None)
    if not detail or not detail.get("created"):
        return fields
    number = lambda key: int(detail[key]) if detail.get(key) else None
    up, down = number("up_votes"), number("down_votes")
    days_since = lambda stamp: max(0, (latest - parse_time(stamp)).days) if stamp else None
    return dict(fields, created=detail["created"], updated=detail["updated"] or None,
        age_days=days_since(detail["created"]), updated_days=days_since(detail["updated"]),
        genre=detail["genre"] or None, subgenre=detail["subgenre"] or None,
        creator=detail["creator"] or None, verified=detail["verified"] == "1",
        visits=number("visits"), favorites=number("favorites"),
        like_pct=round(up / (up + down) * 100, 1) if up is not None and down is not None and up + down else None)


def engagement_rates(data_dir, latest):
    """Visits and favorites per day from the first and last sample in the past week."""
    cutoff = utc_text(latest - timedelta(days=8))
    samples = defaultdict(list)
    for path in sorted((Path(data_dir) / "engagement").glob("*.csv"))[-2:]:
        for row in read_csv(path):
            if row["fetched_at"] >= cutoff and row["visits"]:
                samples[row["game_id"]].append(row)
    rates = {}
    for game_id, rows in samples.items():
        rows.sort(key=lambda row: row["fetched_at"])
        first, last = rows[0], rows[-1]
        days = (parse_time(last["fetched_at"]) - parse_time(first["fetched_at"])).total_seconds() / 86400
        if days >= 0.5:
            rates[game_id] = dict(
                visits_per_day=round((int(last["visits"]) - int(first["visits"])) / days),
                favorites_per_day=round((int(last["favorites"] or 0) - int(first["favorites"] or 0)) / days))
    return rates


def build(data_dir=config.DATA_DIR, now=None):
    """Return the leaderboard and the daily-peak history that dashboard period filters read."""
    now = now or datetime.now(timezone.utc)
    data_dir = Path(data_dir)
    manifest = read_csv(data_dir / "collections.csv")
    complete = {parse_time(row["timestamp"]): int(row["games"]) for row in manifest}
    latest = max(complete) if complete else None
    earliest = min(complete) if complete else None
    previous_snapshot = sorted(complete)[-2] if len(complete) >= 2 else None
    cutoff = latest - timedelta(hours=max(config.WINDOWS), minutes=max(config.WINDOW_TOLERANCE_MINUTES.values())) if latest else now
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
    details = {row["game_id"]: row for row in read_csv(data_dir / "details.csv")}
    rates = engagement_rates(data_dir, latest) if latest else {}
    dates, daily = read_daily(data_dir)
    # The final entry is a rolling 24h peak, so a riser shows up before its day has closed.
    rolling = {game_id: max((count for stamp, count in history.items() if stamp > latest - timedelta(hours=24)),
                            default=None) for game_id, history in points.items()} if latest else {}
    series = {game_id: [daily.get(game_id, {}).get(date) for date in dates] + [rolling.get(game_id)]
              for game_id in set(daily) | set(points)}
    market = market_index(list(series.values()), len(dates) + 1)
    rows = []
    for game_id, history in points.items():
        # Missing from the newest complete collection means not currently listed.
        if latest not in history:
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
                baseline_players=previous[1], baseline_at=utc_text(previous[0]),
                elapsed_hours=round((latest - previous[0]).total_seconds() / 3600, 2),
                approximate=abs((latest - previous[0]).total_seconds() / 60 - hours * 60) > config.TOLERANCE_MINUTES)
                if previous else None)
            if previous:
                samples = [count for stamp, count in ordered if previous[0] <= stamp <= latest]
                peak = max(samples)
                windows[str(hours)].update(observed_peak=peak,
                    below_peak_pct=round((peak - current) / max(peak, 1) * 100, 2),
                    observation_count=len(samples))
        meta = metadata[game_id]
        cutoff = latest - timedelta(hours=24)
        first_seen = parse_time(meta["first_seen"])
        # A real crossing, not a game dipping under the floor overnight: it spent the prior week below it.
        crossed = (current >= config.MIN_PLAYERS
                   and any(cutoff <= stamp < latest and value < config.MIN_PLAYERS for stamp, value in ordered)
                   and all(value < config.MIN_PLAYERS for stamp, value in ordered if stamp < cutoff))
        observed_new = cutoff <= first_seen <= latest
        # Preserve the short-term sustained signal independently of longer lookbacks.
        available = [windows[str(h)] for h in (1, 6, 24) if windows.get(str(h)) is not None]
        peaks = series[game_id]
        record = max(value for value in peaks if value is not None)
        rate = rates.get(game_id)
        week = [count for stamp, count in ordered if stamp > latest - timedelta(days=7)]
        # Little's law: average concurrent players = arrivals per minute x minutes per visit.
        minutes = round(sum(week) / len(week) * 1440 / rate["visits_per_day"], 1) if rate and rate["visits_per_day"] > 0 else None
        rows.append(dict(game_id=game_id, name=meta["name"], icon_url=meta["icon_url"],
            players=current, windows=windows, since_last=since_last, first_seen=meta["first_seen"],
            new_entrant=observed_new or crossed, crossed_floor=crossed,
            sustained=bool(available) and all(window["delta"] > 0 for window in available),
            available_windows=len(available), traction=current >= config.MIN_PLAYERS,
            peak_24h=rolling[game_id], tracked_peak=record,
            below_tracked_peak_pct=round((record - current) / record * 100, 1),
            tracked_days=(latest - first_seen).days, minutes_per_visit=minutes,
            **describe(details.get(game_id), latest), **(rate or dict(visits_per_day=None, favorites_per_day=None)),
            **score(peaks, len(peaks) - 1, market)))
    rows.sort(key=lambda row: (-row["players"], row["game_id"]))
    coverage = {}
    for hours in config.WINDOWS:
        available = sum(row['windows'][str(hours)] is not None for row in rows)
        elapsed = (latest - earliest).total_seconds() / 3600 if latest else 0
        coverage[str(hours)] = dict(available=available,
            reason='available' if available else 'not_enough_history' if elapsed < hours else 'collection_gap')
    result = dict(generated_at=utc_text(now), latest_snapshot=utc_text(latest) if latest else None,
        previous_snapshot=utc_text(previous_snapshot) if previous_snapshot else None,
        last_interval_minutes=round((latest - previous_snapshot).total_seconds() / 60, 2) if previous_snapshot else None,
        history_hours=round((latest - earliest).total_seconds() / 3600, 2) if latest else 0,
        snapshot_count=len(complete), eligible_count=sum(row["traction"] for row in rows), min_players=config.MIN_PLAYERS,
        window_coverage=coverage,
        collect_floor=config.COLLECT_FLOOR, top_n=config.TOP_N,
        tolerance_minutes=config.TOLERANCE_MINUTES, stale_minutes=config.STALE_MINUTES,
        details_as_of=max((row["fetched_at"] for row in details.values()), default=None),
        games=rows)
    keep = config.PUBLISHED_HISTORY_DAYS
    history = dict(dates=dates[-keep:], floor=config.COLLECT_FLOOR, latest_snapshot=result["latest_snapshot"],
        market=[round(value, 4) for value in market[-keep - 1:]],
        games={row["game_id"]: series[row["game_id"]][-keep - 1:] for row in rows})
    return result, history


def analyze(data_dir=config.DATA_DIR, now=None):
    return build(data_dir, now)[0]


def refresh_backtest():
    """Backtests only change when a day completes, so this runs daily, not every 15 minutes."""
    index = read_csv(config.DATA_DIR / "daily_index.csv")
    try:
        through = json.loads(config.BACKTEST_OUTPUT.read_text(encoding="utf-8")).get("through")
    except (OSError, ValueError):
        through = None
    if index and index[-1]["date"] != through:
        atomic_write(config.BACKTEST_OUTPUT, json.dumps(backtest.run(), separators=(",", ":")) + "\n")


def main():
    result, history = build()
    atomic_write(config.OUTPUT, json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n")
    atomic_write(config.HISTORY_OUTPUT, json.dumps(history, separators=(",", ":")) + "\n")
    refresh_backtest()
    print(f"Analyzed {result['eligible_count']} eligible games; {result['history_hours']}h history")


if __name__ == "__main__":
    main()
