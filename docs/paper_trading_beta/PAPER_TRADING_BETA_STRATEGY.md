# Paper Trading Beta Strategy

Last updated: `2026-03-14`

Status: exploratory strategy for a segregated beta feature. Not live. No broker execution. No real-money order routing.

Companion docs:

- `docs/paper_trading_beta/README.md`
- `docs/paper_trading_beta/PAPER_TRADING_BETA_RUNTIME_ARCHITECTURE.md`
- `docs/paper_trading_beta/PAPER_TRADING_BETA_TECHNICAL_IMPLEMENTATION_PLAN.md`
- `docs/paper_trading_beta/PAPER_TRADING_BETA_DATABASE_SCHEMA.md`

## 1. Intent

This document defines a beta paper-trading system that intentionally goes beyond the current deterministic, non-predictive product boundary.

The proposed beta would:

- track a curated active live-paper universe of roughly `50` stocks while learning from a broader historical research corpus;
- optionally track a small, separately governed set of major FX pairs;
- update market prices once per minute during live market hours;
- generate paper-trade entry candidates from explicit research signals;
- set paper position parameters such as target, stop, horizon, and size;
- simulate fills against a cash ledger that starts at `GBP 10,000`;
- track open and closed paper positions;
- evaluate whether each recommendation added value, failed, or was indeterminate;
- learn systematically which signals, combinations, and conditions appear to produce repeatable edge after realistic friction;
- produce two connected outputs: a learning playground for finding promising markers, and an immutable demo-trade lane for testing those hypotheses prospectively;
- preserve full auditability so every decision can be reconstructed from evidence.

This is a research and evaluation system, not a live execution system. It must remain isolated from any live-trading workflow.

## 2. Current Documentation Conflict

The current source-of-truth docs explicitly state:

- deterministic modelling only;
- no market prediction features;
- no buy/sell advice language;
- no recommendation engine.

This beta strategy therefore must be treated as an explicit exception, not a silent extension of the existing product scope.

If implementation proceeds, the documentation should be amended in a controlled way:

1. keep the current core product definition intact;
2. introduce a new, clearly bounded "experimental predictive paper-trading beta" domain;
3. make it clear that the beta is paper-only and separate from the deterministic core;
4. prevent any user from confusing paper recommendations with live instructions.

## 3. Design Principles

The beta should be built around the following principles.

### 3.1 Paper-only isolation

- No broker API order submission.
- No auto-trading.
- No "execute now" path from a recommendation.
- No shared UI language that makes the beta feel like a live dealing screen.

### 3.2 Evidence before opinion

- Every paper recommendation must be supported by timestamped data.
- Every derived score must trace back to raw inputs.
- Every manual action must carry a reason.

### 3.3 Reproducibility

- The system must be able to recreate why a recommendation was emitted at a specific minute.
- Model version, feature values, news items, price bars, and portfolio constraints must all be frozen in the event record.

### 3.4 No hidden hindsight

- No use of revised or future data during live scoring.
- No use of same-bar close as both signal input and fill price unless explicitly justified.
- No universe changes that quietly improve historical results.

### 3.5 Measured evaluation

- "Correct" should not be a single naive yes/no label.
- Recommendations should be evaluated against multiple outcome definitions: target hit, stop hit, net return, benchmark-relative return, calibration quality, and drawdown path.

### 3.6 Conservative realism

- Include friction assumptions: spread, slippage, venue fees, FX drag if applicable, and order-size constraints.
- Prefer liquid names and simple assumptions before expanding complexity.

### 3.7 Research-first learning

- The beta is a signal-learning system first and a trade simulator second.
- Raw data should be collected broadly enough to support discovery, but strategy versions must define what they actually use.
- The objective is to discover where small, repeatable edges may exist, why they exist, and when they break down.

### 3.8 Learn right before learning fast

- The primary objective is not rapid iteration for its own sake; it is reliable learning.
- A slower research loop is acceptable if it produces cleaner evidence, fewer false conclusions, and a more durable process.
- The system should prefer correctness of data, labels, cost assumptions, and evaluation design over speed of model turnover.
- It is better to spend a long period validating a real edge than to move quickly, overfit, and abandon the project.

### 3.9 Separate prediction from execution

- The prediction layer should estimate expected outcome or relative attractiveness for every eligible symbol.
- The trade-rule layer should decide whether and how to express that prediction through entries, exits, targets, and stops.
- The allocation layer should decide which valid opportunities receive limited paper capital.
- These layers should be measured separately so the system can learn whether failure came from the forecast, the execution rule, or the capital-assignment logic.

### 3.10 Dual-output architecture

- The system should produce a `learning playground` where historical and accumulated research data are used to identify candidate markers, patterns, and hypotheses.
- The system should also produce an `immutable demo-trade lane` where promoted hypotheses are tested only against future market behavior.
- A hypothesis should move from playground to demo trading only through an explicit, versioned promotion step.
- Demo-trade records must be append-only and resistant to retrospective editing so false positives cannot be manufactured after the outcome is known.

### 3.11 Confidence on every decision

- Every scored symbol, recommendation, rejection, hypothesis promotion, and demo-trade action should carry a confidence marker.
- Confidence should be stored as both a raw numeric value and a normalized band or label so the system can evaluate calibration over time.
- Confidence should be explainable: the system should be able to show what inputs and model state drove a low, medium, or high-confidence decision.

### 3.12 Broad observation, narrow promotion

- The observation layer should remain intentionally broad so the system can learn from the full opportunity set.
- The governed demo-trade lane should remain intentionally narrow so prospective testing is interpretable and operationally stable.
- Broad observation and narrow promotion are separate concerns: the system may observe many signals and contexts while promoting only a small, tightly controlled subset into forward testing.
- Data sources should not be removed merely because they are not yet part of a promoted strategy; they should be removed only if they damage research integrity, auditability, or operational stability.

### 3.13 Separate observation, prediction, execution, and evaluation

- `observation` records what happened in the market and news flow;
- `prediction` records what the model expected from that observed state;
- `execution` records how a paper trade would have expressed that prediction;
- `evaluation` records whether the idea actually had edge after costs and constraints.

This separation is necessary so the system can learn correctly instead of blending market facts, model expectations, trade mechanics, and outcomes into one opaque record.

### 3.14 Separate research storage from portfolio storage

- The beta should default to its own `beta_research.db` rather than sharing the main portfolio database file.
- The existing portfolio database should remain the source of truth for live holdings, transactions, and deterministic product state.
- The beta database should own its own bars, news, features, labels, score tape, experiments, governance records, and demo-trade history.
- Model files, replay bundles, and exported research artifacts should live outside the SQLite database in a sibling artifact path such as `beta_artifacts/`.
- If the beta needs core catalogue context, it should mirror the required instrument/reference data into the beta database and store any `core_security_id` only as a soft reference.

This separation reduces write contention, keeps backup and restore of portfolio data independent from research data, and lowers the blast radius of beta-specific schema churn or data corruption.

### 3.15 Persist potential signals for later audit

- The system should persist `potential signals` as first-class research artifacts, not only promoted strategies or executed demo trades.
- A potential signal should record when it was found, how it was found, which instrument or context it relates to, what evidence supported it, and what expected gain or directional implication was inferred at the time.
- Potential signals should remain reviewable even if they were never promoted, never traded, or later proved weak.
- The system should also persist enough supporting references that a later AI or human review can independently verify whether the signal was real, duplicated, noisy, or misinterpreted.
- AI review should consume stored evidence packs and candidate-signal records; it should not rely on undocumented memory or free-form past summaries.

This is necessary so later questions such as "review our potential gains" can be answered from stored evidence rather than reconstructed hindsight.

## 4. Strategic Goals

The beta should answer these questions:

