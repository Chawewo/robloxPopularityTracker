"""One request per run; publish a snapshot only after validating the full response."""
import csv
import io
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen

import config


def utc_text(value):
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="")
    os.replace(temporary, path)


def read_csv(path):
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def csv_text(fields, rows):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


MANIFEST_FIELDS = ["timestamp", "games", "floor", "total_players", "discovered"]
CHANGE_FIELDS = ["timestamp", "game_id", "field", "old", "new"]


def append_rows(path, fields, rows):
    """Append to a CSV, writing a header for a new file. Avoids rewriting large histories."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def validate(payload):
    games = payload.get("games") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or payload.get("success", True) is not True or not isinstance(games, dict) or not games:
        raise ValueError("Rolimons returned an unsuccessful or empty games response")
    result = {}
    for game_id, item in games.items():
        if (not str(game_id).isdigit() or not isinstance(item, list) or len(item) < 3
                or not isinstance(item[0], str) or not item[0].strip()
                or type(item[1]) is not int or item[1] < 0
                or not isinstance(item[2], str)):
            raise ValueError(f"Unexpected game record: {game_id}")
        result[str(game_id)] = item[:3]
    return result


def young_games(data_dir, now):
    """Return (games created within YOUNG_DAYS, every game with a known release date)."""
    cutoff = utc_text(now - timedelta(days=config.YOUNG_DAYS))
    details = read_csv(Path(data_dir) / "details.csv")
    return {row["game_id"] for row in details if row["created"] >= cutoff}, {row["game_id"] for row in details}


def save_snapshot(payload, now, data_dir=config.DATA_DIR, extra=None):
    """Store the listed games at or above their floor, plus `extra` games found elsewhere."""
    games = validate(payload)
    timestamp = utc_text(now)
    data_dir = Path(data_dir)
    manifest = read_csv(data_dir / "collections.csv")
    if any(row["timestamp"] == timestamp for row in manifest):
        return 0
    young, known = young_games(data_dir, now)
    selected = {key: value for key, value in games.items()
                if value[1] >= (config.YOUNG_FLOOR if key in young else config.COLLECT_FLOOR)}
    # Enrichment reads this in the same run to learn which small games are young. It is a queue, not history.
    atomic_write(data_dir / "below_floor.csv", csv_text(["game_id", "players"], [
        dict(game_id=key, players=value[1]) for key, value in sorted(games.items(), key=lambda item: -item[1][1])
        if config.YOUNG_FLOOR <= value[1] < config.COLLECT_FLOOR and key not in known and int(key) >= config.RECENT_PLACE_ID]))
    selected.update(extra or {})
    metadata_path = data_dir / "games.csv"
    metadata = {row["game_id"]: row for row in read_csv(metadata_path)}
    changed = False
    changes = []
    for game_id, (name, players, icon) in selected.items():
        old = metadata.get(game_id)
        icon = icon or (old["icon_url"] if old else "")
        row = dict(game_id=game_id, name=name, icon_url=icon,
                   first_seen=old["first_seen"] if old else timestamp)
        if old != row:
            metadata[game_id] = row
            changed = True
        if old and old["name"] != name:
            changes.append(dict(timestamp=timestamp, game_id=game_id, field="name", old=old["name"], new=name))
        if old and old["icon_url"] and old["icon_url"] != icon:
            changes.append(dict(timestamp=timestamp, game_id=game_id, field="icon", old="", new=""))
    if changes:
        append_rows(data_dir / "changes.csv", CHANGE_FIELDS, changes)
    if changed:
        atomic_write(metadata_path, csv_text(
            ["game_id", "name", "icon_url", "first_seen"],
            [metadata[key] for key in sorted(metadata, key=int)]))
    snapshot_dir = data_dir / "snapshots"
    path = snapshot_dir / f"{timestamp[:7]}.csv"
    part = 1
    # Keep monthly partitions below GitHub's per-file limit as history grows.
    while path.exists() and path.stat().st_size >= config.SNAPSHOT_FILE_BYTES:
        part += 1
        path = snapshot_dir / f"{timestamp[:7]}-{part:03d}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        if not exists:
            writer.writerow(["timestamp", "game_id", "players"])
        writer.writerows((timestamp, key, value[1]) for key, value in selected.items())
    # A completion manifest also represents valid snapshots with no qualifying games.
    manifest.append(dict(timestamp=timestamp, games=len(selected), floor=config.COLLECT_FLOOR,
                         total_players=sum(value[1] for value in games.values()), discovered=len(extra or ())))
    atomic_write(data_dir / "collections.csv", csv_text(MANIFEST_FIELDS, manifest))
    return len(selected)


def newest_timestamp(path):
    """Read only the final row, since rows are appended in time order."""
    with path.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - 256))
        lines = stream.read().decode("utf-8", "ignore").strip().splitlines()
    return lines[-1].split(",")[0] if lines else ""


def prune(now, data_dir=config.DATA_DIR):
    """Delete raw partitions once every row has expired, preserving first_seen values.

    Whole files only: trimming rows would rewrite a 40 MB file on every run, adding a full
    copy to Git history each time. Expired rows in a live partition are simply ignored.
    """
    cutoff = utc_text(now - timedelta(days=config.RETENTION_DAYS))
    for path in (Path(data_dir) / "snapshots").glob("*.csv"):
        if path.stem[:7] > cutoff[:7]:
            continue
        newest = newest_timestamp(path)
        if newest == "timestamp" or newest < cutoff:
            path.unlink()
    path = Path(data_dir) / "collections.csv"
    if path.exists():
        rows = read_csv(path)
        retained = [row for row in rows if row["timestamp"] >= cutoff]
        if retained != rows:
            atomic_write(path, csv_text(MANIFEST_FIELDS, retained))


def find_unlisted(payload, now):
    """Games Rolimons is missing. Losing Roblox's lists must never stop this collection."""
    try:
        from discover import discover  # discover.py imports this module's CSV helpers.
        return discover(validate(payload), now)
    except Exception as error:  # Deliberately broad, for the same reason as enrich.py.
        print(f"discovery skipped: {type(error).__name__}: {error}", file=sys.stderr)
        return {}


def main():
    request = Request(config.ENDPOINT, headers={
        "User-Agent": "Mozilla/5.0 (compatible; RobloxRisingTracker/1.0)",
        "Referer": "https://www.rolimons.com/",
    })
    with urlopen(request, timeout=45) as response:
        payload = json.load(response)
    now = datetime.now(timezone.utc)
    count = save_snapshot(payload, now, extra=find_unlisted(payload, now))
    # Roll up before pruning, so no raw day can be deleted without leaving its daily peak.
    from rollup import rollup  # rollup.py imports this module's CSV helpers.
    rollup()
    prune(now)
    print(f"Collected {count} games at {utc_text(now)}")


if __name__ == "__main__":
    main()
