"""One OpenAI client through Portkey, plus a ledger that meters every call in a run."""
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime

from openai import OpenAI

from . import config


class BudgetExceeded(RuntimeError):
    """Raised before a call that would push a run (or the day) past its spending cap."""


_client: OpenAI | None = None


def client() -> OpenAI:
    global _client
    if _client is None:
        if not config.PORTKEY_API_KEY:
            raise SystemExit("PORTKEY_API_KEY not found in this folder's .env or the folder above.")
        _client = OpenAI(
            api_key=config.PORTKEY_API_KEY,
            base_url=config.PORTKEY_BASE_URL,
            default_headers={"x-portkey-api-key": config.PORTKEY_API_KEY},
        )
    return _client


def cost_usd(input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
    uncached = input_tokens - cached_tokens
    return (
        uncached * config.PRICE_INPUT + cached_tokens * config.PRICE_CACHED + output_tokens * config.PRICE_OUTPUT
    ) / 1_000_000


def spent_today() -> float:
    if not config.RUN_LOG.exists():
        return 0.0
    today = date.today().isoformat()
    total = 0.0
    for line in config.RUN_LOG.read_text(encoding="utf-8").splitlines():
        rec = json.loads(line)
        if rec.get("timestamp", "").startswith(today):
            total += rec.get("totals", {}).get("cost_usd", 0.0)
    return total


@dataclass
class Ledger:
    """Token and cost meter for one triage run. Every model call goes through `chat`."""

    calls: list[dict] = field(default_factory=list)

    @property
    def totals(self) -> dict:
        keys = ("input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens")
        t = {k: sum(c[k] for c in self.calls) for k in keys}
        t["cost_usd"] = round(sum(c["cost_usd"] for c in self.calls), 6)
        t["model_calls"] = len(self.calls)
        t["latency_s"] = round(sum(c["latency_s"] for c in self.calls), 2)
        return t

    def _check_budget(self) -> None:
        t = self.totals
        if t["input_tokens"] + t["output_tokens"] >= config.MAX_TOKENS_PER_RUN:
            raise BudgetExceeded(f"Run stopped: token cap of {config.MAX_TOKENS_PER_RUN:,} reached.")
        if t["cost_usd"] >= config.MAX_COST_PER_RUN:
            raise BudgetExceeded(f"Run stopped: per-run cost cap of ${config.MAX_COST_PER_RUN} reached.")
        if spent_today() + t["cost_usd"] >= config.DAILY_BUDGET_USD:
            raise BudgetExceeded(f"Daily budget of ${config.DAILY_BUDGET_USD} used up. Try again tomorrow.")

    def chat(self, agent: str, messages: list[dict], tools: list | None = None, json_mode: bool = False):
        self._check_budget()
        kwargs: dict = {"model": config.MODEL, "messages": messages}
        if tools:
            kwargs["tools"] = tools
            kwargs["reasoning_effort"] = "none"  # gpt-6-luna on chat/completions only allows tools without reasoning
        elif config.REASONING_EFFORT != "default":
            kwargs["reasoning_effort"] = config.REASONING_EFFORT
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        start = time.perf_counter()
        resp = client().chat.completions.create(**kwargs)
        latency = time.perf_counter() - start
        u = resp.usage
        cached = getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        reasoning = getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", 0) or 0
        self.calls.append({
            "agent": agent,
            "input_tokens": u.prompt_tokens,
            "cached_tokens": cached,
            "output_tokens": u.completion_tokens,
            "reasoning_tokens": reasoning,
            "cost_usd": round(cost_usd(u.prompt_tokens, cached, u.completion_tokens), 6),
            "latency_s": round(latency, 2),
        })
        return resp.choices[0].message


def log_run(record: dict) -> None:
    config.LOG_DIR.mkdir(exist_ok=True)
    record = {"timestamp": datetime.now().isoformat(timespec="seconds"), **record}
    with open(config.RUN_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
