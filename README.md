# Itoju — sick-child triage for Nigeria's community health workers

*Itoju* (Yoruba: "care") helps a community health worker turn a quick case note — in English or Nigerian Pidgin —
into a WHO/UNICEF iCCM decision for a child aged 2 months to 5 years: **refer urgently, treat at home, or home care**,
with exact doses and the nearest public facility from the GRID3 Nigeria health-facility register.

The AI never decides an urgency level or a dose by itself. Four agents read, double-check, and explain; the
protocol and dosing table are plain code, and a final check blocks any drug or dose the protocol did not return.

## Run it (about 3 minutes)

Needs Python 3.10+.

```bash
git clone <this repo> itoju && cd itoju
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5050**, click a sample case (e.g. *Convulsions*), and press **Triage this child**.

### API key

The app reads `PORTKEY_API_KEY` from a `.env` file. It looks **in this folder first, then one folder above**
(where the teaching staff keep theirs), so no setup is needed if `../.env` exists. Otherwise:

```bash
cp .env.example .env   # then paste your PORTKEY_API_KEY
```

Model: `gpt-6-luna` through Portkey (the course key). Never commit `.env`.

### Test data vs full data

| Mode | File | Coverage | How |
|---|---|---|---|
| `test` (default) | `data/facilities_test.csv` (in repo, 0.5 MB) | Lagos, Kano, FCT — 4,381 facilities | nothing to do |
| `full` | `data/facilities_full.csv` (6 MB, not in repo) | All 36 states + FCT — 45,808 facilities | see below |

```bash
python scripts/build_facilities.py      # downloads the GRID3 register from HDX and builds both CSVs
ITOJU_DATA=full python app.py           # Windows PowerShell: $env:ITOJU_DATA="full"; python app.py
```

### Reproduce the measurements

```bash
python run_cases.py            # runs the 8 cases in data/test_cases.json, prints the trace and a token/cost table
python run_cases.py injection  # run one case by id
```

Every run is appended to `logs/runs.jsonl` with per-agent input, cached, output and reasoning tokens and cost.

## How it works

```
note ─▶ Input guard (code) ─▶ 1 Intake agent (LLM) ─▶ 2 Safety reviewer (LLM + danger-word net)
     ─▶ 3 Protocol agent (LLM + iCCM tools) ─▶ Output check (code) ─▶ 4 Referral agent (LLM + facility tool, if referring)
```

| Agent | Job | Tools / guard |
|---|---|---|
| Input guard | Blocks prompt-injection text and over-long notes before any AI call | regex rules |
| Intake | Free text (English/Pidgin) → structured findings; unknowns stay empty | JSON schema validated by Pydantic, 1 retry |
| Safety reviewer | Finds danger signs the intake missed; may only *escalate* | Pidgin/English keyword net + LLM second opinion |
| Protocol | Explains the decision for the worker and caregiver (English + Pidgin) | `classify_case()`, `get_dose(drug)` — read verified findings, cannot be altered by the model |
| Output check | Every drug name and dose in the text must match the engine, else safe template | code |
| Referral | Picks and explains nearest public hospital / PHC, writes SBAR referral note | `find_nearest_facility()`; invented facility names are removed |

Limits: 4 tool rounds per agent, 40k tokens and $0.05 per run, $2/day budget (`ITOJU_DAILY_BUDGET`).

## Files

```
app.py                 Flask server (UI + /api/triage)
itoju/agents.py        the four agents and the orchestrator
itoju/iccm.py          WHO/UNICEF iCCM rules as code
itoju/safety.py        input guard, danger-word net, output check
itoju/facilities.py    nearest-facility search (GRID3)
itoju/llm.py           Portkey client, token/cost ledger, budget caps
prompts/*.md           system prompts for each agent
data/iccm_dosing.json  iCCM dosing table
data/test_cases.json   sample cases used for the measurements
static/index.html      the whole front end
```

## Sources

- WHO/UNICEF, *Caring for the Sick Child in the Community* (iCCM), 2011.
- GRID3 Nigeria Health Facilities, Humanitarian Data Exchange: https://data.humdata.org/dataset/nigeria-health-care-facilities-in-nigeria

*Itoju supports trained health workers; it does not replace clinical judgement. When in doubt, refer.*
