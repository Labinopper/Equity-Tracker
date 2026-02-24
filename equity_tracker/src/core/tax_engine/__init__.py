"""
Tax Engine — UK tax calculation module.

Public interface summary:

    from core.tax_engine import (
        get_bands, TaxYearBands,
        TaxContext,
        MarginalRates, get_marginal_rates,
        personal_allowance, income_tax_liability, marginal_income_tax_rate,
        ni_liability, marginal_ni_rate,
        student_loan_repayment,
        calculate_cgt, CgtResult,
        process_sip_event, SIPEvent, SIPHolding, SIPShareType, SIPEventType,
        simulate_employer_leaver,
        rsu_true_cost, sip_partnership_true_cost, espp_true_cost, TrueCostResult,
    )

All monetary values are Decimal. All functions are pure (no I/O, no side effects).
"""

from .bands import TaxYearBands, available_tax_years, get_bands, tax_year_for_date
from .capital_gains import CgtResult, calculate_cgt, marginal_cgt_rate
from .context import TaxContext
from .dividend_tax import (
    DividendTaxBands,
    DividendTaxResult,
    calculate_dividend_tax,
    get_dividend_tax_bands,
)
from .income_tax import (
    income_tax_liability,
    income_tax_on_additional_income,
    marginal_income_tax_rate,
    personal_allowance,
)
from .marginal_rates import MarginalRates, get_marginal_rates
from .national_insurance import marginal_ni_rate, ni_liability, ni_on_additional_income
from .sip_rules import (
    EmployerLeaverSIPOutcome,
    SIPEvent,
    SIPEventType,
    SIPHolding,
    SIPHoldingPeriodCategory,
    SIPShareType,
    SIPTaxResult,
    process_sip_event,
    simulate_employer_leaver,
)
from .student_loan import marginal_student_loan_rate, sl_on_additional_income, student_loan_repayment
from .true_cost import (
    TrueCostResult,
    brokerage_true_cost,
    espp_plus_matching_true_cost,
    espp_true_cost,
    rsu_true_cost,
    sip_dividend_true_cost,
    sip_matching_true_cost,
    sip_partnership_true_cost,
)

__all__ = [
    # Bands
    "TaxYearBands", "get_bands", "available_tax_years", "tax_year_for_date",
    # Context
    "TaxContext",
    # Income Tax
    "personal_allowance", "income_tax_liability", "marginal_income_tax_rate",
    "income_tax_on_additional_income",
    # NI
    "ni_liability", "marginal_ni_rate", "ni_on_additional_income",
    # Student Loan
    "student_loan_repayment", "marginal_student_loan_rate", "sl_on_additional_income",
    # CGT
    "CgtResult", "calculate_cgt", "marginal_cgt_rate",
    # Dividend tax
    "DividendTaxBands", "DividendTaxResult",
    "get_dividend_tax_bands", "calculate_dividend_tax",
    # Marginal rates
    "MarginalRates", "get_marginal_rates",
    # SIP rules
    "SIPShareType", "SIPEventType", "SIPHoldingPeriodCategory",
    "SIPHolding", "SIPEvent", "SIPTaxResult", "EmployerLeaverSIPOutcome",
    "process_sip_event", "simulate_employer_leaver",
    # True cost
    "TrueCostResult", "rsu_true_cost", "sip_partnership_true_cost",
    "sip_matching_true_cost", "sip_dividend_true_cost",
    "espp_true_cost", "espp_plus_matching_true_cost", "brokerage_true_cost",
]
