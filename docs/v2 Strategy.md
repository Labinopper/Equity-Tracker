product:
  name: "equity-tracker"
  target_version: "2.0"
  objective_shift:
    from: "visibility"
    to: "profit-maximization decision support (non-advice)"
  non_goals:
    - "SIP-specific roadmap work"
    - "full HMRC-compliance program (share matching/pooling as a product goal)"
    - "broker execution / auto-trading"
  global_guardrails:
    - "all outputs are informational; user makes decisions"
    - "every calculation shows assumptions + freshness flags"
    - "no hidden automation that can place trades"
    - "confidence signals: missing prices, stale FX, incomplete history"
  navigation_model:
    primary_tabs:
      - "Decide"
      - "Liquidity"
      - "Schemes"
      - "Risk"
      - "Simulate"
      - "Advanced"
    decision_surface_contract:
      - "Net If Sold Today"
      - "Gain vs True Cost"
      - "Net If Held (Next Milestone)"
      - "Net If Long-Term (5+ Years)"
    liquidity_contract:
      - "Est. Net Liquidity is sellable-only"
      - "exclude locked and forfeited/non-realizable shares from liquidity totals"
      - "show excluded value separately as blocked/restricted"

roadmap:
  - id: "ET20-EPIC-01"
    name: "Tax-Year Realization Planner"
    category: "profit_lever_tax"
    problem: "CGT impact depends on tax-year position; current UI lacks a tax-year control panel."
    user_value: "Shows remaining allowance and marginal net outcomes for selling now vs after tax-year boundary."
    key_outputs:
      - "remaining annual exempt amount (current tax year)"
      - "per-lot projected gain/tax if sold today"
      - "cross-year comparison: sell_before_year_end vs sell_after_year_start"
      - "transparent breakdown: price, cost basis, rates, allowance usage"
    ui_surfaces:
      - "/tax-plan page (new)"
      - "portfolio: optional column 'marginal tax if sold today'"
    data_requirements:
      - "existing lots + existing prices + existing settings + existing reports"
    dependencies:
      - "tax-parameter display layer (show which tax-year table was used)"
    complexity: "M"
    risks:
      - "user tax situation complexity varies; must be explicit about assumptions"
    acceptance_criteria:
      - "planner runs with missing prices and clearly flags gaps"
      - "planner displays tax-year boundary date and does not assume today is near year end"
      - "all calculations are auditable (inputs/outputs logged or reproducible)"

  - id: "ET20-EPIC-02"
    name: "Dividend Net-Return and Tax Drag Dashboard"
    category: "profit_lever_cashflow"
    problem: "Dividends contribute to total return; taxable dividend drag is not visible."
    user_value: "Shows after-tax dividend cashflow and net yield by holding type."
    key_outputs:
      - "trailing 12-month dividends (per security, portfolio)"
      - "forecasted dividends (manual input v1; automated later)"
      - "estimated dividend tax (per tax year) given settings"
      - "net dividend yield in GBP"
    ui_surfaces:
      - "/dividends (new)"
      - "portfolio: optional 'net yield' badge"
    data_requirements:
      - "dividend transactions OR manual dividend entries"
      - "tax parameters (dividend allowance + rates by tax year)"
    complexity: "M"
    risks:
      - "dividend forecasting can be inaccurate; keep it optional & labeled"
    acceptance_criteria:
      - "clearly separates ISA vs taxable dividend treatment"
      - "tax-year aware reporting (e.g., pre/post April changes)"

  - id: "ET20-EPIC-03"
    name: "Concentration and Liquidity Risk Panel"
    category: "profit_lever_risk"
    problem: "Portfolio can be overexposed to one security and to constrained lots; current view is granular but not summarizing risk."
    user_value: "Makes exposure, liquidity, and constraints visible at a glance."
    key_outputs:
      - "top holding concentration (% of net liquidation value)"
      - "scheme concentration (RSU/ESPP/ESPP+/ISA/brokerage)"
      - "locked vs sellable vs at-risk proportions"
      - "simple stress tests (net value under price shocks)"
    ui_surfaces:
      - "/risk (new) or portfolio summary module"
    data_requirements:
      - "existing lot summaries + current prices"
    complexity: "S_to_M"
    risks:
      - "risk metrics can be misunderstood; keep defaults simple and explain terms"
    acceptance_criteria:
      - "all charts/totals reconcile to portfolio totals"
      - "stress tests labeled as hypothetical (not prediction)"

  - id: "ET20-EPIC-04"
    name: "Vest, Forfeiture, and Tax Calendar with Alerts"
    category: "profit_lever_timing"
    problem: "Value-impacting events are time-based; easy to miss without a calendar."
    user_value: "Reduces avoidable mistakes (missing forfeiture end dates, overlooking tax-year end)."
    key_outputs:
      - "timeline of upcoming events with value-at-stake"
      - "countdowns (forfeiture end, vest dates, tax-year end)"
      - "optional alerts (local/email)"
    ui_surfaces:
      - "/calendar (new)"
      - "portfolio: 'next event' badges"
    data_requirements:
      - "existing lot dates; optional grant vest schedules"
    complexity: "S_to_M"
    risks:
      - "notification delivery complexity; ship timeline first, alerts second"
    acceptance_criteria:
      - "calendar works without any external APIs"
      - "alerts are opt-in and configurable"

  - id: "ET20-EPIC-05"
    name: "Scenario Lab for Multi-Lot Decisions"
    category: "profit_lever_simulation"
    problem: "Real decisions involve multiple lots/securities and tax-year splits; current simulator is single-lot."
    user_value: "Compares scenarios side-by-side (cash raised, taxes, remaining exposure)."
    key_outputs:
      - "scenario builder (multi-security quantities)"
      - "side-by-side scenario comparison"
      - "sensitivity sliders (price +/-) for robustness"
    ui_surfaces:
      - "/simulate (upgrade)"
    data_requirements:
      - "existing FIFO engine + tax engine + current prices"
    dependencies:
      - "Tax-Year Realization Planner (for cross-year comparisons)"
    complexity: "M_to_L"
    risks:
      - "UI complexity; must preserve current single-lot flow"
    acceptance_criteria:
      - "scenario results reconcile to per-lot/tax reports"
      - "scenario can be exported (JSON/CSV) for auditability"

  - id: "ET20-EPIC-06"
    name: "Data Reliability and Multi-Currency Foundation"
    category: "platform_enabler"
    problem: "Profit planning depends on trustworthy prices/FX; current approach can be stale or quota-bound."
    user_value: "Higher confidence numbers; better freshness diagnostics; scalable refresh."
    key_outputs:
      - "explicit staleness thresholds and freshness indicators"
      - "quota-aware refresh strategy"
      - "FX provider option (daily cached)"
      - "price fallback option with daily budget display"
      - "multi-currency FX path design (beyond USD->GBP)"
    ui_surfaces:
      - "settings: data sources and staleness thresholds"
      - "portfolio: freshness badges + last-updated"
    complexity: "M"
    risks:
      - "API limits require careful caching and user messaging"
    acceptance_criteria:
      - "never silently uses stale data without flagging it"
      - "all fetch failures degrade gracefully to cached values with warnings"

  - id: "ET20-EPIC-07"
    name: "Portfolio and Per-Scheme Enhancements"
    category: "ux_quality"
    problem: "Portfolio and Per Scheme pages have growing holdings but no row filtering, sorting, or formula visibility; repeated workflows are inefficient."
    user_value: "Reduces time-to-decision on large holdings through filters, sort controls, and at-a-glance formula breakdowns."
    key_outputs:
      - "quick row filters (All / Warnings / Locked / Forfeiture Risk)"
      - "optional sort controls for scenario columns (Net/Gain If Sold, If Held, If Long-Term)"
      - "compact hover/expand formula breakdown per decision cell"
      - "persistent table preferences (filter state + column visibility) via localStorage"
      - "one-click focus mode for decision columns on narrow screens"
      - "Per Scheme: per-scheme show/hide toggle"
    ui_surfaces:
      - "portfolio.html (existing page — view-layer additions only)"
      - "per_scheme.html (existing page — view-layer additions only)"
    data_requirements:
      - "no new data — operates on data already in template context"
    complexity: "S_to_M"
    risks:
      - "filter/sort state must not alter backend data or calculation semantics"
    acceptance_criteria:
      - "all filter/sort/preference changes are client-side only (no new API calls for state)"
      - "default sort and filter state matches current behavior exactly (no regression)"
      - "full regression passes after every change to portfolio.html or per_scheme.html"

  - id: "ET20-EPIC-08"
    name: "Analytics Dashboard with Configurable Graphs"
    category: "decision_visibility"
    problem: "All decision data is tabular; patterns across time, scheme concentration, and tax-year position require manual mental synthesis."
    user_value: "Makes portfolio structure, risk concentration, and tax position visually scannable without replacing the authoritative tabular views."
    chart_library: "Chart.js (CDN, no build tools required)"
    chart_library_rationale:
      - "no build toolchain required — single script tag"
      - "dark theme config via Chart.defaults maps to existing navy/teal/red palette"
      - "all required types: line, bar, donut, horizontal bar, stacked bar"
      - "vanilla JS compatible — matches existing codebase approach"
    key_outputs:
      - "portfolio value over time (line — price_history x lot quantities)"
      - "value by scheme (donut — RSU/ESPP/ESPP+/BROKERAGE/ISA)"
      - "top holdings concentration (horizontal bar — top N by % of market value)"
      - "sellable vs locked vs at-risk (stacked donut — sellability breakdown)"
      - "unrealised P&L by security (grouped bar — cost basis vs true cost vs market value)"
      - "tax-year CGT position (bar + line overlay — AEA vs realized gains)"
      - "stress test chart (bar — net liquidation at price shock scenarios)"
      - "forfeiture-at-risk value (donut — ESPP+ matched share at-risk amount)"
      - "upcoming events timeline (Gantt-style — vest, forfeiture, tax-year boundary)"
    widget_configurability:
      - "all charts are individually toggleable on/off"
      - "widget visibility persists in localStorage (analytics.widget_visibility.v1)"
      - "every chart has a plain-text table fallback (Show table toggle)"
      - "charts requiring EPIC-03/EPIC-04 data render as Coming soon placeholders until those EPICs ship"
    ui_surfaces:
      - "/analytics (new page)"
    data_requirements:
      - "price_history table (already exists — daily GBP close per security)"
      - "portfolio_service.get_portfolio_summary() (read-only)"
      - "report_service.cgt_summary() (read-only)"
      - "risk_service (EPIC-03 prerequisite for stress/forfeiture charts)"
      - "calendar_service (EPIC-04 prerequisite for timeline chart)"
    readability_contract:
      - "every chart has a title and one-line explanation subtitle"
      - "every chart using price data shows a freshness indicator"
      - "empty or unavailable states show explicit reason text, never blank/broken canvas"
      - "chart colors supplement pattern fills for colorblind accessibility"
      - "hide_values mode (CF-04) suppresses monetary chart data and renders placeholder"
    complexity: "M"
    risks:
      - "portfolio-over-time aggregation can be slow for large price_history tables; cache or paginate"
      - "chart rendering must degrade gracefully when price data is partial or absent"
    acceptance_criteria:
      - "all chart totals reconcile to existing portfolio/report totals"
      - "every chart renders a meaningful empty state (not broken) with no price data"
      - "full regression passes before and after Chart.js CDN tag is added to base.html"
      - "analytics page loads without error when portfolio is empty"
      - "widget toggle state persists across page reloads"
