"""A trained model that runs beside the breakout score, without replacing it.

So far no model has beaten the hand-built score on real blow-ups. Results swing with how the
test is set up, and one short stretch of history cannot settle that. So once a day this model
is retrained on every finished outcome, writes its picks down, and later has those picks graded
against the score's picks on days neither has seen. Only that forward record can justify
switching. Nothing here changes the score.

Every feature uses data up to the day it describes. Genre and server size are today's values,
which is a small leak, but they rarely change. Chances are calibrated on held-out games, because
the raw model is overconfident at the top. Training needs scikit-learn (requirements-model.txt);
the rest of the tracker does not, and a failure here leaves yesterday's chances in place.

Run: python model.py        Train, predict and grade.
     python model.py --due  Exit 0 if a new day is ready, so CI can skip the install otherwise.
"""
import json
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import pstdev

import backtest
import config
from collect import append_rows, atomic_write, csv_text, read_csv
from rollup import read_daily, read_floors, read_late_games
from signals import fill_unseen, level, market_index, rising_streak, vs_market

TARGETS = ("double", "triple")
FEATURES = ["log_level", "growth_1", "growth_2", "growth_3", "growth_5", "growth_7", "growth_14", "acceleration",
            "streak", "up_share_14", "volatility", "spike", "vs_record", "days_tracked", "presence_14",
            "age", "log_age", "young", "verified", "group_owned", "genre", "max_players", "in_wave"]
TOP_KEPT = 100  # Picks logged per ranking; grading only reads the top 25.
TRAIN_START = 7
TRAIN_MAX_DAYS = 120  # Keeps the daily run quick, and Roblox's rules keep changing anyway.
MIN_TRAIN_DAYS = 10
# Held-out rows come from games the model never saw, on days at least a week from its training
# days. Holding out games alone flatters it, because games share the market of the same days.
TIME_BLOCKS = 4
TIME_GAP = 7
GAME_FOLDS = 3
MISSING = float("nan")


def features(peaks, day, market, floors, detail, genres, in_wave, stamp):
    """One game on one day, from data up to that day only."""
    start = max(0, day - 14)
    levels = [level(peaks, position, floors=floors) for position in range(start, day + 1)]
    seen = [position for position in range(day + 1) if peaks[position] is not None]
    moves = [math.log(peaks[position] / peaks[position - 1]) for position in range(max(1, day - 7), day + 1)
             if peaks[position] and peaks[position - 1]]

    def growth(days):
        value = vs_market(peaks, day, days, market, floors)
        return MISSING if value is None else value
    created = detail.get("created")
    age = (stamp - date.fromisoformat(created[:10])).days if created else None
    genre = detail.get("genre", "")
    return dict(log_level=math.log(levels[-1]), growth_1=growth(1), growth_2=growth(2), growth_3=growth(3),
        growth_5=growth(5), growth_7=growth(7), growth_14=growth(14), acceleration=2 * growth(3) - growth(7),
        streak=rising_streak(peaks, day, floors),
        up_share_14=sum(later > earlier for earlier, later in zip(levels, levels[1:])) / max(len(levels) - 1, 1),
        volatility=pstdev(moves) if len(moves) > 1 else MISSING,
        spike=max(value for value in peaks[max(day - 2, 0):day + 1] if value is not None) / levels[-1],
        vs_record=math.log(levels[-1] / max(peaks[position] for position in seen)),
        days_tracked=day - seen[0], presence_14=sum(peaks[position] is not None for position in range(start + 1, day + 1)) / max(day - start, 1),
        age=MISSING if age is None else age, log_age=MISSING if age is None else math.log(max(age, 1)),
        young=int(age is not None and age <= config.YOUNG_DAYS), verified=int(detail.get("verified") == "1"),
        group_owned=int(detail.get("creator_type") == "Group"), genre=genres.get(genre, -1),
        max_players=float(detail["max_players"]) if detail.get("max_players") else MISSING, in_wave=int(in_wave))


