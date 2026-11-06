"""Breakout signals computed from a game's daily peaks.

A series is a list with one entry per collected day, oldest first. None means the tracker
ran that day and the game never reached the collection floor. The score's shape comes from
backtest.py: steady multi-day climbs predicted later blow-ups far better than any single
percentage change, because the largest one-off jumps are mostly spikes that fade.
"""
from math import log
from statistics import median

import config

# Convex on purpose: a 2-day rise is noise, while 5 or more straight days is the real signal.
STREAK_POINTS = (0, 0, 0, 1, 3, 6, 10, 12, 13, 14, 15)
STREAK_LIMIT = len(STREAK_POINTS) - 1
GROWTH_WEIGHT = 2
GROWTH_CAP = 1.5  # Log ratio; about 4.5x. Larger jumps earn nothing extra.
SPIKE_RATIO = 2.5
SPIKE_PENALTY = 3
FULL_MARKS = STREAK_POINTS[6] + GROWTH_WEIGHT * GROWTH_CAP


def stand_in():
    """An absent day is below the floor. This value is used for scoring and is never displayed."""
    return config.COLLECT_FLOOR * 0.8


def level(peaks, index, width=3):
    """The median of recent daily peaks, so one spike day doesn't move a game's level."""
    window = peaks[max(0, index - width + 1):index + 1]
    return median(stand_in() if value is None else value for value in window)


def rising_streak(peaks, index):
    """Consecutive days ending at index on which the 3-day level rose."""
    streak = 0
    position = index
    while streak < STREAK_LIMIT and position > 0 and level(peaks, position) > level(peaks, position - 1):
        streak += 1
        position -= 1
    return streak


def market_index(series, length):
    """A cumulative log index of the median game's daily move.

    The median stops one huge hit from defining the market, and comparing against it removes
    weekends and holidays from growth figures, including windows that aren't whole weeks.
    """
    index = [0.0]
    for day in range(1, length):
        moves = [log(peaks[day] / peaks[day - 1]) for peaks in series
                 if peaks[day] is not None and peaks[day - 1] is not None
                 and peaks[day] >= config.MIN_PLAYERS and peaks[day - 1] >= config.MIN_PLAYERS]
        index.append(index[-1] + (median(moves) if moves else 0.0))
    return index


def vs_market(peaks, index, days, market):
    """Log growth over `days`, net of the market. None without enough history."""
    if index < days:
        return None
    return log(level(peaks, index) / level(peaks, index - days)) - (market[index] - market[index - days])


def score(peaks, index, market):
    """Score a game on the given day, using only data up to that day."""
    streak = rising_streak(peaks, index)
    growth = vs_market(peaks, index, 3, market)
    visible = max((value for value in peaks[max(0, index - 2):index + 1] if value is not None), default=0)
    spike = visible > SPIKE_RATIO * level(peaks, index)
    points = STREAK_POINTS[streak] + GROWTH_WEIGHT * min(max(growth or 0, 0), GROWTH_CAP)
    if spike:
        points -= SPIKE_PENALTY
    return dict(score=max(0, min(100, round(points / FULL_MARKS * 100))), streak=streak, spike=spike,
                vs_market_3=round(growth, 3) if growth is not None else None)
