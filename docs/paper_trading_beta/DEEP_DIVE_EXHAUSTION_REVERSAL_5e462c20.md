# Deep Dive: EXHAUSTION_REVERSAL_BULL_TEMPLATE_5e462c20

Generated: `2026-03-18 10:30:00 +00:00`

---

## 1. Summary

This is a **bullish mean-reversion hypothesis** that buys instruments after a sharp short-term selloff, expecting a bounce over 5 trading days. It was machine-generated via template mutation from the `EXHAUSTION_REVERSAL` family and has reached **PROMISING** belief status — the first cohort to do so in this beta system.

**Thesis**: When a stock drops >3% in a day, >5% over 5 days, and sits >8% below its 20-day mean, it is statistically likely to revert upward over the next 5 days.

**Current status**: PROMISING with confidence 0.67 after 13 test runs across expanding datasets. The backtest shows strong average returns (~28%) but with enormous volatility (~527%), a thin win rate (~51.7%), and median returns near zero (~0.3%). The edge is driven by extreme right-tail outcomes, not consistent wins.

### Key Concern

The last walk-forward window (Jul 2024 – Mar 2026) shows returns of **89–119%** while all prior windows show **3–10%**. This recent window completely dominates the aggregate statistics. Any analysis of this hypothesis should centre on whether that recent regime is sustainable or anomalous.

---

## 2. Hypothesis Definition

| Field | Value |
|---|---|
| **ID** | `eda47be7-6d9e-41f1-b40f-d2353ac1eed1` |
| **Code** | `EXHAUSTION_REVERSAL_BULL_TEMPLATE_5e462c20` |
| **Name** | Bullish short-term exhaustion reversal [5e462c] |
| **Family** | `EXHAUSTION_REVERSAL` (5fea31a0) |
| **Direction** | BULLISH |
| **Hold period** | 5 days |
| **Target metric** | `fwd_5d_excess_return_pct` |
| **Source** | GENERATED via TEMPLATE_MUTATION |
| **Template** | `EXHAUSTION_REVERSAL_BULL_TEMPLATE` |
| **Definition status** | PROMISING |
| **Created** | 2026-03-16 05:10:43 |
| **Updated** | 2026-03-17 22:00:29 |

### Entry Conditions

All three must be true simultaneously:

| Feature | Operator | Threshold |
|---|---|---|
| `ret_1d_pct` | < | -3.0% |
| `ret_5d_pct` | < | -5.0% |
| `distance_from_20d_mean_pct` | < | -8.0% |

**Exit**: Hold for 5 trading days (no stop loss, no profit target).

**Universe**: UK and US equities with core bias.

**Regime filters**: None.

**Feature subset**: `distance_from_20d_mean_pct`, `ret_1d_pct`, `ret_5d_pct`

### Family Context

| Field | Value |
|---|---|
| **Family name** | Exhaustion reversal |
| **Description** | Sharp short-term overextensions that may mean revert within one week |
| **Generator** | TEMPLATE_MUTATION |
| **Mutation policy** | Threshold variants + regime segmentation |
| **Budget** | Max 20 variants per discovery run |

---

## 3. Belief State

| Field | Value |
|---|---|
| **Status** | PROMISING |
| **Confidence score** | 0.67 |
| **Evidence count** | 2 |
| **In-sample strength** | 25.389 |
| **Out-of-sample strength** | 54.045 |
| **Degradation rate** | 0.0 |
| **Recency score** | 1.0 |
| **Stability score** | 0.310 |
| **Last tested** | 2026-03-18 07:59:38 |
| **Last validated date** | None (not yet VALIDATED) |
| **Supporting test run** | `6ef94c66` (latest, best walk-forward score) |
| **Contradicting test run** | `515c7bb8` (lower returns, mid-series) |

### Belief Notes

| Key | Value |
|---|---|
| distinct_evidence_points | 2 |
| latest_adjusted_return_pct | 27.768 |
| latest_baseline_edge_pct | 21.949 |
| latest_sample_size | 23,036 |
| latest_stability_score | 0.283 |
| latest_walk_forward_score | 17.805 |
| long_average_adjusted_return_pct | 25.389 |
| recent_average_adjusted_return_pct | 25.389 |
| average_stability_score | 0.310 |