def table(rows, series, dates, market, floors, details):
    """Attach features to backtest rows."""
    genres = {name: position for position, name in enumerate(sorted({row.get("genre", "") for row in details.values()}))}
    for row in rows:
        row["features"] = features(series[row["game_id"]], row["day"], market, floors, details.get(row["game_id"], {}),
                                   genres, "In a clone wave" in row["signals"], date.fromisoformat(dates[row["day"]]))
    return rows


def load(data_dir):
    dates, peaks = read_daily(data_dir)
    series = {game_id: [days.get(day) for day in dates] for game_id, days in peaks.items()}
    market = market_index(list(series.values()), len(dates))
    for game_id in read_late_games(data_dir) & set(series):
        series[game_id] = fill_unseen(series[game_id])
    return dates, series, read_floors(data_dir), market


def fit(train, predict):
    """Return (chances for `predict`, chances for `train` from held-out games, raw held-out ranks).

    Ranks stay raw because calibration flattens predictions into steps, and ties would decide
    the top picks. Held-out chances are calibrated on the other half of the games, so the
    accuracy table on the dashboard is a real test: a calibrator judged on its own rows always
    looks perfect.
    """
    from numpy import array, array_split, full, intersect1d, isin, isnan, unique, where
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.isotonic import IsotonicRegression
    from sklearn.model_selection import GroupKFold

    def build():
        return HistGradientBoostingClassifier(max_depth=4, max_iter=200, learning_rate=0.05, min_samples_leaf=60,
                                              l2_regularization=1.0, random_state=7)
    matrix = lambda rows: array([[row["features"][name] for name in FEATURES] for row in rows], float)
    x_train, x_predict = matrix(train), matrix(predict)
    groups = array([row["game_id"] for row in train])
    days = array([row["day"] for row in train])
    results, held_out, ranks = {}, {}, {}
    for target in TARGETS:
        y = array([row["outcome"][target] for row in train], int)
        out_of_fold = full(len(train), float("nan"))
        if y.sum() < 20 or len(unique(groups)) < 5:
            raise ValueError("Not enough finished outcomes to train on")
        for block in array_split(unique(days), TIME_BLOCKS):
            allowed = where((days < block[0] - TIME_GAP) | (days > block[-1] + TIME_GAP))[0]
            judged = where(isin(days, block))[0]
            for fit_rows, test_rows in GroupKFold(n_splits=GAME_FOLDS).split(x_train, groups=groups):
                used, tested = intersect1d(fit_rows, allowed), intersect1d(test_rows, judged)
                out_of_fold[tested] = build().fit(x_train[used], y[used]).predict_proba(x_train[tested])[:, 1]
        assert not isnan(out_of_fold).any()
        half = isin(groups, unique(groups)[::2])
        honest = full(len(train), float("nan"))
        for fitted, judged in ((half, ~half), (~half, half)):
            honest[judged] = IsotonicRegression(out_of_bounds="clip").fit(out_of_fold[fitted], y[fitted]).predict(out_of_fold[judged])
        held_out[target] = honest
        calibrate = IsotonicRegression(out_of_bounds="clip").fit(out_of_fold, y)
        raw = build().fit(x_train, y).predict_proba(x_predict)[:, 1]
        results[target], results[f"{target}_raw"] = calibrate.predict(raw), raw
        ranks[target] = out_of_fold
    return results, held_out, ranks


def graded_rows(data_dir, dates, series):
    """Logged picks whose 14 days have finished, with what happened next."""
    position = {day: index for index, day in enumerate(dates)}
    picks = []
    for path in sorted((Path(data_dir) / "model" / "picks").glob("*.csv")):
        picks += read_csv(path)
    rows = []
    for pick in picks:
        day = position.get(pick["date"])
        peaks = series.get(pick["game_id"])
        if day is None or peaks is None or day + backtest.HORIZON >= len(dates):
            continue
        if all(value is None for value in peaks[:day + 1]):
            continue
        rows.append(dict(game_id=pick["game_id"], day=day, score=float(pick["score"]), model=float(pick["raw"]),
                         outcome=backtest.outcomes(peaks, day)))
    return rows


