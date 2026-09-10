"""One request per run; publish a snapshot only after validating the full response."""
import csv
import io
import json
import os
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


def save_snapshot(payload, now, data_dir=config.DATA_DIR):
    games = validate(payload)
    timestamp = utc_text(now)
    data_dir = Path(data_dir)
    manifest = read_csv(data_dir / "collections.csv")
    if any(row["timestamp"] == timestamp for row in manifest):
        return 0
    selected = {key: value for key, value in games.items() if value[1] >= config.COLLECT_FLOOR}
    metadata_path = data_dir / "games.csv"
    metadata = {row["game_id"]: row for row in read_csv(metadata_path)}
    changed = False
    for game_id, (name, players, icon) in selected.items():
        old = metadata.get(game_id)
        row = dict(game_id=game_id, name=name, icon_url=icon,
                   first_seen=old["first_seen"] if old else timestamp)
        if old != row:
            metadata[game_id] = row
            changed = True
    if changed:
        atomic_write(metadata_path, csv_text(
            ["game_id", "name", "icon_url", "first_seen"],
            [metadata[key] for key in sorted(metadata, key=int)]))
    path = data_dir / "snapshots" / f"{timestamp[:7]}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        if not exists:
            writer.writerow(["timestamp", "game_id", "players"])
        writer.writerows((timestamp, key, value[1]) for key, value in selected.items())
    # A completion manifest also represents valid snapshots with no qualifying games.
    manifest.append(dict(timestamp=timestamp, games=len(selected)))
    atomic_write(data_dir / "collections.csv", csv_text(["timestamp", "games"], manifest))
    return len(selected)


def prune(now, data_dir=config.DATA_DIR):
    """Bound working-tree history, preserving original metadata first_seen values."""
    cutoff = utc_text(now - timedelta(days=config.RETENTION_DAYS))
    for path in (Path(data_dir) / "snapshots").glob("*.csv"):
        rows = read_csv(path)
        retained = [row for row in rows if row["timestamp"] >= cutoff]
        if not retained:
            path.unlink()
        elif len(rows) != len(retained):
            atomic_write(path, csv_text(["timestamp", "game_id", "players"], retained))
    path = Path(data_dir) / "collections.csv"
    if path.exists():
        rows = read_csv(path)
        retained = [row for row in rows if row["timestamp"] >= cutoff]
        if retained != rows:
            atomic_write(path, csv_text(["timestamp", "games"], retained))


def main():
    request = Request(config.ENDPOINT, headers={
        "User-Agent": "Mozilla/5.0 (compatible; RobloxRisingTracker/1.0)",
        "Referer": "https://www.rolimons.com/",
    })
    with urlopen(request, timeout=45) as response:
        payload = json.load(response)
    now = datetime.now(timezone.utc)
    count = save_snapshot(payload, now)
    prune(now)
    print(f"Collected {count} games at {utc_text(now)}")


if __name__ == "__main__":
    main()