1. Which signals consistently precede profitable price movement, and under what conditions?
2. Can a research-driven prediction engine produce paper recommendations that outperform simple baselines after realistic friction?
3. Which stocks or FX pairs show repeatable behavior worth continued observation?
4. Which signals actually add predictive value, and which degrade performance?
5. Which signals only work in specific regimes, sectors, volatility states, or time windows?
6. Are the system's confidence scores calibrated, or is the model overconfident?
7. Does the strategy preserve capital under stress, or does it only work in a narrow regime?
8. Can every recommendation be explained and audited after the fact?
9. Can the research process remain credible and useful over long evaluation windows rather than only in short bursts of promising performance?

## 5. Non-Goals

The first beta should not try to do all of the following:

- live order routing;
- user-facing investment advice for real money;
- options, leverage, or short selling;
- opaque black-box predictions with no evidence trail;
- unlimited universe coverage;
- social-copy features or public leaderboards;
- fully autonomous self-tuning in production.

The first beta should be narrower: long-only, paper-only, evidence-rich, and operationally boring.

## 6. Recommended Operating Boundary

The cleanest starting point is:

- one primary live-paper market focus to start, with the UK prioritized first because it reduces practical FX drag in the initial demo lane;
- one base currency ledger (`GBP`);
- an app-selected seed live-paper universe of roughly `50` highly liquid common stocks, ideally UK-heavy while still learning from both UK and US names;
- long-only paper execution, while still learning from bullish, bearish, and market-risk signals;
- minute-bar refresh during regular market hours, with strategies free to operate on minute, hourly, or daily decision horizons;
- no pre-market or after-hours execution in phase 1;
- overnight holds allowed by default when the strategy thesis remains valid;
- no overnight leverage;
- no hard concurrent-position cap, with soft diversification and drawdown controls used instead.

Reasoning:

- UK-first live execution focus reduces timezone, fee, FX, and market-structure confusion while still allowing broader learning from US data;
- high-liquidity names reduce fill-model distortion;
- long-only avoids borrow and locate complexity;
- FX should not be mixed casually into the same model as equities because the drivers, session behavior, and cost structure are different;
- minute data should be treated as a capability for entry timing, volatility context, and diagnosis, not as a requirement that every strategy be intraday;
- regular-hours only reduces data quality and spread edge cases;
- a seed universe keeps the first live lane auditable while still leaving room for automated expansion once the system proves its coverage and health.

### 6.1 Broad research corpus versus narrow live universe

The beta should distinguish between:

- a `historical research corpus` used for offline learning, dataset construction, and hypothesis discovery;
- an `active live-paper universe` used for shadow scoring, recommendation gating, and immutable demo-trade testing.

Recommended `v1` learning-corpus target:

- US and UK common equities only;
- liquid, seasoned names with cleaner corporate-action history and stable pricing/news coverage;
- roughly `1,000` to `2,000` combined names after liquidity and data-quality filtering, rather than the entire long tail of listed names;
- ideally `10` years of daily history for corpus members, market benchmarks, sector references, and required FX conversion series where data quality permits.

Recommended `v1` live-paper target:

- UK-first live-paper focus, with US names allowed in shadow scoring and demo execution whenever their data quality, FX context, and cost coverage are sufficient;
- an initial app-selected live-paper seed of roughly `50` actively scored names, ideally UK-heavy rather than UK-only;
- automatic expansion beyond the seed when the system judges that data coverage, research quality, and operational health are strong enough;
- prospective minute/news capture focused on the active live universe and its benchmark/reference set, not on the entire research corpus.

Rationale:

- a large multi-market daily corpus produces a much stronger learning dataset than a narrow single-market archive;
- keeping the live paper lane narrow preserves auditability and operational stability while the system is still proving itself;
- broad historical observation and narrow prospective promotion should remain separate design choices.

## 7. Universe Selection Strategy

The system must decide not only how to trade, but what it is allowed to trade.

### 7.1 Universe objectives

The initial active `50` live-paper names should be:

- liquid enough for minute-level simulation to be meaningful;
- large enough in volume to reduce spread noise;
- diverse enough by sector to avoid accidental single-theme overfit;
- stable enough in symbol and listing history to simplify audit and backtesting;
- covered by reliable news and pricing data sources.

### 7.2 Inclusion criteria

Recommended inclusion criteria:

- common equities only;
- minimum average daily traded value threshold;
- minimum price floor to avoid penny-stock behavior;
- minimum months of continuous price history;
- regular news coverage from sources with publish timestamps;
- no known extreme corporate-action instability at launch.

### 7.3 Exclusion criteria

Recommended exclusions for phase 1:

- penny stocks;
- leveraged ETFs and inverse products;
- ADR-heavy edge cases if primary-market data is incomplete;
- names with persistently wide spreads;
- illiquid small caps;
- instruments with unreliable minute-bar coverage;
- names under frequent halts or corporate restructuring if data is noisy.

### 7.4 Universe construction method

The universe should not be hand-tuned daily. It should be governed.

Recommended method:

1. define a documented screening rule;
2. let the application generate the initial candidate list from those rules rather than relying on a hand-curated starter set;
3. review for obvious data-quality failures;
4. allow the application to expand, demote, or remove names automatically as it learns, but only through versioned rule changes and fully logged membership events;
5. preserve a reproducible history of which rule version added, demoted, or removed each symbol and why.

### 7.5 Universe audit fields

Each universe membership record should capture:

- symbol;
- primary exchange;
- sector and industry tags;
- inclusion date;
- exclusion date if removed;
- inclusion rule version;
- data quality status;
- rationale note;
- reviewer identity if manually approved.

### 7.6 FX test universe

If FX is included, it should be treated as a separate sub-universe, not as an incidental extension of the stock list.

Recommended FX rules:

- treat FX as a mandatory conversion and attribution layer from day one for non-GBP instruments;
- store local return, FX contribution, and combined GBP return for any foreign-currency instrument;
- allow FX series to exist as contextual research inputs even before FX becomes a tradable demo-trade universe;
- start with major, liquid pairs only;
- keep the FX list much smaller than the equity list in the first beta;
- separate FX strategies from equity strategies;
- separate FX performance analytics from equity analytics;
- do not introduce leverage, financing, or carry assumptions unless explicitly modeled and reviewed;
- do not allow paper FX trades if the applicable spread or commission schedule is missing;
- only promote FX into the demo-trade lane once its own benchmark framework, cost model, and evaluation logic are versioned explicitly.

## 8. Market Research Strategy

The system should not treat "research" as a vague human intuition bucket. Research must be structured into signal families with evidence.

### 8.1 Research inputs

Phase 1 research inputs should be limited to sources that can be stored and audited:

- minute and daily price/volume bars;
- market index context;
- sector-relative performance;
- macro calendar and central-bank event context if FX is enabled;
- corporate event calendar;
- official company, exchange, and regulatory releases with reliable timestamps;
- narrow, point-in-time fundamental snapshots for research-approved fields where as-reported timing can be retained defensibly;
- timestamped news headlines and article metadata;
- analyst/event transcripts only if timestamp fidelity is reliable;
- optional manual annotations, but only as explicit tagged notes.

Broad fundamental warehousing should not be treated as a `v1` dependency. The first implementation should prefer timely market, event, and release information over a large balance-sheet warehouse unless later research proves specific point-in-time fields materially improve out-of-sample results.

### 8.2 Research categories

Each input should map into one of these categories:

- price action;
- volume and liquidity;
- volatility and range expansion;
- market regime;
- sector rotation;
- event proximity;
- news sentiment or event classification;
- manual hypothesis tag.

### 8.3 Research rules

- Raw data should be stored before being transformed.
- Every derived signal should have a formal definition.
- Every signal should declare its lookback window and refresh cadence.
- Every signal should be tagged as `research-only`, `backtest-approved`, or `live-paper-approved`.
- Broad structured observation is beneficial for research; features should be removed only when they are clearly noisy, redundant, unusable, or operationally harmful.

### 8.3.1 High-value structured context before long-tail data

The beta should prioritize research inputs that are both information-rich and operationally tractable.

Priority order:

- price, volume, benchmark, sector, and FX context;
- corporate actions and scheduled events;
- official releases, filings, and regulated announcements;
- news metadata and text-derived event classifications;
- narrow, point-in-time fundamental fields that can be stored with clear as-reported timing;
- alternative or social data only later, only if rights, stability, and auditability are strong.