---

## 4. Discovery Candidate Origin

This hypothesis was first generated as a discovery candidate and later promoted to a full definition.

| Field | Value |
|---|---|
| **Candidate ID** | `7a8012d4-5422-4732-90a6-6f4fe5f7b7d4` |
| **Discovery run** | `bc544288-18d9-4e4a-987b-bb08a9e642a5` |
| **Candidate hash** | `5e462c204d183835` |
| **Candidate created** | 2026-03-15 02:14:39 |
| **Stage reached** | 5 |
| **Candidate status** | PRUNED (reason: `redundant_variant`, winner: `566ddf1c`) |
| **Redundancy group** | `EXHAUSTION_REVERSAL:BULLISH:distance_from_20d_mean_pct:lt\|ret_1d_pct:lt\|ret_5d_pct:lt:` |

### Discovery-stage metrics (smaller, earlier dataset)

| Metric | Value |
|---|---|
| Support count | 7,429 |
| Matched instruments | 140 |
| Hit rate | 48.77% |
| Average target return | 9.69% |
| Median excess return | -0.273% |
| Outcome volatility | 281.76% |
| Friction-adjusted return | 9.19% |
| Walk-forward score | 5.306 |
| Baseline edge | 4.378% |
| Stability score | 0.257 |
| Walk windows | 7 |
| Recency edge | 19.234% |

Note: The discovery-stage results (9.7% avg return) are much more conservative than the later test runs (22–28%) because the discovery used a smaller, earlier dataset. The gap warrants investigation.

---

## 5. All 13 Test Runs (Raw Data)

Each test run re-evaluates the hypothesis against a progressively expanding dataset as new instruments are added to the universe.

### Test Run Summary Table

| # | Run ID (short) | Date Range | Sample | Instruments | Avg Ret% | Median% | Win% | Walk-Fwd | OOS Score | Baseline Ret% | Edge% | Stability | Volatility% | Created |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `172f98aa` | 2016-04-15 → 2026-03-06 | 16,874 | 241 | 24.57 | 0.28 | 51.41 | 11.30 | 45.05 | 5.93 | 18.14 | 0.336 | 496.0 | 2026-03-16 05:20 |
| 2 | `fa3b8312` | 2016-04-15 → 2026-03-06 | 16,983 | 244 | 24.42 | 0.28 | 51.44 | 10.72 | 42.87 | 5.83 | 18.09 | 0.336 | 494.4 | 2026-03-16 10:01 |
| 3 | `cac729d8` | 2016-04-15 → 2026-03-06 | 17,206 | 247 | 24.12 | 0.28 | 51.42 | 10.94 | 43.38 | 5.73 | 17.89 | 0.336 | 491.2 | 2026-03-17 02:58 |
| 4 | `515c7bb8` | 2016-04-15 → 2026-03-06 | 17,639 | 253 | 23.51 | 0.25 | 51.25 | 11.33 | 44.64 | 5.54 | 17.47 | 0.336 | 485.2 | 2026-03-17 20:57 |
| 5 | `8badbf80` | 2016-04-15 → 2026-03-09 | 18,485 | 265 | 22.50 | 0.22 | 51.12 | 13.10 | 45.88 | 5.21 | 16.79 | 0.375 | 474.0 | 2026-03-17 22:00 |
| 6 | `67d43ad1` | 2016-04-15 → 2026-03-09 | 18,871 | 277 | 24.69 | 0.23 | 51.18 | 11.71 | 46.14 | 5.28 | 18.91 | 0.336 | 496.3 | 2026-03-17 23:13 |
| 7 | `20f2ebbc` | 2016-04-15 → 2026-03-09 | 19,568 | 289 | 23.87 | 0.25 | 51.31 | 11.29 | 44.41 | 5.08 | 18.29 | 0.336 | 487.4 | 2026-03-18 00:31 |
| 8 | `e6607579` | 2016-04-15 → 2026-03-09 | 20,190 | 298 | 23.22 | 0.28 | 51.54 | 11.33 | 44.12 | 4.89 | 17.83 | 0.336 | 479.9 | 2026-03-18 01:38 |
| 9 | `f1519150` | 2016-04-15 → 2026-03-09 | 20,704 | 307 | 22.67 | 0.31 | 51.64 | 11.05 | 44.30 | 4.79 | 17.38 | 0.336 | 473.9 | 2026-03-18 02:51 |
| 10 | `5d481c87` | 2016-04-15 → 2026-03-09 | 21,451 | 316 | 21.90 | 0.28 | 51.53 | 10.67 | 43.35 | 4.78 | 16.62 | 0.336 | 465.6 | 2026-03-18 04:10 |
| 11 | `27b6a180` | 2016-04-15 → 2026-03-09 | 22,094 | 325 | 28.54 | 0.32 | 51.74 | 14.26 | 59.25 | 6.05 | 21.98 | 0.257 | 530.2 | 2026-03-18 05:33 |
| 12 | `054b4a66` | 2016-04-15 → 2026-03-09 | 22,613 | 334 | 27.90 | 0.30 | 51.66 | 13.97 | 57.78 | 5.85 | 21.55 | 0.257 | 524.1 | 2026-03-18 06:59 |
| 13 | `6ef94c66` | 2016-04-15 → 2026-03-09 | 23,036 | 340 | 28.27 | 0.30 | 51.68 | 17.80 | 63.45 | 5.82 | 21.95 | 0.283 | 527.4 | 2026-03-18 07:59 |

