# Nwa Dị Mma — getting mothers to the right hospital, in time

***Nwa Dị Mma*** (pronounced *nwah dee m-MAH*; Igbo for "the child is well") helps a community health worker,
birth attendant or family member in Nigeria describe what is happening to a woman who is pregnant, in labour or
has just given birth — in English, Nigerian Pidgin or Igbo — and get a WHO danger-sign decision: **emergency,
hospital now, facility today, or plan ahead**, plus *where* to go (a hospital that can do a caesarean and give
blood, chosen from the GRID3 Nigeria facility register) and a handover note.

Nigeria accounts for about 1 in 4 maternal deaths worldwide. The AI never decides the urgency, destination or a
medicine by itself: four agents read, double-check, explain and route; the WHO rules and dose table are plain code,
and a final check blocks any medicine or dose the rules did not return.

## Run it (about 3 minutes)

Needs Python 3.10+.

```bash
git clone https://github.com/nnwokorie/nwadimma.git && cd nwadimma
python3 -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open **http://localhost:5050**, press **Start a case**, pick a sample (e.g. *Heavy bleeding after birth*), and press **Check now**.

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
| `test` (default) | `data/facilities_test.csv` (in repo, 1.5 MB) | Lagos, Kano, FCT, Anambra, Enugu, Imo, Abia, Ebonyi — 11,314 facilities | nothing to do |
| `full` | `data/facilities_full.csv` (6 MB, not in repo) | All 36 states + FCT — 45,808 facilities | see below |

```bash
python scripts/build_facilities.py      # downloads the GRID3 register from HDX and builds both CSVs
ITOJU_DATA=full python app.py           # Windows PowerShell: $env:ITOJU_DATA="full"; python app.py
```

### Reproduce the measurements

```bash
python run_cases.py            # runs the 17 cases in data/test_cases.json, prints the trace and a token/cost table
python run_cases.py injection  # run one case by id
```

Every run is appended to `logs/runs.jsonl` with per-agent input, cached, output and reasoning tokens and cost.

| Script | What it produces |
|---|---|
| `python scripts/evaluate.py default 2` | accuracy + mean tokens/cost per agent → `results/eval_default.json` (`ITOJU_REASONING=none` for the cheaper variant) |
| `python scripts/baseline_chatgpt.py` | same model asked directly vs. Nwa Dị Mma → `results/baseline_chatgpt.json` |
| `python scripts/safety_tests.py` | 7 attacks / failures, PASS/FAIL → `results/safety_tests.txt` |
| `python scripts/build_docs.py` | rebuilds the self-contained `report.html` and `slides.html` from `docs/` |

The report and slides are in the repo root: **`report.html`** and **`slides.html`** (open by double-click; arrow keys + `F` for fullscreen in the slides).

## How it works

```
note ─▶ Input guard (code) ─▶ 1 Intake agent (LLM) ─▶ 2 Safety reviewer (LLM + danger-word net)
     ─▶ 3 Protocol agent (LLM + WHO-rule tools) ─▶ Output check (code) ─▶ 4 Referral agent (LLM + facility tool, if referring)
```

| Agent | Job | Tools / guard |
|---|---|---|
| Input guard | Blocks prompt-injection text and over-long notes before any AI call | regex rules |
| Intake | Free text (English/Pidgin/Igbo) → 30 structured findings; unknowns stay empty | JSON schema validated by Pydantic, 1 retry |
| Safety reviewer | Finds danger signs the intake missed; may only *escalate* | English/Pidgin/Igbo keyword net + LLM second opinion |
| Protocol | Explains the decision for the helper and family (English, Pidgin, Igbo) | `classify_case()`, `get_drug(name)` — read verified findings, cannot be altered by the model |
| Output check | Every drug name and dose in the text must match the engine, else safe template | code |
| Referral | Emergencies → nearest general/teaching/federal hospital; normal labour → nearest birth facility; SBAR note | `find_facility()`; invented facility names are removed |

Limits: 4 tool rounds per agent, 40k tokens and $0.05 per run, $2/day budget (`ITOJU_DAILY_BUDGET`).

## Files

```
app.py                 Flask server (UI + /api/triage)
itoju/agents.py        the four agents and the orchestrator
itoju/maternal.py      WHO pregnancy/childbirth danger-sign rules as code
itoju/safety.py        input guard, danger-word net, output check
itoju/facilities.py    nearest-facility search (GRID3)
itoju/llm.py           Portkey client, token/cost ledger, budget caps
prompts/*.md           system prompts for each agent
data/maternal_protocol.json  WHO pre-referral medicine table
data/test_cases.json   sample cases used for the measurements
static/index.html      the whole front end
```

## Sources

- WHO, *Pregnancy, Childbirth, Postpartum and Newborn Care* (2015); *Managing Complications in Pregnancy and Childbirth* (2017); PPH recommendations (2012).
- GRID3 Nigeria Health Facilities, Humanitarian Data Exchange: https://data.humdata.org/dataset/nigeria-health-care-facilities-in-nigeria

*Nwa Dị Mma supports trained health workers and birth attendants; it does not replace clinical care. In an emergency, go to the nearest hospital.*
