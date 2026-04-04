# Tax Engine Documentation

**Last Updated:** 2026-04-03

## Table of Contents

1. [Overview](#overview)
2. [UK Tax System](#uk-tax-system)
3. [Tax Calculation Modules](#tax-calculation-modules)
4. [Lot Matching Algorithms](#lot-matching-algorithms)
5. [Employment Tax](#employment-tax)
6. [Capital Gains Tax](#capital-gains-tax)
7. [Tax Year Handling](#tax-year-handling)

## Overview

The Tax Engine implements **UK-specific tax calculations** for equity compensation and investment portfolios. It provides deterministic, auditable tax computations following HMRC rules.

### Key Principles

- **HMRC Compliance**: Follows UK tax regulations
- **Deterministic**: Same inputs always produce same outputs
- **Transparent**: Clear calculation steps and assumptions
- **Conservative**: Errs on side of caution for estimates

### Tax Engine Location

```
equity_tracker/src/core/
├── lot_engine/
│   ├── fifo.py              # FIFO lot allocation
│   └── uk_matching.py       # HMRC 30-day matching
└── tax_engine/
    ├── income_tax.py        # Income tax calculations
    ├── national_insurance.py # NI contributions
    ├── capital_gains.py     # CGT calculations
    ├── student_loan.py      # Student loan repayments
    ├── employment_tax.py    # Employment tax events
    ├── bands.py             # Tax bands and thresholds
    ├── marginal_rates.py    # Marginal rate calculations
    └── sip_rules.py         # SIP-specific rules
```

## UK Tax System

### Tax Year

**UK Tax Year**: April 6 to April 5 (next year)

```python
# Tax year 2024-25
start_date = date(2024, 4, 6)
end_date = date(2025, 4, 5)
```

**Tax Year String Format**: `"2024-25"`

### Tax Components

1. **Income Tax (IT)**: Tax on employment income
2. **National Insurance (NI)**: Social security contributions
3. **Student Loan (SL)**: Income-contingent repayments
4. **Capital Gains Tax (CGT)**: Tax on investment gains

### Tax Bands (2024-25)

#### Income Tax

| Band | Threshold | Rate |
|------|-----------|------|
| Personal Allowance | £0 - £12,570 | 0% |
| Basic Rate | £12,571 - £50,270 | 20% |
| Higher Rate | £50,271 - £125,140 | 40% |
| Additional Rate | £125,140+ | 45% |

**Personal Allowance Taper:**
- Reduces by £1 for every £2 over £100,000
- Fully withdrawn at £125,140

#### National Insurance (Class 1 Employee)

| Band | Threshold | Rate |
|------|-----------|------|
| Below Primary Threshold | £0 - £12,570 | 0% |
| Main Rate | £12,571 - £50,270 | 12% |
| Additional Rate | £50,270+ | 2% |

#### Capital Gains Tax

| Band | Rate (General) | Rate (Residential Property) |
|------|----------------|----------------------------|
| Basic Rate | 10% | 18% |
| Higher/Additional Rate | 20% | 28% |

**Annual Exempt Amount**: £3,000 (2024-25)

## Tax Calculation Modules

### Income Tax Module

**Location**: [`src/core/tax_engine/income_tax.py`](../equity_tracker/src/core/tax_engine/income_tax.py:1)

**Key Functions:**

```python
def calculate_income_tax(
    gross_income: Decimal,
    pension_sacrifice: Decimal = Decimal("0"),
    other_income: Decimal = Decimal("0"),
    tax_year: str = "2024-25"
) -> dict:
    """
    Calculate income tax liability.
    
    Args:
        gross_income: Employment income before tax
        pension_sacrifice: Pension contributions (reduces taxable income)
        other_income: Other taxable income (dividends, interest, etc.)
        tax_year: UK tax year string
        
    Returns:
        {
            "taxable_income": Decimal,
            "personal_allowance": Decimal,
            "tax_due": Decimal,
            "effective_rate": Decimal,
            "marginal_rate": Decimal,
            "bands": [
                {"band": "basic", "amount": Decimal, "tax": Decimal},
                ...
            ]
        }
    """
```

**Personal Allowance Calculation:**

```python
def calculate_personal_allowance(
    adjusted_net_income: Decimal,
    tax_year: str
) -> Decimal:
    """
    Calculate personal allowance with taper.
    
    ANI = Gross Income - Pension Contributions
    
    Taper:
    - Full allowance if ANI ≤ £100,000
    - Reduces £1 per £2 over £100,000
    - Zero if ANI ≥ £125,140
    """
    standard_allowance = Decimal("12570")  # 2024-25
    taper_threshold = Decimal("100000")
    
    if adjusted_net_income <= taper_threshold:
        return standard_allowance
    
    excess = adjusted_net_income - taper_threshold
    reduction = excess / Decimal("2")
    
    return max(Decimal("0"), standard_allowance - reduction)
```

### National Insurance Module

**Location**: [`src/core/tax_engine/national_insurance.py`](../equity_tracker/src/core/tax_engine/national_insurance.py:1)

**Key Functions:**

```python
def calculate_national_insurance(
    gross_income: Decimal,
    pension_sacrifice: Decimal = Decimal("0"),
    tax_year: str = "2024-25"
) -> dict:
    """
    Calculate Class 1 Employee NI contributions.
    
    Note: Pension sacrifice reduces NI liability
    
    Returns:
        {
            "ni_due": Decimal,
            "effective_rate": Decimal,
            "bands": [
                {"band": "main", "amount": Decimal, "ni": Decimal},
                {"band": "additional", "amount": Decimal, "ni": Decimal}
            ]
        }
    """
```

### Student Loan Module

**Location**: [`src/core/tax_engine/student_loan.py`](../equity_tracker/src/core/tax_engine/student_loan.py:1)

**Plans:**

| Plan | Threshold (2024-25) | Rate |
|------|---------------------|------|
| Plan 1 | £24,990 | 9% |
| Plan 2 | £27,295 | 9% |
| Plan 4 | £31,395 | 9% |
| Postgraduate | £21,000 | 6% |

**Key Functions:**

```python
def calculate_student_loan(
    gross_income: Decimal,
    pension_sacrifice: Decimal,
    plan: int | None,  # 1, 2, or None
    tax_year: str = "2024-25"
) -> Decimal:
    """
    Calculate student loan repayment.
    
    Repayment = max(0, (Income - Threshold) * Rate)
    
    Note: Pension sacrifice reduces repayment liability
    """
```

### Capital Gains Tax Module

**Location**: [`src/core/tax_engine/capital_gains.py`](../equity_tracker/src/core/tax_engine/capital_gains.py:1)

**Key Functions:**

```python
def calculate_cgt(
    capital_gains: Decimal,
    taxable_income: Decimal,  # From income tax calculation
    annual_exempt_amount: Decimal = Decimal("3000"),
    tax_year: str = "2024-25"
) -> dict:
    """
    Calculate Capital Gains Tax.
    
    Process:
    1. Apply annual exempt amount (£3,000)
    2. Determine tax band (basic vs higher/additional)
    3. Apply appropriate CGT rate (10% or 20%)
    
    Returns:
        {
            "taxable_gains": Decimal,
            "exempt_amount_used": Decimal,
            "cgt_due": Decimal,
            "effective_rate": Decimal,
            "bands": [
                {"band": "basic", "gains": Decimal, "cgt": Decimal},
                {"band": "higher", "gains": Decimal, "cgt": Decimal}
            ]
        }
    """
```

**Band Determination:**

```python
def determine_cgt_bands(
    capital_gains: Decimal,
    taxable_income: Decimal,
    basic_rate_limit: Decimal = Decimal("37700")  # 2024-25
) -> tuple[Decimal, Decimal]:
    """
    Split gains between basic and higher rate bands.
    
    Basic rate band = £50,270 - Personal Allowance (£12,570)
                    = £37,700
    
    Returns:
        (gains_at_basic_rate, gains_at_higher_rate)
    """
    # Remaining basic rate band after income
    remaining_basic = max(Decimal("0"), basic_rate_limit - taxable_income)
    
    # Gains in basic rate band
    basic_gains = min(capital_gains, remaining_basic)
    
    # Remaining gains in higher rate band
    higher_gains = max(Decimal("0"), capital_gains - basic_gains)
    
    return (basic_gains, higher_gains)
```

## Lot Matching Algorithms

### FIFO (First In, First Out)

**Location**: [`src/core/lot_engine/fifo.py`](../equity_tracker/src/core/lot_engine/fifo.py:1)

**Purpose**: Allocate disposals to lots in acquisition order.

**Algorithm:**

```python
def allocate_disposal_fifo(
    lots: list[Lot],
    disposal_quantity: Decimal
) -> list[LotAllocation]:
    """
    Allocate disposal using FIFO.
    
    Process:
    1. Sort lots by acquisition_date (oldest first)
    2. Allocate from oldest lot until exhausted
    3. Move to next lot
    4. Continue until disposal quantity satisfied
    
    Returns:
        List of (lot_id, quantity_allocated, cost_basis)
    """
    allocations = []
    remaining = disposal_quantity
    
    # Sort by acquisition date (oldest first)
    sorted_lots = sorted(lots, key=lambda l: l.acquisition_date)
    
    for lot in sorted_lots:
        if remaining <= 0:
            break
        
        available = lot.quantity_remaining
        allocated = min(remaining, available)
        
        allocations.append(LotAllocation(
            lot_id=lot.id,
            quantity=allocated,
            cost_basis=lot.cost_per_share_gbp * allocated
        ))
        
        remaining -= allocated
    
    return allocations
```

### UK Matching Rules (HMRC)

**Location**: [`src/core/lot_engine/uk_matching.py`](../equity_tracker/src/core/lot_engine/uk_matching.py:1)

**Purpose**: Implement HMRC's share matching rules for CGT.

**Matching Order:**

1. **Same Day**: Acquisitions on disposal date
2. **30-Day Rule**: Acquisitions within 30 days after disposal
3. **Section 104 Pool**: All other acquisitions (FIFO)

**Algorithm:**

```python
def allocate_disposal_uk_matching(
    lots: list[Lot],
    disposal_date: date,
    disposal_quantity: Decimal
) -> list[LotAllocation]:
    """
    Allocate disposal using HMRC matching rules.
    
    Process:
    1. Match same-day acquisitions
    2. Match 30-day acquisitions (FIFO within window)
    3. Match Section 104 pool (FIFO)
    """
    allocations = []
    remaining = disposal_quantity
    
    # 1. Same-day matching
    same_day_lots = [l for l in lots if l.acquisition_date == disposal_date]
    allocations, remaining = _match_lots(same_day_lots, remaining)
    
    if remaining <= 0:
        return allocations
    
    # 2. 30-day matching
    window_end = disposal_date + timedelta(days=30)
    thirty_day_lots = [
        l for l in lots
        if disposal_date < l.acquisition_date <= window_end
    ]
    thirty_day_lots.sort(key=lambda l: l.acquisition_date)
    allocations_30d, remaining = _match_lots(thirty_day_lots, remaining)
    allocations.extend(allocations_30d)
    
    if remaining <= 0:
        return allocations
    
    # 3. Section 104 pool (FIFO)
    pool_lots = [
        l for l in lots
        if l.acquisition_date < disposal_date
    ]
    pool_lots.sort(key=lambda l: l.acquisition_date)
    allocations_pool, remaining = _match_lots(pool_lots, remaining)
    allocations.extend(allocations_pool)
    
    return allocations
```

**Example:**

```
Disposal: 100 shares on 2024-06-01

Lots:
- 2024-05-15: 30 shares (before disposal)
- 2024-06-01: 20 shares (same day)
- 2024-06-10: 40 shares (within 30 days)
- 2024-07-05: 50 shares (after 30-day window)

Matching:
1. Same day: 20 shares from 2024-06-01
2. 30-day: 40 shares from 2024-06-10
3. Section 104: 30 shares from 2024-05-15
4. Remaining: 10 shares unmatched (shortfall)
```

## Employment Tax

### Employment Tax Events

**Location**: [`src/core/tax_engine/employment_tax.py`](../equity_tracker/src/core/tax_engine/employment_tax.py:1)

**Taxable Events:**

1. **RSU Vesting**: Market value at vest is employment income
2. **ESPP Purchase**: Discount is employment income
3. **ESPP+ Disposal**: Gain up to forfeiture threshold is employment income
4. **SIP Forfeiture**: Forfeited matching shares trigger tax

**Key Functions:**

```python
def calculate_employment_tax_on_vest(
    market_value_gbp: Decimal,
    gross_income: Decimal,
    pension_sacrifice: Decimal,
    student_loan_plan: int | None,
    tax_year: str
) -> dict:
    """
    Calculate employment tax on RSU vest.
    
    Process:
    1. Add vest value to gross income
    2. Calculate IT, NI, SL on total income
    3. Compute marginal tax on vest value
    
    Returns:
        {
            "taxable_amount": Decimal,
            "income_tax": Decimal,
            "national_insurance": Decimal,
            "student_loan": Decimal,
            "total_tax": Decimal,
            "effective_rate": Decimal
        }
    """
```

### SIP Rules

**Location**: [`src/core/tax_engine/sip_rules.py`](../equity_tracker/src/core/tax_engine/sip_rules.py:1)

**Share Incentive Plan (SIP) Tax Treatment:**

| Share Type | Holding Period | Tax Treatment |
|------------|----------------|---------------|
| Partnership | < 5 years | Income tax + NI on withdrawal |
| Partnership | ≥ 5 years | Tax-free |
| Matching | < 3 years | Income tax + NI on withdrawal |
| Matching | ≥ 3 years | Tax-free |
| Dividend | < 3 years | Income tax on withdrawal |
| Dividend | ≥ 3 years | Tax-free |

**Key Functions:**

```python
def calculate_sip_withdrawal_tax(
    lot: Lot,
    withdrawal_date: date,
    market_value_gbp: Decimal,
    gross_income: Decimal,
    pension_sacrifice: Decimal,
    student_loan_plan: int | None
) -> dict:
    """
    Calculate tax on SIP share withdrawal.
    
    Process:
    1. Determine holding period
    2. Check if tax-free threshold met
    3. Calculate employment tax if applicable
    """
```

## Capital Gains Tax

### CGT Calculation Process

```
1. Identify Disposals
   ↓
2. Match to Lots (FIFO or UK Matching)
   ↓
3. Calculate Gain per Disposal
   Gain = Proceeds - Cost Basis - Fees
   ↓
4. Aggregate Gains/Losses
   Net Gain = Total Gains - Total Losses
   ↓
5. Apply Annual Exempt Amount (£3,000)
   Taxable Gain = max(0, Net Gain - £3,000)
   ↓
6. Determine Tax Bands
   Split between Basic (10%) and Higher (20%)
   ↓
7. Calculate CGT Due
   CGT = (Basic Gains × 10%) + (Higher Gains × 20%)
```

### Loss Carry-Forward

**Rules:**
- Losses can be carried forward indefinitely
- Must be used against gains in earliest year
- Cannot create a refund (only reduce CGT to zero)

**Implementation:**

```python
def apply_loss_carry_forward(
    current_year_gains: Decimal,
    carried_forward_losses: Decimal,
    annual_exempt_amount: Decimal
) -> dict:
    """
    Apply carried-forward losses to current year gains.
    
    Process:
    1. Apply annual exempt amount first
    2. Apply carried-forward losses to remaining gains
    3. Calculate CGT on net taxable gains
    
    Returns:
        {
            "gross_gains": Decimal,
            "exempt_amount_used": Decimal,
            "losses_used": Decimal,
            "losses_remaining": Decimal,
            "taxable_gains": Decimal,
            "cgt_due": Decimal
        }
    """
```

## Tax Year Handling

### Tax Year Boundaries

```python
def get_tax_year_dates(tax_year: str) -> tuple[date, date]:
    """
    Get start and end dates for UK tax year.
    
    Args:
        tax_year: String like "2024-25"
        
    Returns:
        (start_date, end_date)
        
    Example:
        >>> get_tax_year_dates("2024-25")
        (date(2024, 4, 6), date(2025, 4, 5))
    """
    start_year = int(tax_year.split("-")[0])
    return (
        date(start_year, 4, 6),
        date(start_year + 1, 4, 5)
    )
```

### Current Tax Year

```python
def get_current_tax_year(as_of: date | None = None) -> str:
    """
    Determine current UK tax year.
    
    Args:
        as_of: Date to check (default: today)
        
    Returns:
        Tax year string like "2024-25"
        
    Example:
        >>> get_current_tax_year(date(2024, 5, 1))
        "2024-25"
        >>> get_current_tax_year(date(2024, 3, 1))
        "2023-24"
    """
    if as_of is None:
        as_of = date.today()
    
    # Tax year starts April 6
    if as_of.month < 4 or (as_of.month == 4 and as_of.day < 6):
        # Before April 6: previous tax year
        start_year = as_of.year - 1
    else:
        # On or after April 6: current tax year
        start_year = as_of.year
    
    return f"{start_year}-{str(start_year + 1)[-2:]}"
```

## Related Documentation

- [Architecture Overview](./01-ARCHITECTURE-OVERVIEW.md) - System design
- [Service Layer](./04-SERVICE-LAYER.md) - Tax services
- [Developer Onboarding](./07-DEVELOPER-ONBOARDING.md) - Testing tax calculations