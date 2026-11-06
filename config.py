"""Tracker settings. All timestamps and comparison windows use UTC."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUTPUT = ROOT / "dashboard" / "data.json"
HISTORY_OUTPUT = ROOT / "dashboard" / "history.json"
BACKTEST_OUTPUT = ROOT / "dashboard" / "backtest.json"
ENDPOINT = "https://api.rolimons.com/games/v1/gamelist"
MIN_PLAYERS = 1_000  # "Traction" floor; the dashboard's player filter can go down to COLLECT_FLOOR.
COLLECT_FLOOR = 500
WINDOWS = (1, 6, 24, 48, 72, 168)
TOLERANCE_MINUTES = 20
WINDOW_TOLERANCE_MINUTES = {1: 20, 6: 90, 24: 180, 48: 180, 72: 180, 168: 180}
RETENTION_DAYS = 60
TOP_N = 100  # Display limit per lens; JSON retains all eligible games for sorting.
STALE_MINUTES = 45
SNAPSHOT_FILE_BYTES = 40 * 1024 * 1024
PUBLISHED_HISTORY_DAYS = 365  # Daily peaks sent to the dashboard for any-length comparisons.

# Roblox enrichment (release dates, genres, engagement). Best effort; see enrich.py.
ENRICH_BATCH = 50  # Roblox rejects larger universeIds lists on /v1/games.
ENRICH_REQUEST_DELAY = 0.15  # Seconds between /v1/games requests.
ENRICH_RESOLVE_DELAY = 1.1  # The place-to-universe endpoint allows 60 requests a minute.
ENRICH_RESOLVE_BUDGET = 45  # New lookups per run; cached forever, so this only slows a backlog.
ENRICH_REFRESH_HOURS = 6
