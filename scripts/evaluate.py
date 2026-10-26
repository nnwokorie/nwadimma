"""Run every test case N times and save accuracy + measured tokens/cost (evidence for the report).

    python scripts/evaluate.py default 2     # ITOJU_REASONING=default, 2 repeats
    ITOJU_REASONING=none python scripts/evaluate.py none 2
"""
import json
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from itoju.agents import triage  # noqa: E402
from itoju.config import REASONING_EFFORT, ROOT  # noqa: E402

label = sys.argv[1] if len(sys.argv) > 1 else REASONING_EFFORT
repeats = int(sys.argv[2]) if len(sys.argv) > 2 else 1
cases = json.loads((ROOT / "data" / "test_cases.json").read_text(encoding="utf-8"))


def one(case):
    out = triage(case["note"], case["location"], case.get("stage", ""))
    got = out.get("engine", {}).get("urgency") if out["status"] == "ok" else out["status"]
    return {"id": case["id"], "expected": case["expected"], "got": got, "correct": got == case["expected"],
            "totals": out["usage"]["totals"], "calls": out["usage"]["calls"],
            "trace": [{k: s[k] for k in ("agent", "kind", "summary")} for s in out["trace"]]}


with ThreadPoolExecutor(max_workers=4) as pool:
    runs = list(pool.map(one, [c for _ in range(repeats) for c in cases]))

billed = [r for r in runs if r["totals"]["model_calls"]]
summary = {
    "variant": label, "runs": len(runs), "accuracy": round(sum(r["correct"] for r in runs) / len(runs), 3),
    "wrong": [(r["id"], r["expected"], r["got"]) for r in runs if not r["correct"]],
}
for k in ("model_calls", "input_tokens", "cached_tokens", "output_tokens", "reasoning_tokens", "cost_usd", "latency_s"):
    vals = [r["totals"][k] for r in billed]
    summary[f"mean_{k}"] = round(statistics.mean(vals), 6)
summary["cache_hit_rate"] = round(sum(r["totals"]["cached_tokens"] for r in billed) / sum(r["totals"]["input_tokens"] for r in billed), 3)
by_agent = {}
for r in billed:
    for c in r["calls"]:
        a = by_agent.setdefault(c["agent"], {"calls": 0, "input": 0, "cached": 0, "output": 0, "reasoning": 0, "cost": 0.0})
        a["calls"] += 1; a["input"] += c["input_tokens"]; a["cached"] += c["cached_tokens"]
        a["output"] += c["output_tokens"]; a["reasoning"] += c["reasoning_tokens"]; a["cost"] += c["cost_usd"]
summary["per_agent_per_run"] = {a: {k: round(v / len(billed), 6) for k, v in d.items()} for a, d in by_agent.items()}

(ROOT / "results").mkdir(exist_ok=True)
(ROOT / "results" / f"eval_{label}.json").write_text(json.dumps({"summary": summary, "runs": runs}, indent=2), encoding="utf-8")
print(json.dumps(summary, indent=2))
