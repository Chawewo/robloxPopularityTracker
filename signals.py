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
# A sudden lasting jump, such as a game clearing Roblox's under-16 evaluation. Not scored.
STEP_RATIO = 3
STEP_BASE_DAYS = 7
STEP_MIN_OBSERVED = 3
FULL_MARKS = STREAK_POINTS[6] + GROWTH_WEIGHT * GROWTH_CAP


def stand_in(floor=None):
    """An absent day is below the floor. This value is used for scoring and is never displayed."""
    return (config.COLLECT_FLOOR if floor is None else floor) * 0.8


def level(peaks, index, width=3, floors=None):
    """The median of recent daily peaks, so one spike day doesn't move a game's level.

    floors gives each day's collection floor, so lowering the floor doesn't change what an
    absent day in the past meant.
    """
    start = max(0, index - width + 1)
    return median(stand_in(floors[start + offset] if floors else None) if value is None else value
                  for offset, value in enumerate(peaks[start:index + 1]))


def fill_unseen(peaks):
    """Hold a late-found game flat before its first reading.

    An absent day normally means below the floor. For a game found late it means nobody was
    looking, and reading that as a jump from nothing would score a 40,000-player game as a
    spike. Flat history gives it no credit or penalty until real movement is observed.
    """
    first = next((value for value in peaks if value is not None), None)
    filled, started = [], False
    for value in peaks:
        started = started or value is not None
        filled.append(value if started else first)
    return filled


def rising_streak(peaks, index, floors=None):
    """Consecutive days ending at index on which the 3-day level rose."""
    streak = 0
    position = index
    while (streak < STREAK_LIMIT and position > 0
           and level(peaks, position, floors=floors) > level(peaks, position - 1, floors=floors)):
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


def vs_market(peaks, index, days, market, floors=None):
    """Log growth over `days`, net of the market. None without enough history."""
    if index < days:
        return None
    return (log(level(peaks, index, floors=floors) / level(peaks, index - days, floors=floors))
            - (market[index] - market[index - days]))


def step_up(peaks, index):
    """How many times higher the last two days sit than the week before, or None.

    Both days must hold the gain, so a one-day spike does not count, and the game must have
    been seen beforehand, so a game Rolimons listed late does not look like a jump.
    """
    recent = peaks[max(index - 1, 0):index + 1]
    base = [value for value in peaks[max(index - 1 - STEP_BASE_DAYS, 0):max(index - 1, 0)] if value is not None]
    if len(recent) < 2 or None in recent or len(base) < STEP_MIN_OBSERVED:
        return None
    ratio = min(recent) / median(base)
    return round(ratio, 1) if ratio >= STEP_RATIO else None


def score(peaks, index, market, floors=None):
    """Score a game on the given day, using only data up to that day."""
    streak = rising_streak(peaks, index, floors)
    growth = vs_market(peaks, index, 3, market, floors)
    visible = max((value for value in peaks[max(0, index - 2):index + 1] if value is not None), default=0)
    spike = visible > SPIKE_RATIO * level(peaks, index, floors=floors)
    points = STREAK_POINTS[streak] + GROWTH_WEIGHT * min(max(growth or 0, 0), GROWTH_CAP)
    if spike:
        points -= SPIKE_PENALTY
    return dict(score=max(0, min(100, round(points / FULL_MARKS * 100))), streak=streak, spike=spike,
                vs_market_3=round(growth, 3) if growth is not None else None)