The goal is not to collect every possible dataset. The goal is to avoid missing the classes of information most likely to matter for forecast quality while refusing low-quality feeds that add noise, cost, or legal ambiguity.

### 8.3.2 Observation versus promotion boundary

The system should continue observing broadly across:

- price bars across multiple timeframes;
- market and sector benchmarks;
- corporate events;
- news and text-derived signals;
- FX series;
- calendar context;
- regime indicators.

Only a small subset of that observed information should be promoted into the governed demo-trade lane at first.

The learning playground exists so the system can discover:

- patterns that appear promising;
- patterns that fail;
- signals that work only in specific regimes;
- signals that interact in non-obvious ways.

### 8.4 News research approach

If news is used, the system must store more than a final score.

For each scored article, store:

- source;
- headline;
- publication timestamp;
- ingestion timestamp;
- symbol linkage method;
- article category;
- raw sentiment or event tags;
- scoring model version;
- extracted rationale;
- confidence;
- whether the article was the first, follow-up, or duplicate report.

This is necessary to answer questions such as:

- does a specific news score bucket underperform?
- do follow-up articles work worse than first reports?
- does overnight news behave differently from intraday news?
- do certain publishers or categories carry more predictive value?

### 8.5 Manual research use

Manual notes can exist, but they should never bypass the evidence model.

Any manual override should require:

- a reason code;
- a free-text note;
- the user identity;
- a timestamp;
- the before/after change;
- a classification such as `suppress`, `allow`, `priority-increase`, or `size-reduce`.

### 8.6 Data acquisition priorities

For `v1`, the system should not begin by chasing perfect minute-level history for every possible feature. The acquisition order should favor correctness and broad research utility.

Recommended order:

1. define the security master, benchmark set, and sector reference set;
2. define the broader historical research corpus separately from the narrower active live-paper universe;
3. backfill daily bars for the full approved research corpus and all benchmark/reference instruments;
4. backfill corporate actions and event history aligned to that daily history;
5. start prospective capture of official releases and filing metadata for supported markets and sources;
6. add narrow point-in-time fundamental snapshots only for fields the approved feature definitions actually use;
7. start prospective minute-bar capture for the active live-paper universe and benchmark/reference instruments;
8. backfill a recent window of minute bars where vendor limits and cost make it practical;
9. start prospective news ingestion immediately once symbol mapping and retention rules are ready;
10. backfill historical news metadata and text only where rights, cost, and timestamp quality are acceptable.

This means the right answer is usually:

- daily first for broad historical foundation;
- minute immediately for prospective capture on the active live universe;
- recent minute backfill second, not first;
- news ingestion as early as possible, but only once storage and licensing rules are explicit.

Under a constrained shared provider budget, expensive news endpoints should not sit in the same continuous hot path as minute-level price and FX refresh.

For `v1`, that means:

- broad prospective news observation should default to low-cost or free feeds such as RSS and official publisher feeds where legally and operationally acceptable;
- paid provider endpoints should be reserved for targeted enrichment rather than full-universe continuous polling;
- price and FX freshness should take priority over optional news enrichment when they share the same provider budget.
- official releases and filing metadata should be treated as high-priority structured context, because missing them creates avoidable blind spots in catalyst research even when compute is constrained.

### 8.7 History depth requirements

Recommended history depth for `v1`:

- equities, benchmarks, and sector references in the broader US+UK research corpus: ideally `10` years of daily history, with `5` years as a practical minimum where a full decade is not available or data quality is weaker;
- FX pairs used for conversion and attribution: ideally `10` years of daily history for required conversion series such as `GBP/USD`, with `5` years as a practical minimum if the full retained market corpus cannot yet be aligned cleanly;
- minute bars: begin prospective collection from day one, with an ideal historical backfill of `6` to `12` months and a practical minimum of `60` to `90` trading days if minute data is used mainly for entry timing and diagnostics;
- official release and filing metadata: ideally `3` to `5` years where rights and source availability permit, with prospective capture prioritized over deep backfill;
- point-in-time fundamental snapshots: retain the full available as-reported history for the specific fields actually used, but do not block `v1` on a giant fundamentals warehouse;
- news metadata/headlines: ideally `12` to `36` months of historical coverage if licensing permits, but prospective collection is more important than deep backfill if historical rights are poor;
- corporate actions and event history: cover the full period of retained daily bars so label construction and adjusted-price logic stay coherent.

The key principle is:

- daily history should be deep enough to expose multiple regimes across both markets and to create a genuinely large learning dataset;
- minute history only needs to be deep enough to support the intended entry-timing and diagnostic features for the active live-paper universe in `v1`, not to recreate a decade of intraday history for every research-corpus name;
- prospective data collection should begin as early as possible so the system starts building a clean research tape immediately.

### 8.8 Market data to obtain

The system should obtain at least the following structured data:

- instrument master data: symbol, exchange, currency, sector, industry, active status, benchmark mappings, and research-corpus versus live-universe membership context;
- daily OHLCV bars for each tracked instrument in the US+UK research corpus;
- minute OHLCV bars for each actively researched instrument and the chosen benchmarks;
- benchmark/index series for market-relative labels across both supported markets;
- sector reference series for sector-relative labels across both supported markets;
- FX series for non-GBP instruments and any explicit FX beta universe;
- corporate actions: splits, symbol changes, mergers where relevant, and dividend events where they affect adjusted history or event labeling;
- event calendar data: earnings dates, dividend dates, macro events if used, and any scheduled catalyst calendar the strategy relies on;
- official release and filing references, timestamps, and extracted structured event fields for approved sources;
- point-in-time fundamental snapshots for any fields that become research-approved, such as shares outstanding, market cap context, or selected statement-derived fields;
- exchange-calendar and market-session metadata sufficient to classify whether a bar or article arrived during market hours, after-hours, or on a market holiday.

Where possible, the beta should reuse the project's existing price and FX infrastructure first rather than standing up a parallel market-data path before the research need is proven.

### 8.9 Price data acquisition strategy

For price data, the preferred `v1` approach is:

- use the existing daily and live pricing infrastructure as the first acquisition path for equities and FX where it already fits the required coverage;
- backfill the broader US+UK daily research corpus before making full minute history a hard dependency;
- start prospective minute ingestion for the approved universe as soon as the universe is frozen;
- store raw minute bars even if many strategies later use only derived hourly or daily features;
- derive hourly aggregates from raw minute bars when possible so intraday views remain consistent with the underlying tape;
- keep benchmark and sector-reference series on the same or closely aligned timestamps as the traded universe;
- treat decade-scale daily history as the main multi-market learning asset and minute history as the narrower live-operating asset.

The project already includes Twelve Data-based daily, FX, and streaming price services, so that path should normally be the starting point for `v1` rather than introducing a second price stack immediately.

### 8.10 Price data storage model

The storage design should preserve both raw facts and research-ready traceability.

Recommended tables or logical entities:

- `instrument_master`;
- `daily_bars`;
- `minute_bars`;
- `benchmark_bars`;
- `sector_reference_bars`;
- `fx_bars`;
- `corporate_actions`;
- `event_calendar`;
- `data_ingestion_runs`.

Each bar or event record should retain at least:

- instrument identifier;
- timeframe;
- event or bar timestamp;
- source/provider name;
- source timestamp if different from bar timestamp;
- ingested timestamp;
- open/high/low/close/volume where applicable;
- adjustment basis or adjustment reference;
- correction or revision marker if the source later changes the record;
- raw payload reference or checksum where practical.

This makes it possible to replay not only the strategy outcome, but also the state of the underlying market data used to form the signal.

### 8.11 News acquisition strategy

News should be treated as a first-class data pipeline, not an informal add-on.

Recommended `v1` news approach:

