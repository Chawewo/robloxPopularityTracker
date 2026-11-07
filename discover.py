"""Find games Rolimons has not listed yet, and read their player counts straight from Roblox.

Rolimons adds games late and in bursts, so a game can pass 100,000 players before the tracker
sees it. Every run this scans Roblox's own Explore lists, remembers every game found there in
data/discovered.csv, and keeps reading unlisted games directly from Roblox even after they
fall off every list. The same file records launch evidence: the first day a game was on any
list, the first day on Up-and-Coming, and any change to its maturity label.

Best effort: these endpoints are public but undocumented, and losing them must never stop the
Rolimons collection.
"""
import time
from datetime import date, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError

import charts
import config
from collect import append_rows, atomic_write, csv_text, read_csv, utc_text
from enrich import GAMES_URL, batches, fetch_json

ICON_URL = "https://thumbnails.roblox.com/v1/games/icons?universeIds={}&size=150x150&format=Webp&isCircular=false"
FIELDS = ["game_id", "universe_id", "name", "icon_url", "created", "first_chart", "first_up_and_coming",
          "listed", "maturity", "visits_at_first", "last_active"]
COVERAGE_FIELDS = ["date", "charted_5k", "missing_5k", "charted_500", "missing_500", "watching"]
MATURITY_FIELDS = ["timestamp", "game_id", "old", "new"]


def merge_seen(rows, seen, today, stamp):
    """Update the permanent record from one scan. Returns the maturity label changes."""
    changes = []
    for game_id, game in seen.items():
        row = rows.setdefault(game_id, dict.fromkeys(FIELDS, "") | dict(game_id=game_id, first_chart=today))
        row["universe_id"] = game["universe_id"] or row["universe_id"]
        row["name"] = game["name"] or row["name"]
        if game["players"] >= config.YOUNG_FLOOR:
            row["last_active"] = today
        if game["up_and_coming"] and not row["first_up_and_coming"]:
            row["first_up_and_coming"] = today
        if game["maturity"] and game["maturity"] != row["maturity"]:
            if row["maturity"]:
                changes.append(dict(timestamp=stamp, game_id=game_id, old=row["maturity"], new=game["maturity"]))
            row["maturity"] = game["maturity"]
    return changes


def watch_list(rows, listed, today):
    """Unlisted games still active recently, most recently active first."""
    cutoff = (date.fromisoformat(today) - timedelta(days=config.WATCH_DAYS)).isoformat()
    waiting = [row for game_id, row in rows.items()
               if game_id not in listed and row["universe_id"] and row["last_active"] >= cutoff]
    waiting.sort(key=lambda row: (row["last_active"], int(row["game_id"])), reverse=True)
    return waiting[:config.WATCH_MAX]


def fetch_live(universe_ids, fetch, pause):
    """Read current games from Roblox, keeping whatever arrived before any rate limit."""
    items = {}
    for batch in batches(universe_ids, config.ENRICH_BATCH):
        try:
            payload = fetch(GAMES_URL.format(",".join(batch)))
        except (HTTPError, URLError, TimeoutError, ValueError):
            break
        for item in (payload or {}).get("data", []):
            items[str(item["id"])] = item
        pause(config.WATCH_DELAY)
    return items


def fetch_icons(rows, fetch):
    """Rolimons supplies icons for listed games, so only unlisted ones need this."""
    missing = [row for row in rows if not row["icon_url"]][:config.ICON_BUDGET]
    by_universe = {row["universe_id"]: row for row in missing}
    for batch in batches(list(by_universe), 100):
        try:
            payload = fetch(ICON_URL.format(",".join(batch)))
        except (HTTPError, URLError, TimeoutError, ValueError):
            return
        for item in (payload or {}).get("data", []):
            row = by_universe.get(str(item.get("targetId")))
            if row and item.get("state") == "Completed" and item.get("imageUrl"):
                row["icon_url"] = item["imageUrl"]


def read_counts(watching, seen, now, today, fetch, pause):
    """Return {game_id: [name, players, icon]} for unlisted games above their floor."""
    young_from = utc_text(now - timedelta(days=config.YOUNG_DAYS))
    items = fetch_live([row["universe_id"] for row in watching], fetch, pause)
    found = {}
    for row in watching:
        item = items.get(row["universe_id"])
        if item:
            row["name"] = item.get("name") or row["name"]
            row["created"] = item["created"][:19] + "Z" if item.get("created") else row["created"]
            row["visits_at_first"] = row["visits_at_first"] or item.get("visits", "")
            players = item.get("playing") or 0
        elif row["game_id"] in seen:
            players = seen[row["game_id"]]["players"]  # The list's count is close enough for one run.
        else:
            continue
        if players >= config.YOUNG_FLOOR:
            row["last_active"] = today
        young = bool(row["created"]) and row["created"] >= young_from
        if players >= (config.YOUNG_FLOOR if young else config.COLLECT_FLOOR):
            found[row["game_id"]] = players
    return found


def record_coverage(data_dir, today, seen, listed, watching):
    """One row a day showing how much of Roblox's own lists Rolimons is missing."""
    count = lambda low, high: [game_id for game_id, game in seen.items() if low <= game["players"] < high]
    big, middle = count(5_000, float("inf")), count(500, 5_000)
    row = dict(date=today, charted_5k=len(big), missing_5k=sum(game_id not in listed for game_id in big),
               charted_500=len(middle), missing_500=sum(game_id not in listed for game_id in middle),
               watching=len(watching))
    path = Path(data_dir) / "coverage.csv"
    rows = [existing for existing in read_csv(path) if existing["date"] != today] + [row]
    atomic_write(path, csv_text(COVERAGE_FIELDS, rows))


def discover(listed, now, data_dir=config.DATA_DIR, fetch=fetch_json, pause=time.sleep):
    """Scan Roblox and return {game_id: [name, players, icon]} for games Rolimons is missing."""
    data_dir = Path(data_dir)
    stamp, today = utc_text(now), utc_text(now)[:10]
    positions, seen = charts.scan(fetch, pause)
    if not seen:
        return {}
    charts.write_positions(now, data_dir, positions)
    path = data_dir / "discovered.csv"
    rows = {row["game_id"]: row for row in read_csv(path)}
    changes = merge_seen(rows, seen, today, stamp)
    for game_id, row in rows.items():
        if game_id in listed and not row["listed"]:
            row["listed"] = today
    watching = watch_list(rows, listed, today)
    counts = read_counts(watching, seen, now, today, fetch, pause)
    fetch_icons([row for row in watching if row["game_id"] in counts], fetch)
    atomic_write(path, csv_text(FIELDS, [rows[key] for key in sorted(rows, key=int)]))
    if changes:
        append_rows(data_dir / "maturity_changes.csv", MATURITY_FIELDS, changes)
    record_coverage(data_dir, today, seen, listed, watching)
    return {game_id: [rows[game_id]["name"], players, rows[game_id]["icon_url"]] for game_id, players in counts.items()}
