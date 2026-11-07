"""Replay history to measure which signals really came before blow-ups.

For each past day this uses only data up to that day, ranks the games a scout could still
act on, then checks what happened next. A hit needs a sustained 3-day level, judged against
the highest level already visible on the day, so a spike or one-day event is not counted as
a prediction. It must also beat the game's record so far: an old game bouncing back to last
month's level is a routine update cycle, not a blow-up, and counting those made a trained
model look far better than it was.

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
from rollup import read_daily, read_floors, read_late_games
from signals import fill_unseen, level, market_index, rising_streak, score, step_up, vs_market

LOOKBACK = 14
HORIZON = 14
TOP_K = 25
SCOUT_MIN = config.COLLECT_FLOOR
SCOUT_MAX = 15_000
OLD_FLOOR = 500  # Every game was absent below this until November 2026.
BIG = 5_000
EARLY_DAYS = 5  # Games seen this soon after tracking began may simply have been there already.
MAX_DAYS_TESTED = 90  # Keeps the daily refresh quick as history grows.
MIN_SAMPLES = 150  # Below this a signal's result is still mostly chance.
HITS = {"double": (2, 2_000), "triple": (3, 3_000), "five_x": (5, 5_000)}
BANDS = ((0, 15), (15, 40), (40, 70), (70, 101))


def outcomes(peaks, day):
    """What happened in the HORIZON days after `day`, as {hit name: True or False}."""
    visible = max(value for value in peaks[max(day - 2, 0):day + 1] if value is not None)
    record = max(value for value in peaks[:day + 1] if value is not None)
    future = sustained_level(peaks, day + 1, day + HORIZON + 1)
    return {name: future >= multiple * visible and future >= minimum and future > record
            for name, (multiple, minimum) in HITS.items()}


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
    discovered = read_csv(Path(data_dir) / "discovered.csv")
    first_scan = min((row["first_chart"] for row in discovered), default="")
    for row in discovered:
        day = index.get(row["first_chart"])
        if day is not None and row["first_chart"] != first_scan:  # The first scan found everything at once.
            for offset in range(3):
                flags["New to Roblox lists in past 3 days"].add((row["game_id"], day + offset))
    for row in read_csv(Path(data_dir) / "maturity_changes.csv"):
        day = index.get(row["timestamp"][:10])
        if day is not None:
            for offset in range(7):
                flags["Maturity label changed in past week"].add((row["game_id"], day + offset))
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


def observations(series, length, market, floors=None, names=None, details=None, flags=None, days=None):
    """One row per scouted game per day. `days` overrides the range, including unfinished days."""
    names, details, flags = names or {}, details or {}, flags or {}
    start = max(LOOKBACK, length - HORIZON - MAX_DAYS_TESTED)
    rows = []
    for day in (range(start, length - HORIZON) if days is None else days):
        stamp = date.fromisoformat(names["__dates__"][day]) if "__dates__" in names else None
        families, family_of = day_families(series, day, names["__dates__"], floors, names, details) if stamp else ({}, {})
        for game_id, peaks in series.items():
            if peaks[day] is None:
                continue
            current = level(peaks, day, floors=floors)
            if not SCOUT_MIN <= current <= SCOUT_MAX:
                continue
            outcome = outcomes(peaks, day)
            age = age_on(details.get(game_id, {}).get("created"), stamp) if stamp else None
            family = families.get(family_of.get(game_id))
            result = score(peaks, day, market, floors)
            growth_3d = vs_market(peaks, day, 3, market, floors)
            jump = step_up(peaks, day)
            signals = {
                "Step-up, game under 120 days old": jump is not None and age is not None and age <= 120,
                "Step-up, older game": jump is not None and age is not None and age > 120,
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


def caught(rows, outcome, key="score"):
    """How many games with this outcome ever made the daily top picks while it was still ahead."""
    winners = {row["game_id"] for row in rows if row["outcome"][outcome]}
    found = {row["game_id"] for row in top_picks(rows, key) if row["outcome"][outcome]}
    return dict(games=len(winners), caught=len(found))


def late_arrivals(series, floors):
    """Games the tracker first saw at a size that left no time to catch them.

    A game that first reads 2,000 or more skipped the floor entirely. Almost always that means
    the source listed it late, which no score can fix, so this measures the data's blind spot.
    """
    arrived = early = 0
    for peaks in series.values():
        first = next((day for day, value in enumerate(peaks) if value is not None and value >= OLD_FLOOR), None)
        if first is None or first < EARLY_DAYS:
            continue
        if any(level(peaks, day, floors=floors) >= BIG for day in range(first, len(peaks))):
            arrived += 1
            early += peaks[first] < 2_000
    return dict(games=arrived, seen_early=early)


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
    for game_id in read_late_games(data_dir) & set(series):
        series[game_id] = fill_unseen(series[game_id])
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
        five_x_caught=caught(rows, "five_x"), late_arrivals=late_arrivals(series, floors),
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