- ingest from a feed that provides stable article identifiers, publisher/source metadata, and reliable publication timestamps;
- prefer a source that can provide symbol tags or enough metadata for deterministic post-ingestion entity linking;
- collect news continuously, not only when a recommendation is triggered;
- ingest prospective news from day one even if historical backfill is limited;
- backfill historical news only to the extent that timestamp quality, rights, and budget remain defensible;
- treat follow-up stories, duplicates, and breaking-news revisions as separate but linked events.

If a source does not provide trustworthy timestamps or stable identifiers, it should not be used for core research labels.

Given the current shared Twelve Data budget is tight relative to minute-level market-data needs, Twelve Data should not be the primary continuous news-ingestion path for `v1`.

Recommended source strategy:

- use RSS and official publisher or regulator feeds as the primary broad-observation layer;
- use Twelve Data only as a secondary, targeted enrichment source where its endpoint coverage is useful and the credit cost is justified;
- if Twelve Data press-release ingestion is enabled, restrict it to promoted strategy symbols, active paper positions, or scheduled low-frequency sweeps rather than continuous full-universe polling;
- keep separate throttles and monitoring for news enrichment so optional news pulls cannot consume the minute budget needed for prices and FX;
- when provider budget is tight, freeze paid news enrichment first and continue ingesting RSS or official feeds.

### 8.12 News storage model

News storage should separate raw article records, linkage, and derived features.

Recommended entities:

- `news_articles_raw`;
- `news_article_links`;
- `news_text_snapshots`;
- `news_features`;
- `news_ingestion_runs`.

Recommended raw article fields:

- vendor article id;
- canonical URL;
- publisher/source;
- headline;
- summary/description if available;
- full text only if licensing permits storage;
- publication timestamp;
- first-seen timestamp;
- ingestion timestamp;
- language;
- duplicate or follow-up relationship fields;
- raw payload reference or checksum.

Recommended linkage/feature fields:

- linked symbol or symbols;
- linkage method and linkage confidence;
- event/category tags;
- sentiment score and model version;
- novelty/duplicate score;
- extracted rationale or key phrases;
- whether the article occurred in-market, pre-market, after-hours, or off-session.

If licensing does not permit full article storage, the system should still store:

- the allowed metadata fields;
- a canonical URL;
- a stable content fingerprint if permitted;
- all derived features used in scoring;
- the exact model version that produced those features.

The guiding rule is that the beta must be able to explain what it knew from the news feed at the time, even if it cannot legally retain every byte of article text.

### 8.13 Feature store

The document already refers to feature snapshots, but the system should also maintain a reusable, versioned feature store.

Recommended entities:

- `feature_definitions`;
- `feature_store`;
- `feature_store_runs`.

Recommended feature-store fields:

- symbol;
- decision or feature timestamp;
- feature name;
- feature version;
- feature value;
- source references;
- calculation window;
- source timeframe;
- missing/null flag where relevant;
- created timestamp;
- build or run id.

Examples of stored features may include:

- short-horizon returns;
- VWAP distance;
- sector-relative strength;
- realized volatility;
- abnormal volume;
- news sentiment;
- FX context features.

The main rule is:

- features should not live only inside model code or one-off notebooks;
- features used for scoring should be versioned, queryable, and reusable across research runs;
- recommendation evidence and the score tape should point back to feature-store rows where practical.

### 8.14 Label store

The canonical learning target should also exist as an explicit, versioned label layer rather than being recomputed ad hoc in each experiment.

Recommended entities:

- `label_definitions`;
- `labels`;
- `label_runs`.

Recommended label fields:

- symbol;
- decision timestamp;
- target horizon;
- market benchmark used;
- sector benchmark used;
- return in local currency;
- FX-adjusted return;
- return in GBP if applicable;
- market return;
- sector return;
- excess return;
- cost-adjusted return;
- optional derived outcomes such as target-hit, stop-hit, and drawdown flags;
- label version;
- label finalized timestamp;
- source references.

The label store should preserve the exact outcome definition used by a research run so the same inputs and same label version always produce the same target values.

### 8.15 Training dataset builder

The system should not assemble training data ad hoc inside individual model scripts.

It should have a reproducible training-dataset builder that joins:

- feature store rows;
- label store rows;
- universe membership;
- benchmark mappings;
- eligibility state and related context where needed.

Recommended entities:

- `dataset_builder_runs`;
- `training_rows`;
- `dataset_manifests`.

Recommended dataset fields:

- decision timestamp;
- symbol;
- feature-set version;
- label version;
- universe version;
- benchmark-mapping version;
- dataset version;
- split or fold assignment for train/validation/test or walk-forward use;
- optional strategy or experiment id.

The dataset builder should make it possible to say:

- which exact rows were used to train this model;
- which feature versions and label versions were used;
- which universe and benchmark mappings were in force;
- which rows were held out for evaluation.

## 9. Signal and Prediction Strategy

The system should separate feature engineering, signal generation, and trade decisioning.

### 9.1 Feature families

Recommended feature families for phase 1:

- short-horizon returns (`1m`, `5m`, `15m`, `60m`);
- medium-horizon returns (`1d`, `3d`, `5d`, `10d`);
- opening-range behavior;
- price relative to intraday VWAP;
- price relative to multi-day moving references;
- realized volatility and volatility expansion;
- abnormal volume;
- sector-relative strength;
- broad-market regime state;
- macro event proximity and relative-currency context if FX is enabled;
- distance to earnings or other scheduled events;
- news-event presence and sentiment buckets;
- time-of-day and day-of-week features.

### 9.1.1 Time-horizon flexibility

The target beta is not primarily an intraday trading system.

The expected shape is closer to:

- small moves in the rough range of `2` to `5` percent when the setup is correct;
- multi-day holding periods;
- a relatively low recommendation cadence, potentially around `1` to `2` trades per week in early versions.

This means:

- minute bars remain valuable for entry timing, volatility context, and signal diagnosis;
- hourly and daily bars may be more appropriate for some prediction targets;
- strategy versions should declare their operating horizon explicitly rather than inheriting a minute-level assumption by default.

### 9.2 Prediction target

The system needs a clearly defined target. Good examples:

- probability that a stock outperforms a threshold over the next `N` minutes or hours;
- expected return over a fixed holding window after friction;
- probability that price reaches target before stop within the holding horizon.

The first beta should choose one primary target and keep the others as diagnostic labels.

For the initial swing-oriented beta, the most likely primary targets are:

- expected net return over a multi-day holding window after friction;
- probability of achieving a modest target move before a defined stop within that window.

### 9.2.1 Canonical v1 learning target

The first version should choose one canonical learning target for the research loop, even if multiple diagnostic labels are retained.

Recommended canonical `v1` target:

- `5-trading-day excess return versus market and sector benchmarks after estimated trading costs`.

This is a strong first target because it:

- matches the intended swing-style horizon;
- measures stock-specific edge rather than broad market drift;
- produces a continuous label that is useful for ranking as well as thresholding;
- is easier to compare across traded and non-traded opportunities;
- keeps the first learning loop focused on whether the model can identify relative outperformance after friction.

Other target definitions such as target-before-stop probability should remain available as secondary labels, not the primary research anchor.

The following must be versioned explicitly for this canonical target:

- excess-return formula;
- benchmark mapping logic;
- sector mapping logic;
- FX conversion rules;
- cost inclusion assumptions;
- label-construction timestamps and cut-off rules.

### 9.2.2 Prediction, trade rule, and allocation layers

The system should treat the following as separate layers:

- `prediction`: estimate the expected excess return, probability, or rank for each eligible symbol;
- `trade rule`: convert a prediction into an entry, stop, target, holding window, and execution assumptions;
- `allocation`: determine which valid opportunities receive paper capital under portfolio constraints.

In `v1`, the learning problem should focus primarily on prediction quality and ranking quality. Targets and stops are useful execution rules, but they should not define the entire research objective.

### 9.2.3 Confidence definition

Confidence should be treated as a formal research object, not a vague "model strength" score.

Each scored decision should retain:

- raw prediction output;
- normalized confidence score;
- confidence band mapping;
- confidence schema or version identifier;
- any uncertainty estimate produced by the model.