### Observations Across Runs

- **Sample grows steadily**: 16,874 → 23,036 as the instrument universe expands (241 → 340 instruments)
- **Average return is volatile**: ranges 21.9% to 28.5% — not converging
- **Median return is near zero**: 0.22% to 0.32% across all runs — the distribution is heavily right-skewed
- **Win rate barely above coin-flip**: 51.1% to 51.7%
- **Outcome volatility is extreme**: 465–530% — individual trade outcomes are wildly dispersed
- **Walk-forward score improved** in latest run (17.8 vs ~11 average), but this is driven by the most recent window
- **Stability score decreased** in runs 11–13 (0.257–0.283 vs 0.336 earlier) — the walk-forward windows are becoming less consistent, not more

---

## 6. Walk-Forward Window Breakdown (Latest Run: `6ef94c66`)

The latest test run has **6 walk-forward windows** (the runs with 7 windows have very small final windows with only 3–51 observations):

| Window | Period | Sample | Adj Return% | Edge vs Baseline% |
|---|---|---|---|---|
| 1 | 2016-04-15 → 2017-12-18 | 1,731 | 3.34 | -2.48 |
| 2 | 2017-12-19 → 2019-08-19 | 2,449 | 3.71 | -2.11 |
| 3 | 2019-08-20 → 2021-04-14 | 3,800 | 2.64 | -3.18 |
| 4 | 2021-04-15 → 2022-11-25 | 5,998 | 5.16 | -0.66 |
| 5 | 2022-11-28 → 2024-07-18 | 4,447 | 7.91 | +2.09 |
| 6 | 2024-07-19 → 2026-03-09 | 4,611 | **118.99** | **+113.17** |

### Critical Finding

**Windows 1–4 show negative edge** — the strategy underperforms the baseline in those periods. Window 5 is marginally positive (+2.1%). **Window 6 accounts for essentially all of the strategy's apparent edge**, with an adjusted return of 119% and edge of +113%.

This is a **massive red flag** for independent verification. The ~28% aggregate return is almost entirely attributable to one 20-month window. Without window 6, the strategy would likely show flat-to-negative edge.

---

## 7. Walk-Forward Windows Across All 13 Runs

Here is how each window's adjusted return evolved as the universe grew:

### Window 6 (Jul 2024 → Mar 2026) across runs:

| Run | Sample | Adj Return% | Edge% |
|---|---|---|---|
| 1 (16,874 total) | 3,601 | 88.76 | +82.83 |
| 2 (16,983 total) | 3,633 | 88.09 | +82.26 |
| 3 (17,206 total) | 3,687 | 86.73 | +80.99 |
| 4 (17,639 total) | 3,714 | 86.02 | +80.48 |
| 5 (18,485 total) | 3,871 | 82.62 | +77.41 |
| 6 (18,871 total) | 3,941 | 93.81 | +88.54 |
| 7 (19,568 total) | 4,041 | 91.51 | +86.42 |
| 8 (20,190 total) | 4,133 | 89.63 | +84.74 |
| 9 (20,704 total) | 4,226 | 90.00 | +85.21 |
| 10 (21,451 total) | 4,334 | 87.76 | +82.98 |
| 11 (22,094 total) | 4,452 | 118.79 | +112.74 |
| 12 (22,613 total) | 4,564 | 115.85 | +110.00 |
| 13 (23,036 total) | 4,611 | 118.99 | +113.17 |

Window 6 jumped from ~87–93% to ~115–119% between runs 10 and 11 (when the universe grew from 316 to 325 instruments). This suggests a small number of newly added instruments with extreme returns are disproportionately inflating the result.

### Window 1–5 consistency (latest run):

| Window | Adj Return% | Interpretation |
|---|---|---|
| 1 (2016–2018) | 3.34 | Below baseline |
| 2 (2018–2019) | 3.71 | Below baseline |
| 3 (2019–2021) | 2.64 | Below baseline (COVID era) |
| 4 (2021–2023) | 5.16 | Near baseline |
| 5 (2023–2024) | 7.91 | Slightly above baseline |

Without the recent window, this hypothesis would show **stable but uninspiring** mean-reversion behaviour, roughly matching or slightly lagging the unconditional baseline.

---

## 8. Signal Observations (Live)

| Status | Count | First Seen | Last Seen |
|---|---|---|---|
| MATCHED | 426 | 2026-03-16 05:26 | 2026-03-18 07:30 |

**No realized returns yet** — all 426 signal observations have `realized_return_pct = None`. The 5-day hold period has not elapsed for any live signal.

No recommendation decisions have been generated for this hypothesis (likely because it has PROMISING but not VALIDATED belief status, and recommendation flow requires VALIDATED).

### Sample of Recent Live Signals

| Date | Symbol | Exp Return% | ret_1d% | ret_5d% | dist_20d% |
|---|---|---|---|---|---|
| 2026-03-17 | MARS | 6.30 | -3.12 | -6.38 | -8.31 |
| 2026-03-13 | BOY | 7.71 | -6.17 | -7.94 | -11.83 |
| 2026-03-13 | BVXP | 7.83 | -5.00 | -8.06 | -9.09 |
| 2026-03-13 | WTE | 23.98 | -4.00 | -24.21 | -23.85 |
| 2026-03-13 | GGP | 9.00 | -3.55 | -9.23 | -10.66 |
| 2026-03-13 | BRBY | 5.97 | -3.42 | -6.20 | -9.14 |
| 2026-03-13 | 0A7K | 84.65 | -83.19 | -84.88 | -81.63 |
| 2026-03-13 | 0A3U | 45.75 | -38.04 | -45.98 | -60.66 |

Note the extreme outliers: 0A7K (ret_1d = -83%) and 0A3U (ret_1d = -38%) are likely delisting/crash events. These contribute massively to expected returns because the model has learned from similar historical outliers bouncing. This is the mechanism by which the average return is ~28% while the median is ~0.3%.

---

## 9. Verification Checklist

For independent verification, the following areas should be examined:

### Statistical Concerns

1. **Survivorship bias**: Are the instruments in the universe subject to survivorship filtering? Stocks that crash 80%+ and then delist would inflate backtest returns if the backtest assumes they bounce.