def grade(rows):
    """The score's top 25 against the model's on days neither had seen."""
    days = sorted({row["day"] for row in rows})
    return dict(days=len(days), score=backtest.hit_rates(backtest.top_picks(rows, "score")),
                model=backtest.hit_rates(backtest.top_picks(rows, "model")))


def held_out_report(train, ranks):
    rows = [dict(row, model=float(rank)) for row, rank in zip(train, ranks["double"])]
    return dict(rows=len(rows), games=len({row["game_id"] for row in rows}), days=len({row["day"] for row in rows}),
                base=backtest.hit_rates(rows), score=backtest.hit_rates(backtest.top_picks(rows, "score")),
                model=backtest.hit_rates(backtest.top_picks(rows, "model")))


def calibration(train, held_out):
    """Predicted against actual, so the dashboard can show whether its percentages are honest."""
    groups = defaultdict(list)
    for row, chance in zip(train, held_out["double"]):
        groups[next(low for low in (40, 20, 10, 5, 0) if chance * 100 >= low)].append((chance, row["outcome"]["double"]))
    return [dict(low=low, rows=len(pairs), predicted=round(sum(pair[0] for pair in pairs) / len(pairs) * 100, 1),
                 actual=round(sum(pair[1] for pair in pairs) / len(pairs) * 100, 1)) for low, pairs in sorted(groups.items())]


def due(data_dir=config.DATA_DIR, output=config.MODEL_OUTPUT):
    index = read_csv(Path(data_dir) / "daily_index.csv")
    try:
        through = json.loads(Path(output).read_text(encoding="utf-8")).get("through")
    except (OSError, ValueError):
        through = None
    return bool(index) and index[-1]["date"] != through


def run(data_dir=config.DATA_DIR, output=config.MODEL_OUTPUT):
    data_dir = Path(data_dir)
    dates, series, floors, market = load(data_dir)
    names = {row["game_id"]: row["name"] for row in read_csv(data_dir / "games.csv")}
    names["__dates__"] = dates
    details = {row["game_id"]: row for row in read_csv(data_dir / "details.csv")}
    last = len(dates) - 1
    finished = range(max(TRAIN_START, last - backtest.HORIZON - TRAIN_MAX_DAYS), last - backtest.HORIZON + 1)
    if len(finished) < MIN_TRAIN_DAYS:
        raise ValueError("Not enough finished days to train on")
    train = table(backtest.observations(series, len(dates), market, floors, names, details, days=finished),
                  series, dates, market, floors, details)
    today = table(backtest.observations(series, len(dates), market, floors, names, details, days=[last]),
                  series, dates, market, floors, details)
    chances, held_out, ranks = fit(train, today)
    latest = [dict(date=dates[last], game_id=row["game_id"], score=row["score"],
                   double=round(float(double) * 100, 1), triple=round(float(triple) * 100, 1), raw=round(float(raw), 5))
              for row, double, triple, raw in zip(today, chances["double"], chances["triple"], chances["double_raw"])]
    fields = ["date", "game_id", "score", "double", "triple", "raw"]
    atomic_write(data_dir / "model" / "latest.csv", csv_text(fields, latest))
    kept = {row["game_id"]: row for key in ("score", "raw")
            for row in sorted(latest, key=lambda row: (-row[key], row["game_id"]))[:TOP_KEPT]}
    picks = data_dir / "model" / "picks" / f"{dates[last][:7]}.csv"
    if not any(row["date"] == dates[last] for row in read_csv(picks)):
        append_rows(picks, fields, kept.values())
    result = dict(through=dates[last], trained_days=len(finished), trained_rows=len(train), games_scored=len(latest),
                  held_out=held_out_report(train, ranks), calibration=calibration(train, held_out),
                  forward=grade(graded_rows(data_dir, dates, series)))
    atomic_write(output, json.dumps(result, separators=(",", ":")) + "\n")
    return result


def main():
    if "--due" in sys.argv:
        sys.exit(0 if due() else 1)
    print(json.dumps(run(), indent=1))


if __name__ == "__main__":
    main()
