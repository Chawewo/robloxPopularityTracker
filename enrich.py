"""Add release dates, genres and engagement from Roblox's public game APIs.

Rolimons supplies only a name, a player count and an icon. Everything that needs a
release date comes from here. This step is best effort: a Roblox outage or rate limit
must never stop player-count collection, so main() reports failures and exits cleanly.
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import charts
import config
from collect import append_rows, atomic_write, csv_text, read_csv, utc_text

UNIVERSE_URL = "https://apis.roblox.com/universes/v1/places/{}/universe"
GAMES_URL = "https://games.roblox.com/v1/games?universeIds={}"
VOTES_URL = "https://games.roblox.com/v1/games/votes?universeIds={}"
BADGES_URL = "https://badges.roblox.com/v1/universes/{}/badges?limit=100&sortOrder=Asc"
GROUP_URL = "https://groups.roblox.com/v1/groups/{}"
UNIVERSE_FIELDS = ["game_id", "universe_id"]
DETAIL_FIELDS = ["game_id", "universe_id", "created", "updated", "genre", "subgenre", "creator",
                 "creator_type", "verified", "max_players", "visits", "favorites",
                 "up_votes", "down_votes", "fetched_at", "creator_id"]
UPDATE_FIELDS = ["game_id", "updated"]
BADGE_FIELDS = ["date", "game_id", "badges", "daily_awards"]
GROUP_FIELDS = ["date", "group_id", "members"]
ENGAGEMENT_FIELDS = ["fetched_at", "game_id", "visits", "favorites", "up_votes", "down_votes"]


def fetch_json(url, retries=2, pause=time.sleep):
    request = Request(url, headers={"User-Agent": "RobloxRisingTracker/1.0", "Accept": "application/json"})
    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=30) as response:
                return json.load(response)
        except HTTPError as error:
            # A 4xx other than rate limiting means this id will never resolve.
            if error.code == 404 or error.code == 400:
                return None
            if error.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise
            retry_after = error.headers.get("Retry-After", "") if error.headers else ""
            pause(int(retry_after) if retry_after.isdigit() else 2 ** (attempt + 2))
        except (URLError, TimeoutError):
            if attempt == retries:
                raise
            pause(2 ** (attempt + 2))
    return None


def resolve_universes(place_ids, known, budget, fetch=fetch_json, pause=time.sleep):
    """Map new place ids to universes. Universe ids never change, so this cache is permanent."""
    found = {}
    for place_id in place_ids:
        if place_id in known or len(found) >= budget:
            continue
        try:
            payload = fetch(UNIVERSE_URL.format(place_id))
        except HTTPError as error:
            if error.code == 429:
                break  # Keep what resolved; the next run continues from the permanent cache.
            raise
        if payload and payload.get("universeId"):
            found[place_id] = str(payload["universeId"])
        pause(config.ENRICH_RESOLVE_DELAY)
    return found


def batches(values, size):
    for start in range(0, len(values), size):
        yield values[start:start + size]


def fetch_details(universe_ids, fetch=fetch_json, pause=time.sleep):
    details, votes = {}, {}
    for batch in batches(universe_ids, config.ENRICH_BATCH):
        joined = ",".join(batch)
        for url, target in ((GAMES_URL, details), (VOTES_URL, votes)):
            payload = fetch(url.format(joined))
            for item in (payload or {}).get("data", []):
                target[str(item["id"])] = item
            pause(config.ENRICH_REQUEST_DELAY)
    return details, votes


def detail_row(game_id, universe_id, item, vote, stamp):
    creator = item.get("creator") or {}
    stamp_of = lambda key: item[key][:19] + "Z" if item.get(key) else ""
    return dict(game_id=game_id, universe_id=universe_id, created=stamp_of("created"),
        updated=stamp_of("updated"), genre=item.get("genre_l1") or item.get("genre") or "",
        subgenre=item.get("genre_l2") or "", creator=creator.get("name", ""),
        creator_type=creator.get("type", ""), verified=int(bool(creator.get("hasVerifiedBadge"))),
        max_players=item.get("maxPlayers", ""), visits=item.get("visits", ""),
        favorites=item.get("favoritedCount", ""), up_votes=(vote or {}).get("upVotes", ""),
        down_votes=(vote or {}).get("downVotes", ""), fetched_at=stamp, creator_id=creator.get("id", ""))


def current_game_ids(data_dir):
    """Games in the newest completed snapshot, read from the end of the latest partition."""
    snapshots = sorted((Path(data_dir) / "snapshots").glob("*.csv"), key=lambda p: (p.stem[:7], p.stem))
    manifest = read_csv(Path(data_dir) / "collections.csv")
    if not snapshots or not manifest:
        return []
    latest = max(row["timestamp"] for row in manifest)
    for path in reversed(snapshots):
        ids = [row["game_id"] for row in read_csv(path) if row["timestamp"] == latest]
        if ids:
            return ids
    return []


def enrich(now, data_dir=config.DATA_DIR, fetch=fetch_json, pause=time.sleep):
    data_dir = Path(data_dir)
    stamp = utc_text(now)
    metadata = read_csv(data_dir / "games.csv")
    universes = {row["game_id"]: row["universe_id"] for row in read_csv(data_dir / "universes.csv")}
    details = {row["game_id"]: row for row in read_csv(data_dir / "details.csv")}
    current = current_game_ids(data_dir)
    # Charts also reveal universe ids, which saves rate-limited lookups for those games.
    free = charts.record(now, data_dir, fetch)
    known_before = len(universes)
    universes.update({key: value for key, value in free.items() if key not in universes})
    charted = len(universes) - known_before
    # Newest place ids first within listed games: a new riser needs its release date most urgently.
    order = sorted(current, key=int, reverse=True) + [row["game_id"] for row in metadata]
    found = resolve_universes(list(dict.fromkeys(order)), universes, config.ENRICH_RESOLVE_BUDGET, fetch, pause)
    if found or charted:
        universes.update(found)
        atomic_write(data_dir / "universes.csv", csv_text(UNIVERSE_FIELDS,
            [dict(game_id=key, universe_id=universes[key]) for key in sorted(universes, key=int)]))
    newest = max((row["fetched_at"] for row in details.values()), default=None)
    refresh_due = newest is None or now - datetime.fromisoformat(newest.replace("Z", "+00:00")) >= timedelta(hours=config.ENRICH_REFRESH_HOURS)
    wanted = [key for key in universes if key not in details]
    if refresh_due:
        wanted += [key for key in current if key in universes and key in details]
    wanted = list(dict.fromkeys(wanted))
    if not wanted:
        return dict(resolved=len(found) + charted, refreshed=0)
    items, votes = fetch_details([universes[key] for key in wanted], fetch, pause)
    written, updates = [], []
    for key in wanted:
        universe = universes[key]
        if universe in items:
            previous = details.get(key, {}).get("updated")
            details[key] = detail_row(key, universe, items[universe], votes.get(universe), stamp)
            written.append(details[key])
            if previous and previous != details[key]["updated"]:
                updates.append(dict(game_id=key, updated=details[key]["updated"]))
    if written:
        atomic_write(data_dir / "details.csv", csv_text(DETAIL_FIELDS,
            [details[key] for key in sorted(details, key=int)]))
    if updates:
        append_rows(data_dir / "updates.csv", UPDATE_FIELDS, updates)
    if refresh_due and written:
        append_engagement(written, stamp, data_dir)
    return dict(resolved=len(found) + charted, refreshed=len(written))


def sampled_today(path, key, today):
    return {row[key] for row in read_csv(path) if row["date"] == today}


def sample_badges(now, data_dir=config.DATA_DIR, fetch=fetch_json, pause=time.sleep):
    """Daily awards of a game's most-earned badge, a rough count of new players.

    Many games award a badge on first join, so that badge's daily count tracks arrivals. It is
    a proxy: a game without one reports its next most common badge. Sampled once a day per
    game, newest games first, spread across runs to stay inside the rate limit.
    """
    data_dir = Path(data_dir)
    today = utc_text(now)[:10]
    path = data_dir / "badges" / f"{today[:7]}.csv"
    done = sampled_today(path, "game_id", today)
    universes = {row["game_id"]: row["universe_id"] for row in read_csv(data_dir / "universes.csv")}
    queue = [key for key in sorted(current_game_ids(data_dir), key=int, reverse=True)
             if key in universes and key not in done][:config.ENRICH_BADGE_BUDGET]
    rows = []
    for game_id in queue:
        try:
            payload = fetch(BADGES_URL.format(universes[game_id]))
        except HTTPError as error:
            if error.code == 429:
                break
            raise
        badges = (payload or {}).get("data", [])
        awards = [badge.get("statistics", {}).get("pastDayAwardedCount", 0) for badge in badges]
        rows.append(dict(date=today, game_id=game_id, badges=len(badges), daily_awards=max(awards, default=0)))
        pause(config.ENRICH_BADGE_DELAY)
    if rows:
        append_rows(path, BADGE_FIELDS, rows)
    return len(rows)


def sample_groups(now, data_dir=config.DATA_DIR, fetch=fetch_json):
    """Member counts for the groups behind listed games, a measure of community growth."""
    data_dir = Path(data_dir)
    today = utc_text(now)[:10]
    path = data_dir / "groups" / f"{today[:7]}.csv"
    done = sampled_today(path, "group_id", today)
    listed = set(current_game_ids(data_dir))
    groups = dict.fromkeys(row["creator_id"] for row in read_csv(data_dir / "details.csv")
                           if row.get("creator_type") == "Group" and row.get("creator_id") and row["game_id"] in listed)
    queue = sorted(set(groups) - done, key=int, reverse=True)[:config.ENRICH_GROUP_BUDGET]
    rows = []
    for group_id in queue:
        try:
            payload = fetch(GROUP_URL.format(group_id))
        except HTTPError as error:
            if error.code == 429:
                break
            raise
        if payload and "memberCount" in payload:
            rows.append(dict(date=today, group_id=group_id, members=payload["memberCount"]))
    if rows:
        append_rows(path, GROUP_FIELDS, rows)
    return len(rows)


def append_engagement(rows, stamp, data_dir):
    """Keep a small, permanent visits/favorites/likes history. Only currently listed games matter."""
    path = Path(data_dir) / "engagement" / f"{stamp[:7]}.csv"
    existing = read_csv(path)
    if any(row["fetched_at"][:10] == stamp[:10] for row in existing):
        return  # One sample per UTC day is enough for velocity and keeps the file small.
    existing.extend({field: row[field] if field != "fetched_at" else stamp for field in ENGAGEMENT_FIELDS}
                    for row in rows if row["visits"] != "")
    atomic_write(path, csv_text(ENGAGEMENT_FIELDS, existing))


def main():
    now = datetime.now(timezone.utc)
    # Separate steps: losing one source should not cost the others.
    for name, step in (("enrich", enrich), ("badges", sample_badges), ("groups", sample_groups)):
        try:
            print(f"{name}: {step(now)}")
        except Exception as error:  # Deliberately broad: enrichment must not fail the collection run.
            print(f"{name} skipped: {type(error).__name__}: {error}", file=sys.stderr)


if __name__ == "__main__":
    main()