2. **Window 6 dominance**: The Jul 2024 – Mar 2026 window drives nearly all of the edge. What happened in that period? Was there a broad market correction followed by a V-shaped recovery? This would be a regime-specific effect, not a durable alpha source.

3. **Mean vs median divergence**: Average return ~28% vs median ~0.3% means ~50% of trades lose money. The positive average is driven by extreme right-tail events. This is a classic pattern for strategies that are long volatility / long crash recovery.

4. **Max drawdown -100%**: Individual instruments can go to zero. Position sizing and portfolio construction would need to account for this.

5. **Transaction costs**: 50bps is assumed. For small-cap UK stocks in a selloff (wide spreads, thin books), real friction could be much higher.

6. **Sample expansion effect**: As the universe grows from 241 to 340 instruments, returns jump from ~24% to ~28% in the last 3 runs. Are the newly added instruments systematically different?

### Data Integrity

7. **Label dates**: The latest label date in the system is 2026-03-10, so the most recent test end date of 2026-03-09 is genuine out-of-sample relative to the hold period.

8. **Baseline**: `UNCONDITIONAL_UNIVERSE_MEAN` — this is the average 5-day return across all instruments regardless of conditions. Baseline return is ~5–6%, so the excess is the strategy return minus this.

9. **Walk-forward methodology**: Windows appear to be non-overlapping chronological splits. The walk_forward_score is a composite across windows. Verify: are models retrained per window or is this a static rule?

10. **No realized returns on live signals**: Cannot validate out-of-sample performance until signals mature past their 5-day hold period. First live signals were generated 2026-03-16; earliest realization expected around 2026-03-21.

---

## 10. Raw Data: All Walk-Forward Windows (All Runs)

### Run 1 (`172f98aa`, 2026-03-16, n=16,874)

| Window | Start | End | n | Avg Ret% | Adj Ret% | Edge% |
|---|---|---|---|---|---|---|
| 1 | 2016-04-15 | 2018-01-15 | 1,071 | 5.38 | 4.88 | -1.05 |
| 2 | 2018-01-16 | 2019-09-27 | 1,419 | 6.33 | 5.83 | -0.10 |
| 3 | 2019-09-30 | 2021-05-26 | 2,575 | 4.26 | 3.76 | -2.17 |
| 4 | 2021-05-27 | 2022-12-21 | 4,651 | 6.70 | 6.20 | +0.28 |
| 5 | 2022-12-22 | 2024-07-30 | 3,506 | 10.33 | 9.83 | +3.90 |
| 6 | 2024-07-31 | 2026-03-02 | 3,601 | 89.26 | 88.76 | +82.83 |
| 7 | 2026-03-03 | 2026-03-06 | 51 | 1.84 | 1.34 | -4.59 |

### Run 5 (`8badbf80`, 2026-03-17, n=18,485)

| Window | Start | End | n | Avg Ret% | Adj Ret% | Edge% |
|---|---|---|---|---|---|---|
| 1 | 2016-04-15 | 2018-01-04 | 1,245 | 4.92 | 4.42 | -0.79 |
| 2 | 2018-01-08 | 2019-09-12 | 1,707 | 5.14 | 4.64 | -0.56 |
| 3 | 2019-09-13 | 2021-05-11 | 2,898 | 3.46 | 2.96 | -2.24 |
| 4 | 2021-05-12 | 2022-12-14 | 4,956 | 6.55 | 6.05 | +0.84 |
| 5 | 2022-12-15 | 2024-07-30 | 3,808 | 9.64 | 9.14 | +3.94 |
| 6 | 2024-07-31 | 2026-03-09 | 3,871 | 83.12 | 82.62 | +77.41 |

### Run 13 (`6ef94c66`, 2026-03-18, n=23,036) — Latest

