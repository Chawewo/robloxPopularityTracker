"""Record which games Roblox itself is promoting.

Chart placement drives discovery, so it can lead player counts. Each game's position is kept
per chart per UTC day. These endpoints are public but undocumented, so this step is best
effort and any change on Roblox's side only leaves the columns blank.
"""
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from uuid import uuid4

import config
from collect import atomic_write, csv_text, read_csv, utc_text

SORTS_URL = "https://apis.roblox.com/explore-api/v1/get-sorts?sessionId={}&device={}&country={}{}"
CHART_FIELDS = ["chart", "game_id", "universe_id", "best_rank", "last_rank", "samples"]
CHARTS = ("top-trending", "up-and-coming", "top-revisited", "top-earning", "top-rated", "fun-with-friends")
DEVICES = ("computer", "high_end_phone")  # Most Roblox play is on phones, and charts differ by device.
UP_AND_COMING = "up-and-coming"


def scan(fetch, pause=time.sleep):
    """Read Roblox's Explore lists. Returns (chart positions, every game found).

    Positions are {(chart, game_id): (rank, universe_id)} using the best rank across the two
    worldwide device lists, so chart history stays comparable as discovery widens. Every other
    list only feeds discovery. A failure keeps whatever already arrived.
    """
    positions, seen = {}, {}
    for device, country in config.DISCOVERY_SCANS:
        token = ""
        session = uuid4()
        for _ in range(config.CHART_PAGES):
            try:
                payload = fetch(SORTS_URL.format(session, device, country, f"&sortsPageToken={token}" if token else ""))
            except (HTTPError, URLError, TimeoutError, ValueError):
                return positions, seen
            for sort in (payload or {}).get("sorts", []):
                charted = sort.get("sortId") in CHARTS and device in DEVICES and country == "all"
                rank = 0
                for game in sort.get("games") or []:
                    if game.get("isSponsored") or not game.get("rootPlaceId"):
                        continue  # Paid placement says nothing about organic momentum.
                    rank += 1
                    game_id, universe = str(game["rootPlaceId"]), str(game.get("universeId", ""))
                    found = seen.setdefault(game_id, dict(universe_id=universe, name=game.get("name", ""),
                                                          players=0, maturity=game.get("contentMaturity", ""),
                                                          up_and_coming=False))
                    found["players"] = max(found["players"], game.get("playerCount") or 0)
                    found["up_and_coming"] = found["up_and_coming"] or sort.get("sortId") == UP_AND_COMING
                    key = (sort.get("sortId"), game_id)
                    if charted and (key not in positions or rank < positions[key][0]):
                        positions[key] = (rank, universe)
            token = (payload or {}).get("nextSortsPageToken")
            pause(config.DISCOVERY_DELAY)
            if not token:
                break
    return positions, seen


def fetch_positions(fetch, pause=time.sleep):
    return scan(fetch, pause)[0]


def record(now, data_dir, fetch, pause=time.sleep):
    """Scan and save positions. Returns {game_id: universe_id}."""
    return write_positions(now, data_dir, fetch_positions(fetch, pause))


def write_positions(now, data_dir, positions):
    """Merge positions into today's file. Returns {game_id: universe_id}."""
    if not positions:
        return {}
    path = Path(data_dir) / "charts" / f"{utc_text(now)[:10]}.csv"
    rows = {(row["chart"], row["game_id"]): row for row in read_csv(path)}
    for (chart, game_id), (rank, universe) in positions.items():
        row = rows.get((chart, game_id))
        rows[(chart, game_id)] = dict(chart=chart, game_id=game_id, universe_id=universe,
            best_rank=min(rank, int(row["best_rank"])) if row else rank, last_rank=rank,
            samples=int(row["samples"]) + 1 if row else 1)
    atomic_write(path, csv_text(CHART_FIELDS, sorted(rows.values(), key=lambda row: (row["chart"], int(row["last_rank"])))))
    return {game_id: universe for (_, game_id), (_, universe) in positions.items() if universe}


def read_recent(data_dir, days):
    """Return rows from the newest `days` daily files as (date, rows), oldest first."""
    paths = sorted((Path(data_dir) / "charts").glob("*.csv"))[-days:]
    return [(path.stem, read_csv(path)) for path in paths]