The system should be able to demonstrate that high-confidence decisions behaved differently from low-confidence decisions across out-of-sample data.

### 9.3 Model strategy

Recommended order of sophistication:

1. baseline rules and simple models first;
2. interpretable model versions before deep black-box models;
3. promote only models that outperform baselines across walk-forward periods.

Candidate sequence:

- rules-only baseline;
- linear / tree-based interpretable models;
- ensemble only after the evidence layer is mature.

### 9.3.1 Initial promoted strategy families

The first promoted strategy families should remain narrow and interpretable.

Recommended initial families:

- trend continuation with pullback recovery;
- catalyst plus confirmation.

Neither family should have default priority in the research loop. Both should be allowed to compete in parallel until prospective evidence separates them.

These are strong starting points because they are easier to:

- diagnose;
- explain;
- refine;
- retire if the evidence fails.

### 9.4 Recommendation emission

A recommendation should only be emitted when:

- the symbol is in the approved universe;
- the data freshness checks pass;
- the strategy is inside allowed market hours;
- the confidence threshold is met;
- the expected edge exceeds estimated friction;
- the position would respect portfolio and risk constraints.

If any gate fails, the system should log a rejected candidate event rather than silently doing nothing.

The recommendation engine should be free to emit infrequent, higher-conviction candidates. Low trade count is acceptable if the resulting evidence quality is high.

The research system should still score the full eligible universe at each decision point, even when no recommendation is emitted and even when capital is fully allocated.

## 10. Paper Trade Construction

The recommendation is only half the story. The beta must define how paper trades are formed and managed.

### 10.1 Starting capital

- base cash ledger starts at `GBP 10,000`;
- all paper positions draw from this ledger;
- realized and unrealized P/L update the available capital;
- if trading non-GBP securities, store the FX rate used at entry and exit.

### 10.2 Position sizing

Recommended default constraints:

- soft max capital per position;
- soft max risk per trade;
- soft max sector exposure;
- dynamic throttling of new recommendations when portfolio crowding, drawdown, or data stress increases;
- optional cooling-off period after repeated losses in one name or sector;
- portfolio-level ability to move partially or fully to cash when credible risk-off conditions appear.

Good starting behavior is confidence- and expected-edge-weighted sizing with diversification controls, not naive equal-weighting or unconstrained concentration.

### 10.3 Entry assumptions

The fill model must avoid hindsight. Recommended order:

1. decision made on the close of minute `t`;
2. earliest simulated fill is next eligible bar, not the signal bar;
3. fill price uses a configurable assumption such as next-bar open, next-bar VWAP, or worse-of-open-and-spread-adjusted price;
4. if volume or data quality is insufficient, mark the recommendation as not filled.

### 10.4 Exit parameters

Each paper trade should carry:

- entry timestamp;
- planned holding horizon;
- target price or target return;
- stop-loss or invalidation threshold;
- time-based exit;
- optional trailing rule;
- exit reason taxonomy.

The initial beta should assume many trades are multi-day rather than same-day unless a strategy version is explicitly designed as intraday.

### 10.5 Exit reason taxonomy

Recommended exit reasons:

- target hit;
- stop hit;
- time expiry;
- strategy reversal;
- risk-off regime exit;
- stale data safety exit;
- market close flattening;
- manual beta override;
- universe removal;
- risk-limit breach.

### 10.6 Fees, spread, and cost modeling

All reported performance should be net of applicable costs, never gross-only.

The project already has fee and FX settings infrastructure, so the beta should reuse and extend that foundation rather than invent a disconnected cost model.

Recommended rules:

- use the existing broker-fee configuration and estimation path for supported equity paper trades;
- treat spread and slippage as part of the execution model, not as optional commentary;
- if FX paper trades are enabled, add a versioned FX cost schedule covering spread, commission if any, and any explicit conversion cost;
- if overnight FX behavior is ever enabled later, financing or rollover assumptions must also be versioned;
- if the system cannot determine the applicable fee schedule for an instrument, the recommendation should be ineligible rather than silently costed at zero.

## 11. Price Update and Session Strategy

The user requested minute-level updates during live trade. That is workable, but the operational rule set matters.

### 11.1 Refresh cadence

- ingest fresh price data once per minute during regular market hours;
- record bar timestamp, source timestamp, and ingestion timestamp;
- do not allow decisioning on data that fails freshness thresholds.

### 11.2 Session control

The engine should know:

- which market session is open;
- which symbols are currently eligible;
- when a trading day starts and ends;
- whether a symbol is halted;
- whether a market holiday is active.

### 11.3 Phase 1 simplification

Recommended phase 1:

- regular-hours only;
- no pre-market / after-hours fills;
- overnight holds are allowed by default for strategies that remain valid outside the entry session;
- forced flat by session close should apply only to strategies that are intentionally intraday.

## 12. Auditability Requirements

Auditability must be a first-class feature, not an afterthought.

### 12.1 Immutable decision log

Every candidate, accepted recommendation, rejection, paper fill, modification, and closure should be append-only.

This immutable log should also cover:

- hypothesis creation;
- hypothesis promotion from playground to demo-trade status;
- hypothesis retirement or suspension;
- every demo-trade lifecycle event.

### 12.2 Evidence package per recommendation

Each recommendation should store at least:

| Field | Why it matters |
|---|---|
| `recommendation_id` | Stable reference across all downstream records |
| `strategy_version` | Reproducibility |
| `model_version` | Reproducibility |
| `decision_timestamp` | Ordering and replay |
| `symbol` | Trade identity |
| `feature_snapshot` | Why the model scored the name |
| `feature_store_refs` | Links to the exact derived features used at score time |
| `score_breakdown` | Explainability |
| `confidence_marker` | Decision confidence at the moment of action |
| `raw_input_refs` | Link back to bars, news, events |
| `portfolio_state_snapshot` | Constraint context |
| `risk_limits_snapshot` | Why size/exposure were allowed |
| `entry_rule` | Entry logic |
| `target_rule` | Success definition |
| `stop_rule` | Failure definition |
| `expected_horizon` | Evaluation window |
| `prediction_target_version` | Which canonical label was being optimized |
| `benchmark_refs` | Market/sector reference basis for excess-return evaluation |
| `estimated_friction` | Realism |
| `decision_explanation` | Human-readable evidence summary |

### 12.3 Full-universe score tape

The system should persist a score tape for every eligible symbol at every decision timestamp, not only for executed or recommended trades.

Each score-tape row should capture at least:

- decision timestamp;
- symbol;
- strategy version;
- model version;
- prediction target version;
- raw prediction output;
- predicted excess return or probability;
- normalized confidence score;
- confidence or uncertainty estimate;
- confidence band or confidence label;
- confidence schema version;
- rank within the eligible universe;
- benchmark references used for evaluation;
- feature-store reference or feature-set version;
- key feature snapshot or feature reference;
- eligibility flags;
- whether the symbol was recommended, rejected, or bypassed due to allocation limits;
- the reason it was not traded if not traded.

This tape is essential for learning quickly without learning incorrectly. It allows the system to compare traded names, rejected names, and ignored names inside one consistent research dataset.

### 12.4 Rejected-candidate log

The system should also store why candidates were rejected:

- insufficient confidence;
- edge below cost;
- stale or missing data;
- portfolio full;
- sector cap reached;
- market closed;
- symbol halted;
- manual suppression.

Rejected-candidate analytics are important. They show whether the system is too strict, too loose, or frequently blocked by poor data hygiene.

### 12.5 Data and research artifact retention

Retention should preserve:

- raw minute bars used for decisioning;
- raw news records and metadata;
- corporate-event snapshots;
- universe membership history;
- potential-signal candidates and their evidence trackers;
- feature-store rows and feature definitions;
- label-store rows and label definitions;
- dataset-builder manifests and dataset-version metadata;
- strategy configuration versions;
- model artifacts and strategy metadata;
- AI or automated review runs and their structured findings;
- evaluation outputs.

### 12.6 Replay capability

The system should support "replay recommendation `X`" and show:

- what data was known at that moment;
- what the model predicted;
- which constraints were active;
- which gates passed or failed;
- how the simulated fill was chosen;
- how the recommendation ultimately performed.

## 13. Success / Failure Evaluation Strategy

The user asked for a system that can determine whether a recommendation was correct. That must be defined carefully.

### 13.1 Avoid a single binary label

One binary "correct / incorrect" field is too crude. A recommendation can:

- make money but underperform the benchmark;
- hit the target after violating the intended drawdown;
- fail the nominal trade but still improve calibration;
- be invalid because the data was stale or the fill assumption was unrealistic.

### 13.2 Recommended outcome labels

Store multiple labels per paper trade:

- `net_positive`: closed P/L after friction > `0`;
- `target_before_stop`: target hit before stop;
- `beat_benchmark`: trade beat a chosen benchmark over the same horizon;
- `max_drawdown_exceeded`: trade violated tolerated adverse move;
- `confidence_calibrated`: realized outcome aligned with score bucket;
- `execution_feasible`: fill assumptions remained plausible.

### 13.3 Strategy-level performance metrics

Track at minimum:

- total return on paper capital;
- hit rate;
- average win;
- average loss;
- expectancy;
- max drawdown;
- profit factor;
- turnover;
- exposure by symbol and sector;
- P/L after estimated friction;
- benchmark-relative return;
- calibration by score decile;
- win/loss by holding horizon.

### 13.4 Diagnostic cuts

The system should support slicing performance by:

- symbol;
- sector;
- market regime;
- time of day;
- day of week;
- news score bucket;
- presence or absence of news;
- event proximity;
- volatility regime;
- model version;
- signal family;
- position size bucket;
- holding horizon;
- data freshness status.

This is how the team answers questions like:

- does a specific news score underperform?
- is the opening hour systematically weak?
- are late-day recommendations noisier?
- does one model version only work in strong momentum regimes?
- which signals appear weak individually but stronger in combination?

### 13.5 Baselines

The beta should not judge itself in a vacuum. Baselines should include:

- do nothing;
- equal-weight universe hold over the same window;
- simple momentum rule;
- random eligible picks with identical holding rules;
- previous model version.

Without baselines, impressive-looking paper returns may be meaningless.

### 13.6 Signal-learning outputs

The beta should not stop at recording paper P/L. It should produce structured learning about signal quality.

Recommended outputs:

- hypothesis leaderboard for the learning playground;
- per-signal performance summaries;
- per-signal-combination performance summaries;
- feature-importance or contribution views where the model type supports them;
- regime-specific scorecards;
- rejection-reason analytics;
- confidence calibration reports;
- lists of signals that looked promising pre-cost but collapsed after friction;
- lists of signals that are weak standalone but useful in combination.

The learning playground should therefore answer:

- which markers appear promising enough to justify prospective testing;
- how stable those markers are across regimes;
- what confidence levels those markers tend to produce;
- which hypotheses deserve promotion into immutable demo-trade testing.

### 13.7 Alpha and benchmark attribution

The system should distinguish between:

- absolute return;
- market-relative return;
- sector-relative return;
- return after estimated costs.

Where possible, each outcome should be decomposed into:

- market move;
- sector move;
- instrument-specific excess move;
- execution-cost drag.

This prevents the model from being rewarded simply for participating in a broad upswing.

### 13.8 Counterfactual attribution

The system should also explain why a good or bad outcome occurred at the workflow level.

Recommended counterfactual questions:

- was the prediction wrong?
- was the prediction directionally right but below the recommendation threshold?
- was the symbol ranked correctly but not allocated capital?
- was the trade rule poor even though the underlying prediction was useful?
- did costs or fill assumptions erase an otherwise valid edge?

This helps the system learn whether failure came from forecasting, thresholding, execution design, or portfolio allocation.

### 13.9 Statistical validity rules

The beta should define explicit statistical validity rules before trusting a signal, strategy version, or reported edge.

Recommended rules:

- require minimum observation counts before drawing conclusions from a signal or model version;
- show confidence intervals or uncertainty bands around key metrics such as excess return, hit rate, and calibration;
- compare results against baselines across multiple out-of-sample windows, not one favorable slice;
- avoid promoting or retiring strategies based on very small samples or short-lived performance bursts;
- record how many materially distinct trials, parameter sweeps, and feature-set variants were attempted before a candidate was selected;
- require selection-bias-aware diagnostics such as Deflated Sharpe Ratio for promotion candidates and Probability of Backtest Overfitting where broad model-search activity occurred;
- use stronger multiple-testing controls such as Reality Check or SPA-style review when strategy families are mined across large numbers of related variants;
- define degradation and retirement criteria in advance so weak strategies can be paused without ad hoc justification.

The goal is to prevent noise from being mistaken for learning.

### 13.10 Confidence calibration and governance

Confidence should be reviewed as a governed metric, not merely displayed as an explanatory artifact.

Recommended requirements:

- compare realized outcomes across confidence bands;
- test whether higher-confidence predictions historically outperform lower-confidence predictions on the canonical target;
- detect when confidence compression or overconfidence emerges after model changes;
- version confidence-band mappings so historical calibration remains reproducible;
- avoid promoting models whose confidence ordering is weak, unstable, or misleading.

## 14. Research and Backtesting Methodology

The system should not go straight from idea to live paper recommendations. It needs a disciplined research path.

### 14.0 How the system actually learns

The system should learn from four connected datasets rather than from executed trades alone:

1. feature observations: what signals and contextual facts existed for a symbol at a decision time;
2. prediction outputs: what the model or rule predicted, how strongly, and how the symbol ranked;
3. realized outcomes: what happened afterward on the canonical target and on supporting diagnostic labels;
4. allocation and execution decisions: whether the symbol was traded, skipped, resized, or blocked by governance or portfolio constraints.

Combining these layers allows the system to distinguish:

- ideas that were genuinely predictive;
- ideas that ranked well but never received capital;
- ideas that were directionally useful but undermined by the trade rule;
- ideas whose apparent edge disappeared once costs, FX effects, or allocation limits were applied.

The learning architecture should therefore be layered explicitly:

1. instrument master;
2. raw fact store for bars, FX, news, events, and calendars;
3. feature store;
4. label store;
5. reproducible training-dataset builder;
6. model and rule research;
7. strategy registry and research registry;
8. live scoring or shadow mode;
9. full-universe score tape;
10. promotion governance;
11. paper execution engine;
12. immutable demo-trade ledger;
13. evaluation, attribution, and learning-playground review.

### 14.1 Historical pipeline

Build a historical research pipeline that:

- uses time-ordered data only;
- supports walk-forward evaluation;
- freezes universe membership by date;
- builds aligned market and sector benchmark series for excess-return labels;
- versions the canonical label-construction rules explicitly;
- materializes labels through the label store rather than recomputing them informally inside each experiment;
- applies realistic cost assumptions;
- preserves feature definitions by version.

### 14.2 Leakage controls

Explicitly prevent:

- look-ahead price leakage;
- revised-news timestamp leakage;
- post-event labeling leaks;
- survivorship bias in the stock universe;
- using future corporate-action knowledge;
- repeatedly reusing the same holdout slice until it becomes an implicit training set;
- silent reclassification of poor outcomes.

### 14.2.1 Required research-validation controls

The beta should not treat statistical validation as optional polish.

Required controls:

- use purged and, where appropriate, embargoed time-series cross-validation when label horizons overlap and naive folds would leak future information;
- maintain at least one untouched late-stage holdout window that is not repeatedly used during normal feature or threshold tuning;
- persist trial counts, search families, and major parameter grids so later evaluation can distinguish genuine edge from heavy data mining;
- compute and store selection-bias-aware diagnostics for promotion candidates, including Deflated Sharpe Ratio and related trial-aware metrics where applicable;
- run stronger multiple-testing review for broad model-search or parameter-mining exercises before promotion decisions are made;
- treat any candidate that cannot be validated with these controls as research-incomplete, not promotion-ready.

### 14.3 Promotion criteria

