"""Settings, key loading, and the per-run safety limits."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

# Look for .env in the repo first, then one folder up (where the teaching staff keep theirs).
# load_dotenv never overwrites a value that is already set, so the repo copy wins.
load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

PORTKEY_API_KEY = os.getenv("PORTKEY_API_KEY")
PORTKEY_BASE_URL = os.getenv("PORTKEY_BASE_URL", "https://api.portkey.ai/v1")
MODEL = os.getenv("ITOJU_MODEL", "gpt-6-luna")  # the only model allowed on the course key

# Facility register: "test" = Lagos, Kano and FCT (ships with the repo); "full" = all 37 states.
DATA_MODE = os.getenv("ITOJU_DATA", "test")
FACILITY_CSV = ROOT / "data" / ("facilities_full.csv" if DATA_MODE == "full" else "facilities_test.csv")

# Prices in USD per 1M tokens. Set these to the course's published gpt-6-luna rates.
PRICE_INPUT = float(os.getenv("PRICE_INPUT_PER_M", "0.25"))
PRICE_CACHED = float(os.getenv("PRICE_CACHED_PER_M", "0.025"))
PRICE_OUTPUT = float(os.getenv("PRICE_OUTPUT_PER_M", "2.00"))

# Guardrails against runaway agents and overspending.
MAX_NOTE_CHARS = 2000          # longest case note we accept
MAX_TOOL_ROUNDS = 4            # tool-calling turns the protocol agent may take
MAX_TOKENS_PER_RUN = 40_000    # hard stop for one triage run (input + output)
MAX_COST_PER_RUN = 0.05        # USD; run is stopped if it would exceed this
DAILY_BUDGET_USD = float(os.getenv("ITOJU_DAILY_BUDGET", "2.00"))

LOG_DIR = ROOT / "logs"
RUN_LOG = LOG_DIR / "runs.jsonl"
