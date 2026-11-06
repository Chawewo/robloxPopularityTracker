"""Replay history to measure which signals really came before blow-ups.

For each past day this uses only data up to that day, ranks the games a scout could still
act on, then checks what happened next. A hit needs a sustained 3-day level, judged against
the highest level already visible on the day, so a spike that had already happened and a
one-day event are not counted as predictions.

The signal lab tests every signal the same way, including ones still collecting history.
Nothing joins the score until it earns its place here. The score's weights were chosen after
seeing this same history, so results are optimistic. Rerun as history grows: later days are
a fairer test. Run: python backtest.py
"""
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import median

import charts
import clones
import config
from collect import atomic_write, read_csv
from rollup import read_daily, read_floors
from signals import level, market_index, rising_streak, score, vs_market

LOOKBACK = 14
HORIZON = 14
TOP_K = 25
SCOUT_MIN = config.COLLECT_FLOOR
SCOUT_MAX = 15_000
MAX_DAYS_TESTED = 90  # Keeps the daily refresh quick as history grows.
MIN_SAMPLES = 150  # Below this a signal's result is still mostly chance.
HITS = {"double": (2, 2_000), "triple": (3, 3_000), "five_x": (5, 5_000)}
BANDS = ((0, 15), (15, 40), (40, 70), (70, 101))


def sustained_level(peaks, start, end):
    """The best median of any 3 consecutive days, needing at least 2 of the 3 to be observed."""
    best = 0
    for first in range(start, end - 1):
        window = [value for value in peaks[first:first + 3] if value is not None]
        if len(window) >= 2:
            best = max(best, median(window))
    return best


def age_on(created, day):
    return (day - date.fromisoformat(created[:10])).days if created else None


def day_families(series, day, dates, floors, names, details):
    """Families as they looked on that day, using only games that existed by then."""
    stamp = date.fromisoformat(dates[day])
    games = []
    for game_id, peaks in series.items():
        age = age_on(details.get(game_id, {}).get("created"), stamp)
        if age is not None and age >= 0 and game_id in names and any(value is not None for value in peaks[:day + 1]):
            games.append(dict(game_id=game_id, name=names[game_id], age_days=age,
                              creator=details[game_id].get("creator"), level=level(peaks, day, floors=floors),
                              level_before=level(peaks, max(day - 7, 0), floors=floors)))
    return clones.find_families(games)


def forward_flags(data_dir, dates):
    """Signals logged going forward, as {name: {(game_id, day index)}}. Empty until data builds."""
    index = {date_text: position for position, date_text in enumerate(dates)}
    flags = defaultdict(set)
    for date_text, rows in charts.read_recent(data_dir, len(dates) + 1):
        for row in rows:
            if date_text in index:
                flags[f"On {row['chart'].replace('-', ' ')} chart"].add((row["game_id"], index[date_text]))
    previous = {}
    for path in sorted((Path(data_dir) / "badges").glob("*.csv")):
        for row in read_csv(path):
            if row["date"] in index and row["daily_awards"]:
                awards = int(row["daily_awards"])
                before = previous.get(row["game_id"])
                if before and awards >= 1.5 * before:
                    flags["New players up 50%"].add((row["game_id"], index[row["date"]]))
                previous[row["game_id"]] = awards
    samples = defaultdict(list)
    for path in sorted((Path(data_dir) / "engagement").glob("*.csv")):
        for row in read_csv(path):
            samples[row["game_id"]].append(row)
    for game_id, rows in samples.items():
        rows.sort(key=lambda row: row["fetched_at"])
        for first, middle, last in zip(rows, rows[1:], rows[2:]):
            early = int(middle["visits"] or 0) - int(first["visits"] or 0)
            late = int(last["visits"] or 0) - int(middle["visits"] or 0)
            day = index.get(last["fetched_at"][:10])
            if day is not None and early > 0 and late >= 1.3 * early:
                flags["Visits accelerating 30%"].add((game_id, day))
    for row in read_csv(Path(data_dir) / "updates.csv"):
        day = index.get(row["updated"][:10])
        if day is not None:
            for offset in range(3):
                flags["Updated in past 3 days"].add((row["game_id"], day + offset))
    changes = defaultdict(set)
    for row in read_csv(Path(data_dir) / "changes.csv"):
        day = index.get(row["timestamp"][:10])
        if day is not None:
            for offset in range(3):
                changes[f"{'Title' if row['field'] == 'name' else 'Icon'} changed in past 3 days"].add((row["game_id"], day + offset))
    flags.update(changes)
    return flags


