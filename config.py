"""Tracker settings. All timestamps and comparison windows use UTC."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
OUTPUT = ROOT / "dashboard" / "data.json"
ENDPOINT = "https://api.rolimons.com/games/v1/gamelist"
MIN_PLAYERS = 1_000
COLLECT_FLOOR = 500
WINDOWS = (1, 6, 24)
TOLERANCE_MINUTES = 20
RETENTION_DAYS = 60
TOP_N = 100  # Display limit per lens; JSON retains all eligible games for sorting.
STALE_MINUTES = 45
SNAPSHOT_FILE_BYTES = 40 * 1024 * 1024