A strategy version should only be promoted into live paper mode if it:

- beats chosen baselines across multiple walk-forward windows;
- has enough scored observations to make the conclusion meaningful for its horizon and cadence;
- shows benchmark-relative strength on the canonical target;
- does not depend on one narrow regime;
- remains positive after conservative friction;
- shows uncertainty bounds that remain defensible relative to baseline;
- has acceptable drawdown behavior;
- produces explainable recommendation evidence;
- has a documented hypothesis rationale.

Promotion should favor robustness over speed. It is acceptable for a candidate strategy to remain in research or shadow mode for an extended period if more evidence is needed across different market conditions.

Retirement or suspension criteria should also be defined in advance so weak strategies are paused through explicit governance rather than quiet abandonment.

### 14.4 Research registry

Each research experiment should be logged with:

- hypothesis;
- dataset period;
- universe version;
- feature set version;
- model version;
- canonical target version;
- acceptance criteria;
- backtest result summary;
- decision to promote, reject, or rework.

This reduces p-hacking and memory-hole behavior.

## 15. Beta Workflow

A practical operating workflow should look like this.

### 15.1 Phase 0: governance and scope lock

- define the experimental boundary;
- define UI labels and disclaimers;
- define the approved universe rule;
- define the UK-first live-paper focus and the broader US+UK learning corpus;
- define the canonical `v1` learning target;
- define the benchmark set used for excess-return evaluation;
- define the cost model version;
- define success criteria for the beta.

### 15.2 Phase 1: data and audit foundation

- build symbol universe store;
- define the broader US+UK historical research corpus separately from the narrower live-paper universe;
- start with an app-selected seed live-paper universe of roughly `50` liquid names and let later expansion be system-driven and auditable rather than manual by default;
- backfill ideally `10` years of daily history for the research corpus and its benchmark/reference/FX set, with `5` years as the practical minimum where a full decade is not yet available;
- build minute-bar ingestion;
- start prospective minute and news collection for the active live-paper universe as early as possible once symbol mapping is stable;
- build hourly and daily aggregation paths derived from the same timestamped source history;
- build benchmark and sector reference series for attribution and excess-return labels;
- build news ingestion and retention;
- make RSS or official-feed ingestion the default broad news path, with paid provider enrichment kept out of the minute-by-minute critical path;
- backfill only a recent, defensible window of minute and news history rather than blocking on perfect deep intraday history;
- build immutable event log;
- build feature definitions and the feature store;
- build label definitions and the label store;
- build the reproducible training-dataset builder;
- build the full-universe score tape store;
- build strategy and model version registry.

### 15.3 Phase 2: offline research

- engineer candidate features;
- materialize versioned feature sets through the feature store;
- materialize canonical labels through the label store;
- build reproducible training datasets from versioned features, labels, and universe rules;
- test baselines;
- run walk-forward backtests;
- evaluate prediction quality separately from trade-rule quality and allocation effects;
- let both initial candidate strategy families compete in research without forcing a default winner too early;
- determine which operating horizon is most defensible for each candidate strategy;
- promote only explicit, versioned, and auditable hypotheses from the learning playground into the demo-trade lane, whether the promotion decision is system-driven or manually triggered;
- define recommendation explanation templates.

### 15.4 Phase 3: shadow live scoring

- start shadow scoring automatically once observation, freshness checks, and minimum evidence plumbing are healthy;
- score the full eligible universe live without creating paper positions;
- log all accepted and rejected candidates;
- persist ranked predictions even when nothing is traded;
- attach confidence markers to every scored decision and promotion candidate;
- verify freshness, timing, and evidence quality;
- validate that replay works.

### 15.5 Phase 4: paper execution beta

- keep paper execution capability present from the start of the beta runtime, but block new demo entries until candidate signals, hypothesis state, and validation gates are ready;
- turn on paper fills automatically once those gates are satisfied;
- start with `GBP 10,000` ledger;
- allow sizing to follow model confidence and expected edge while still applying soft exposure and drawdown controls;
- compare executed paper outcomes with the broader score tape so allocation effects are visible;
- ensure demo-trade records are immutable once created, apart from append-only lifecycle updates;
- allow the system to auto-pause new entries when prospective live performance degrades materially, while continuing observation, shadow scoring, and learning;
- allow the system to auto-resume entries when recovery conditions are met;
- review recommendations daily and weekly;
- freeze strategy versions during each evaluation window.

Early success should be defined by evidence quality and learning quality, not by achieving a high paper trade count.

### 15.6 Phase 5: evaluation and refinement

- review outcomes by signal family and time segment;
- identify failure clusters;
- adjust only through versioned changes;
- rerun the shadow period after major model updates.

The refinement loop should be intentionally patient. Fewer, better-supported changes are preferable to rapid churn driven by short-term results.

## 16. UI and Product Strategy

This beta should not be blended invisibly into the main deterministic product.

### 16.1 Naming

Avoid live-trading language in the UI. Good labels:

- `Paper Trading Beta`;
- `Signal Lab`;
- `Experimental Prediction Beta`.

Less safe labels:

- `Trade Now`;
- `Buy Recommendation`;
- `Action Center`.

### 16.2 Core beta surfaces

Recommended surfaces:

- beta overview / paper portfolio page;
- learning playground / hypothesis lab;
- universe monitor;
- watched opportunities page;
- live signal queue;
- active paper positions;
- immutable demo-trade ledger;
- closed recommendation ledger;
- recommendation replay / evidence panel;
- health, jobs, and runtime-mode page;
- performance diagnostics dashboard;
- research registry;
- model/version admin page.

The learning playground should remain broader than the immutable demo-trade lane. That asymmetry is intentional.

The default beta UI should favor high-signal summary over raw detail.

Recommended default view:

- one overview page that behaves like a `paper portfolio` rather than a research notebook;
- one compact summary strip that combines current actionable state with live learning/progress metrics;
- top-level summary of active paper positions, recent closed paper trades, and current watched opportunities or signal candidates;
- visible counts for signals identified, promoted, rejected, dismissed, and currently watched so progress is obvious even before many trades exist;
- visible trend indicators showing whether learning quality, confidence ordering, or live prospective performance is improving or deteriorating;
- in-app milestone notifications for important automatic actions such as hypothesis promotion, demo-trade open/close, risk-off activation, and auto-pause or auto-resume events;
- drill-down links into replay and evidence views only when needed;
- avoid making the default landing page a wall of model internals, raw feature values, or article-level diagnostics.
- keep the overview materially shorter and simpler than the main Portfolio page, because the beta's purpose is monitoring and review rather than full holdings administration.

### 16.3 Mandatory warnings

Every beta page should state:

- paper-only;
- no live execution;
- no broker connectivity;
- experimental;
- based on model assumptions and incomplete market information.

## 17. Governance, Access, and Data Rights

The beta should be controlled as tightly as any other high-risk internal experiment.

### 17.1 Access control

- keep the beta behind an explicit feature flag;
- restrict access to named internal testers;
- separate beta permissions from normal portfolio permissions;
- log every user access to beta research, positions, and admin controls;
- require elevated permission for strategy changes, universe edits, and manual overrides.

### 17.2 Strategy approval and change control

No strategy version should appear in live paper mode unless it has:

- a registered hypothesis;
- a versioned configuration;
- documented backtest evidence;
- an owner;
- a reviewer;
- an effective start time and planned review window.

Material changes to thresholds, sizing, targets, stops, or feature sets should create a new version, not silently mutate an existing one.

### 17.3 Data licensing and vendor rights

Before implementation, confirm that the chosen data sources allow:

- storage of minute-level bars;
- retention of raw and derived data for audit;
- use of news metadata and text-derived features;
- replay and internal analytics;
- any internal redistribution inside the application.

The fastest way to invalidate a serious beta is to build it on top of data rights that do not permit retention, replay, or derived-model usage.

### 17.4 Legal and compliance review

Even though this is paper-only, recommendation-like output can still create policy and regulatory questions.

Before exposing the beta beyond a narrow internal test group, review:

- whether the language used in the UI is too advice-like;
- whether disclaimers are sufficiently prominent;
- whether paper results could be misrepresented as proven investable performance;
- whether audit retention and user-action logging meet internal governance expectations.

This should be treated as a governance checkpoint, not as a late documentation clean-up task.

## 18. Operational Controls and Monitoring

The beta needs run-time discipline as much as research discipline.

### 18.1 Health and freshness monitoring

The system should continuously track:

- price feed freshness;
- news ingestion freshness;
- symbol mapping failures;
- halted or unavailable symbols;
- model scoring errors;
- clock skew between source, ingestion, and application timestamps.

### 18.2 Circuit breakers

The beta should stop or degrade gracefully when:

- price data is stale beyond threshold;
- the news pipeline is delayed or duplicating;
- a symbol's state is unknown;
- the strategy service cannot load the approved model version;
- the applicable fee schedule cannot be resolved for a tradable instrument;
- the paper ledger becomes inconsistent;
- an abnormal spike in recommendations suggests a logic fault.

Recommended safe behavior is "freeze new entries, keep logging, and raise alerts" rather than continuing with partial confidence.

If the shared external-data budget becomes constrained, the system should degrade in this order:

- freeze paid news enrichment;
- continue RSS or official-feed ingestion where available;
- preserve price and FX refresh for the approved research universe;
- alert that news coverage has degraded so evaluation later accounts for the reduced source mix.

### 18.3 End-of-day controls

At market close, the system should:

- finalize end-of-day marks;
- reconcile open positions and cash;
- tag any stale or incomplete fills;
- persist daily snapshots for later replay;
- produce a daily beta summary for review.

### 18.4 Drift review

Model and strategy drift should be reviewed on a fixed cadence.

Review areas:

- deterioration by score bucket;
- deterioration by time-of-day;
- deterioration after market-regime changes;
- rising rejection rates caused by data quality issues;
- recommendation count spikes or collapses after version changes.

## 19. Risks, Failure Modes, and What Would Detract

The main ways this beta can fail are operational, statistical, and behavioral.

### 19.1 Statistical failure modes

- overfitting to one historical regime;
- too many features for too few genuine events;
- mistaking noise for edge;
- using weak labels;
- relying on a score that is not calibrated.

### 19.2 Data failure modes

- stale minute bars;
- missing or delayed news timestamps;
- symbol mapping errors;
- bad corporate-action adjustments;
- hidden changes in data vendor behavior;
- missing halt and holiday awareness.

### 19.3 Product failure modes

- users treating paper recommendations as live advice;
- unclear separation from the deterministic core product;
- manual cherry-picking of trade entries;
- inconsistent exit rules across strategies;
- too many strategies running at once.

### 19.4 Evaluation failure modes

- grading trades only on gross return and ignoring cost;
- changing success definitions after outcomes are known;
- removing losing signals without logging the reason;
- changing the universe too often;
- not comparing against baselines.

## 20. What Would Enhance the Beta

These choices would improve the odds of producing useful results:

- narrow the launch scope to one market and one or two simple strategies;
- prefer liquid names with strong data coverage;
- reuse the project's existing fee-estimation and FX freshness infrastructure instead of creating a parallel cost layer;
- choose one canonical `v1` prediction target rather than optimizing many targets at once;
- allow multiple observation horizons while forcing each strategy to declare its actual decision horizon;
- freeze the universe for fixed windows;
- score the whole eligible universe continuously rather than learning only from executed trades;
- log rejected candidates as well as accepted ones;
- use interpretable models first;
- keep strategy versions stable during evaluation periods;
- accept long evidence-collection windows when needed rather than forcing early conclusions;
- preserve full replay and evidence views;
- analyze by time-of-day, regime, and news bucket from day one;
- maintain a research registry so ideas are tested, not memory-edited.

## 21. What Would Detract from the Beta

These choices would make the beta less credible:

- adding too many stocks, markets, or asset classes immediately;
- mixing paper-trading UX into the live portfolio surfaces;
- allowing silent manual overrides;
- using unversioned prompts or opaque AI summaries as signal drivers;
- ignoring spread, slippage, FX friction, or instrument-specific fee schedules;
- forcing every strategy into an intraday frame even when the evidence favors multi-day behavior;
- changing the prediction target repeatedly before the first one is properly validated;
- learning only from executed trades and ignoring the broader scored opportunity set;
- optimizing for fast iteration at the expense of data quality, label quality, or evaluation integrity;
- letting the stock universe drift opportunistically;
- scoring with incomplete timestamps;
- skipping the shadow-scoring stage.

## 22. Recommended First Release Shape

The most defensible first release is:

- UK-first live-paper focus at launch, backed by a broader US+UK historical research corpus and with US names allowed in shadow or demo lanes whenever coverage is strong enough;
- an app-selected seed active universe of roughly `50` liquid names, ideally UK-heavy rather than UK-only;
- automatic active-universe expansion once the system judges data coverage, research health, and operational stability to be good enough;
- a broader governed US+UK daily research corpus, ideally in the rough range of `1,000` to `2,000` liquid common equities after filtering;
- optional major-FX paper testing only if its fee and spread schedule is implemented, replayable, and shown to be beneficial;
- long-only paper execution with explicit bearish/risk-off signal tracking and the ability to move to cash automatically;
- regular-hours entry and exit handling, with multi-day holdings allowed for swing-style strategies;
- ideally `10` years of daily history for the research corpus, with `5` years as the practical minimum where a full decade is not yet available;
- prospective minute and news capture from the moment the active live-paper universe is frozen;
- minute-bar updates with a default scoring cadence of roughly every `5` minutes unless a strategy version justifies something faster or slower;
- hourly and daily strategy support from the same audited data foundation;
- RSS or official-feed news ingestion as the primary broad observation path, with Twelve Data used only for narrow, budget-aware enrichment if enabled;
- one canonical `v1` learning target: `5-trading-day excess return versus market and sector benchmarks after estimated costs`;
- `GBP 10,000` paper capital;
- no hard concurrent-position cap, but soft capital, concentration, and drawdown controls;
- both trend continuation with pullback recovery and catalyst plus confirmation treated as parallel initial candidate families plus at least one baseline;
- one learning playground for marker discovery and one immutable demo-trade lane for forward hypothesis testing;
- versioned feature store, label store, and training-dataset builder;
- full-universe score tape at each decision point;
- confidence markers on every scored decision;
- automatic shadow start once observation health is proven, and automatic demo entry once candidate and validation gates are satisfied;
- explicit separation of prediction, trade-rule, and allocation evaluation;
- full evidence package and replay;
- in-app notifications, daily dashboard snapshots, and live progress metrics so the beta visibly shows what it is learning;
- daily and weekly review analytics.

This is intentionally conservative. The first job is not to maximize paper return. The first job is to prove that the system can generate, explain, and evaluate recommendations honestly.

## 23. Recommended Documentation Follow-up

If this beta is approved for implementation, the documentation set should next be updated in this order:

1. `docs/todo.md`: add an explicit experimental predictive beta workstream and carve-out from the current scope guardrails.
2. `docs/STRATEGIC_DOCUMENTATION.md`: add a separate page/domain definition for paper-trading beta surfaces, with hard separation from the deterministic core.
3. `docs/README.md`: list this document as an exploratory strategy reference.
4. implementation review docs: add acceptance criteria, risk controls, and evaluation checkpoints for the beta.

## 24. Bottom Line

This beta is viable if it is treated as a research system with strict evidence capture, versioned strategy logic, and conservative paper execution assumptions.

It is not viable if it is treated as a vague "AI picks stocks" feature.

It is also not viable if it optimizes for speed of iteration ahead of correctness of learning.

The strongest version of this proposal is:

- small in scope;
- heavy on auditability;
- explicit about uncertainty;
- rigorous in evaluation;
- anchored on one primary learning target before expanding;
- able to learn from the full opportunity set, not only the chosen trades;
- patient enough to validate conclusions over time;
- clearly segregated from any live-trading interpretation.