def observations(series, length, market, floors=None, names=None, details=None, flags=None):
    names, details, flags = names or {}, details or {}, flags or {}
    start = max(LOOKBACK, length - HORIZON - MAX_DAYS_TESTED)
    rows = []
    for day in range(start, length - HORIZON):
        stamp = date.fromisoformat(names["__dates__"][day]) if "__dates__" in names else None
        families, family_of = day_families(series, day, names["__dates__"], floors, names, details) if stamp else ({}, {})
        for game_id, peaks in series.items():
            if peaks[day] is None:
                continue
            current = level(peaks, day, floors=floors)
            if not SCOUT_MIN <= current <= SCOUT_MAX:
                continue
            visible = max(value for value in peaks[day - 2:day + 1] if value is not None)
            future = sustained_level(peaks, day + 1, day + HORIZON + 1)
            outcome = {name: future >= multiple * visible and future >= minimum
                       for name, (multiple, minimum) in HITS.items()}
            age = age_on(details.get(game_id, {}).get("created"), stamp) if stamp else None
            family = families.get(family_of.get(game_id))
            result = score(peaks, day, market, floors, family is not None)
            growth_3d = vs_market(peaks, day, 3, market, floors)
            signals = {
                "5+ rising days": result["streak"] >= 5,
                "3-4 rising days": 3 <= result["streak"] <= 4,
                "Released 14-30 days ago": age is not None and 14 < age <= 30,
                "Released 31-60 days ago": age is not None and 30 < age <= 60,
                "Released 61-180 days ago": age is not None and 60 < age <= 180,
                "Released over 2 years ago": age is not None and age > 730,
                "In a clone wave": family is not None,
                "Clone in a rising wave": bool(family and game_id != family["leader"] and (family["median_growth_pct"] or 0) > 0),
                "Clone wave, game climbing": family is not None and result["streak"] >= 1 and (growth_3d or 0) > 0,
                "Far below record": current <= 0.25 * max(value for value in peaks[:day + 1] if value),
                "Creator is verified": details.get(game_id, {}).get("verified") == "1",
            }
            signals.update({name: (game_id, day) in members for name, members in flags.items()})
            rows.append(dict(game_id=game_id, day=day, outcome=outcome, score=result["score"],
                             growth_7d=vs_market(peaks, day, 7, market, floors), growth_3d=growth_3d,
                             signals=[name for name, on in signals.items() if on]))
    return rows


def hit_rates(rows):
    return {name: round(sum(row["outcome"][name] for row in rows) / len(rows) * 100, 2) if rows else None
            for name in HITS}


def top_picks(rows, key, top=TOP_K):
    by_day = {}
    for row in rows:
        by_day.setdefault(row["day"], []).append(row)
    picks = []
    for day_rows in by_day.values():
        day_rows.sort(key=lambda row: (row[key] is None, -(row[key] or 0), row["game_id"]))
        picks.extend(day_rows[:top])
    return picks


def signal_lab(rows, names):
    """Every signal's record against the average game."""
    base = hit_rates(rows)["double"] or 0
    lab = []
    for name in names:
        members = [row for row in rows if name in row["signals"]]
        double = hit_rates(members)["double"]
        lab.append(dict(name=name, samples=len(members), games=len({row["game_id"] for row in members}),
            double=double, lift=round(double / base, 2) if double is not None and base else None,
            enough=len(members) >= MIN_SAMPLES))
    lab.sort(key=lambda item: (not item["enough"], -(item["lift"] or 0)))
    return lab


def run(data_dir=config.DATA_DIR):
    dates, peaks = read_daily(data_dir)
    floors = read_floors(data_dir)
    series = {game_id: [days.get(date_text) for date_text in dates] for game_id, days in peaks.items()}
    market = market_index(list(series.values()), len(dates))
    names = {row["game_id"]: row["name"] for row in read_csv(data_dir / "games.csv")}
    names["__dates__"] = dates
    details = {row["game_id"]: row for row in read_csv(data_dir / "details.csv")}
    flags = forward_flags(data_dir, dates)
    rows = observations(series, len(dates), market, floors, names, details, flags)
    seen = sorted({name for row in rows for name in row["signals"]} | set(flags))
    days = sorted({row["day"] for row in rows})
    middle = days[len(days) // 2] if days else 0
    result = dict(through=dates[-1] if dates else None, days_tested=len(days),
        games_tested=len({row["game_id"] for row in rows}), observations=len(rows),
        horizon_days=HORIZON, top_k=TOP_K, scout_max=SCOUT_MAX, hit_definitions=HITS,
        base_rates=hit_rates(rows),
        top_by_score=hit_rates(top_picks(rows, "score")),
        top_by_growth_7d=hit_rates(top_picks(rows, "growth_7d")),
        top_by_growth_3d=hit_rates(top_picks(rows, "growth_3d")),
        early_top_by_score=hit_rates(top_picks([row for row in rows if row["day"] < middle], "score")),
        late_top_by_score=hit_rates(top_picks([row for row in rows if row["day"] >= middle], "score")),
        bands=[dict(low=low, high=high - 1, observations=len(band), **hit_rates(band))
               for low, high in BANDS
               for band in [[row for row in rows if low <= row["score"] < high]]],
        signals=signal_lab(rows, seen))
    return result


def main():
    result = run()
    atomic_write(config.BACKTEST_OUTPUT, json.dumps(result, separators=(",", ":")) + "\n")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
