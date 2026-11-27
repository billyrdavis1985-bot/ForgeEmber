# Plan

Build window: **Nov 23, 2026, 9:00 AM ET → Dec 2, 2026, 9:00 PM ET.** Target submission: **Dec 2, 3:00 PM ET.** Judging runs Dec 3–6, and winners are announced Dec 7.

## Timeline

| Date | Work | Exit criterion |
|---|---|---|
| Mon 11/23 | **Phase 0**: docs, scaffold, budget module (fully tested), README skeleton, smoke test (not run) | Tests and ruff green; Billy reviews; Billy runs the smoke test |
| Tue–Wed 11/24–25 | **Phase 1**: envelope, Ember agents, schemas, verifier, policy, baselines. One vertical slice end to end: invoice PDF → extraction → verify → HOLD → receipt. | The slice runs on one fixture at `dev` stage, with a ledger line per call |
| Thu 11/26 | Thanksgiving, a light day: fixture generator only, or off | `scripts/gen_fixtures.py` emits labeled fixtures |
| Fri–Sat 11/27–28 | Evaluation harness → **Gate A**; prompt caching; batch runner → **Gate B**; **Gate C** | Gates recorded in BUILD_LOG |
| Sun 11/29 | **Scale run.** Results report and README numbers. | Headline result published from the ledger |
| Mon 11/30 | Approval console, static replay demo site, **Gate D** | Keyless replay works |
| Tue 12/1 | Video recorded in segments; final README; trace export; usage screenshot; Muse Code trajectory export | All submission artifacts exist |
| Wed 12/2 | Buffer. **Submit by 3:00 PM ET.** | Submitted |

## Budget split (hard cap $9.00 of the event's $10.00 limit)

| Stage | Limit | Used for |
|---|---:|---|
| dev | $1.50 | Smoke test, prompt development, single-item slices |
| eval | $1.50 | Labeled fixture and corpus evaluation (Gates A and C) |
| scale | $4.00 | The scale run (Gate B sizes it) |
| demo | $0.75 | Recording the demo video |
| reserve | $1.25 | Contingency, and video input if Gate D passes |

A stage stops at its limit. Moving money between stages needs Billy's approval (`forgeember budget transfer ... --approved-by "Billy Davis"`) and a BUILD_LOG entry. **The cap is never raised.**

## Decision gates

| Gate | When | Measure | Rule |
|---|---|---|---|
| **A** | After the first evaluation | Routing accuracy and field-extraction accuracy on the fixtures | Both **≥ 90%**: go to the scale run. Otherwise improve prompts and examples for at most one day, then re-test. |
| **B** | Before the scale run | Measured cost per item × 10,000 | If **> $4.00**: lower reasoning effort, trim context, or shrink N. Never raise the cap. |
| **C** | After eval with and without the skeptic | Share of decisions the skeptic changes; extra attacks caught | If it changes **< 2%** of decisions **and** catches no extra attacks: run it on escalated cases only, not in bulk. |
| **D** | Nov 30 | Reserve stage balance | Build video input only if the reserve still holds **≥ $1.00**. |

## Submission checklist

- [ ] 3-minute demo video (only things built during the event)
- [ ] Public repo, MIT license, README submission overview up to date
- [ ] Project description
- [ ] Prompt/token trace: `scripts/export_trace.py` → `traces/submission-trace.jsonl` + `traces/summary.json`
- [ ] Meta Usage-page screenshot, Nov 23–Dec 2, filtered to Muse Ember
- [ ] `forgeember budget reconcile` run against that Usage-page total; ledger ≥ Meta
- [ ] Muse Code trajectory: `/export trajectory` (if Muse Code was used)
- [ ] README "Budget & Trace" block regenerated; CI green
- [ ] AI-assistance disclosure in the README
- [ ] Every dataset in `docs/DATA.md` has a verified license, and the demo uses only our own fixtures
