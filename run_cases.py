"""Run the sample cases end to end and print a token/cost table (evidence for the report)."""
import json
import sys

from itoju.agents import triage
from itoju.config import ROOT

cases = json.loads((ROOT / "data" / "test_cases.json").read_text(encoding="utf-8"))
wanted = set(sys.argv[1:])
rows = []
for case in cases:
    if wanted and case["id"] not in wanted:
        continue
    out = triage(case["note"], case["location"], case.get("stage", ""))
    t = out["usage"]["totals"]
    urgency = out.get("engine", {}).get("urgency", "-")
    rows.append((case["id"], out["status"], urgency, t))
    print(f"\n=== {case['id']}: {out['status']} / {urgency}")
    for s in out["trace"]:
        print(f"  [{s['agent']}] {s['summary']}")
    if out.get("messages"):
        print("  CHW:", out["messages"].get("chw_summary"))
    if out.get("questions"):
        print("  ASK:", out["questions"])
    if out.get("referral"):
        print("  REFER:", out["referral"].get("recommended_facility"), "|", out["referral"].get("alternative_facility"))

print(f"\n{'case':28} {'status':12} {'urgency':16} {'calls':>5} {'input':>7} {'cached':>7} {'output':>7} {'cost $':>9}")
for cid, status, urg, t in rows:
    print(f"{cid:28} {status:12} {urg:16} {t['model_calls']:>5} {t['input_tokens']:>7} {t['cached_tokens']:>7} {t['output_tokens']:>7} {t['cost_usd']:>9.5f}")
