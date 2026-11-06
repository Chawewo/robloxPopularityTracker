"""Replay history to measure how well the breakout score predicted real blow-ups.

For each past day this uses only data up to that day, ranks the games a scout could still
act on, then checks what happened next. A hit needs a sustained 3-day level, judged against
the highest level already visible on the day, so a spike that had already happened and a
one-day event are not counted as predictions.

The score's weights were chosen after seeing this same history, so results are optimistic.
Rerun this as history grows: later days are a fairer test. Run: python backtest.py
"""
import json
from statistics import median

import config
from collect import atomic_write
from rollup import read_daily
from signals import level, market_index, score, vs_market

LOOKBACK = 14
HORIZON = 14
TOP_K = 25
SCOUT_MIN = config.COLLECT_FLOOR
SCOUT_MAX = 15_000
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


def observations(series, length, market):
    rows = []
    for day in range(LOOKBACK, length - HORIZON):
        for game_id, peaks in series.items():
            if peaks[day] is None:
                continue
            current = level(peaks, day)
            if not SCOUT_MIN <= current <= SCOUT_MAX:
                continue
            visible = max(value for value in peaks[day - 2:day + 1] if value is not None)
            future = sustained_level(peaks, day + 1, day + HORIZON + 1)
            outcome = {name: future >= multiple * visible and future >= minimum
                       for name, (multiple, minimum) in HITS.items()}
            rows.append(dict(game_id=game_id, day=day, outcome=outcome,
                             score=score(peaks, day, market)["score"],
                             growth_7d=vs_market(peaks, day, 7, market),
                             growth_3d=vs_market(peaks, day, 3, market)))
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


def run(data_dir=config.DATA_DIR):
    dates, peaks = read_daily(data_dir)
    series = {game_id: [days.get(date) for date in dates] for game_id, days in peaks.items()}
    market = market_index(list(series.values()), len(dates))
    rows = observations(series, len(dates), market)
    middle = (LOOKBACK + len(dates) - HORIZON) // 2
    result = dict(through=dates[-1] if dates else None, days_tested=len({row["day"] for row in rows}),
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
               for band in [[row for row in rows if low <= row["score"] < high]]])
    return result


def main():
    result = run()
    atomic_write(config.BACKTEST_OUTPUT, json.dumps(result, separators=(",", ":")) + "\n")
    print(json.dumps(result, indent=1))


if __name__ == "__main__":
    main()