| Window | Start | End | n | Avg Ret% | Adj Ret% | Edge% |
|---|---|---|---|---|---|---|
| 1 | 2016-04-15 | 2017-12-18 | 1,731 | 3.84 | 3.34 | -2.48 |
| 2 | 2017-12-19 | 2019-08-19 | 2,449 | 4.21 | 3.71 | -2.11 |
| 3 | 2019-08-20 | 2021-04-14 | 3,800 | 3.14 | 2.64 | -3.18 |
| 4 | 2021-04-15 | 2022-11-25 | 5,998 | 5.66 | 5.16 | -0.66 |
| 5 | 2022-11-28 | 2024-07-18 | 4,447 | 8.41 | 7.91 | +2.09 |
| 6 | 2024-07-19 | 2026-03-09 | 4,611 | 119.49 | 118.99 | +113.17 |

---

## 11. Raw Data: Full Signal Observation List

426 total MATCHED signals, all BULLISH, all with `prediction_source = VALIDATED_BASELINE`.

None have realized returns yet. Below is a representative sample (30 most recent, deduplicated):

| Decision Date | Symbol | Expected Return% | ret_1d% | ret_5d% | dist_20d% | drawdown_20d% | vol_20d% |
|---|---|---|---|---|---|---|---|
| 2026-03-17 | MARS | 6.30 | -3.12 | -6.38 | -8.31 | -13.87 | 2.26 |
| 2026-03-13 | BOY | 7.71 | -6.17 | -7.94 | -11.83 | -15.85 | 3.42 |
| 2026-03-13 | BVXP | 7.83 | -5.00 | -8.06 | -9.09 | -16.18 | 2.32 |
| 2026-03-13 | WTE | 23.98 | -4.00 | -24.21 | -23.85 | -31.43 | 7.10 |
| 2026-03-13 | GGP | 9.00 | -3.55 | -9.23 | -10.66 | -18.33 | 3.45 |
| 2026-03-13 | BRBY | 5.97 | -3.42 | -6.20 | -9.14 | -15.61 | 2.03 |
| 2026-03-13 | 0A8P | 6.70 | -3.44 | -6.93 | -14.27 | -28.81 | 5.49 |
| 2026-03-13 | 0A7K | 84.65 | -83.19 | -84.88 | -81.63 | -90.36 | 41.29 |
| 2026-03-13 | 0A6A | 12.16 | -4.52 | -12.39 | -10.73 | -18.33 | 4.10 |
| 2026-03-13 | AAMMF | 13.62 | -3.95 | -14.22 | -13.90 | -25.89 | 7.89 |
| 2026-03-13 | 0A61 | 9.89 | -5.00 | -10.12 | -8.66 | -16.31 | 3.78 |
| 2026-03-13 | 0A56 | 10.54 | -6.61 | -10.77 | -16.25 | -26.54 | 4.51 |
| 2026-03-13 | 0A3U | 45.75 | -38.04 | -45.98 | -60.66 | -73.55 | 13.71 |
| 2026-03-13 | AAGFF | 4.93 | -9.04 | -5.53 | -12.32 | -27.01 | 7.28 |
| 2026-03-13 | AAGAF | 7.65 | -7.92 | -8.25 | -11.92 | -22.55 | 4.87 |

---

## 12. Metadata

### Discovery provenance

```json
{
  "family_code": "EXHAUSTION_REVERSAL",
  "parent_hypothesis_code": null,
  "seed_template": "EXHAUSTION_REVERSAL_BULL_TEMPLATE",
  "source_type": "GENERATED"
}
```

### Definition metadata

```json
{
  "baseline_edge_pct": 44.0734,
  "candidate_hash": "5e462c204d183835",
  "discovery_run_id": "47ae8e56-77f6-4684-9c88-18b626a40d06",
  "redundancy_group": "EXHAUSTION_REVERSAL:BULLISH:distance_from_20d_mean_pct:lt|ret_1d_pct:lt|ret_5d_pct:lt:",
  "stability_score": 0.375,
  "stage_reached": 5,
  "support_count": 2254
}
```

### Transaction cost model

All test runs assume **50 basis points** round-trip transaction cost.

### Baseline policy

`UNCONDITIONAL_UNIVERSE_MEAN` — average 5-day forward return across the full eligible universe regardless of any conditions. Baseline returns range from 4.78% to 6.05% across test runs as the universe composition changes.
