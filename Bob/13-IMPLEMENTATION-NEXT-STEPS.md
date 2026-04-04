# Beta System: Implementation Next Steps

**Created:** April 3, 2026  
**Status:** Ready for Implementation  
**Priority:** HIGH

---

## Overview

This document provides concrete next steps for implementing the Beta system enhancements outlined in the [Implementation Plan](12-BETA-IMPLEMENTATION-PLAN.md). Based on the comprehensive project review, we have identified 8 critical gaps and created a 24-week roadmap. This document focuses on the immediate actions needed to begin Phase 1.

---

## Current State Summary

**From April 3, 2026 Benchmark:**
- Validation Accuracy: 48.9% (worse than random)
- Activated Models: 0 out of 109 trained
- Validated Hypotheses: 0 (32 rejected, 2 degraded)
- Average Return: +0.08% (essentially zero edge)

**Conclusion:** System is operationally functional but has NOT demonstrated profitable trading capability.

---

## Phase 1: Foundation & Validation (Weeks 1-8)

### Week 1-2: Prediction Accuracy Tracking

#### Step 1: Create Database Migration

**File:** `equity_tracker/alembic/versions/018_prediction_accuracy_tracking.py`

**New Tables:**
1. `beta_prediction_accuracy_log` - Log every prediction with confidence
2. `beta_calibration_metrics` - Track accuracy by confidence band

**Migration Code Structure:**
```python
"""Prediction accuracy tracking

Revision ID: 018
Revises: 017
Create Date: 2026-04-03

"""
from alembic import op
import sqlalchemy as sa

def upgrade():
    # Create beta_prediction_accuracy_log table
    op.create_table(
        'beta_prediction_accuracy_log',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('hypothesis_definition_id', sa.String(36), nullable=False),
        sa.Column('signal_observation_id', sa.String(36), nullable=True),
        sa.Column('execution_signal_id', sa.String(36), nullable=True),
        sa.Column('predicted_return_pct', sa.Float, nullable=False),
        sa.Column('realized_return_pct', sa.Float, nullable=True),
        sa.Column('prediction_error_pct', sa.Float, nullable=True),
        sa.Column('directional_match', sa.Integer, nullable=True),
        sa.Column('confidence_score', sa.Float, nullable=False),
        sa.Column('confidence_band', sa.String(20), nullable=False),
        sa.Column('prediction_time', sa.DateTime, nullable=False),
        sa.Column('realization_time', sa.DateTime, nullable=True),
        sa.Column('horizon_days', sa.Integer, nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.ForeignKeyConstraint(['hypothesis_definition_id'], ['beta_hypothesis_definitions.id']),
    )
    
    # Create indexes
    op.create_index('idx_prediction_accuracy_hypothesis', 'beta_prediction_accuracy_log', ['hypothesis_definition_id'])
    op.create_index('idx_prediction_accuracy_confidence', 'beta_prediction_accuracy_log', ['confidence_band'])
    op.create_index('idx_prediction_accuracy_time', 'beta_prediction_accuracy_log', ['prediction_time'])
    
    # Create beta_calibration_metrics table
    op.create_table(
        'beta_calibration_metrics',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('hypothesis_definition_id', sa.String(36), nullable=True),
        sa.Column('confidence_band', sa.String(20), nullable=False),
        sa.Column('evaluation_period_start', sa.Date, nullable=False),
        sa.Column('evaluation_period_end', sa.Date, nullable=False),
        sa.Column('prediction_count', sa.Integer, nullable=False),
        sa.Column('realized_count', sa.Integer, nullable=False),
        sa.Column('mean_predicted_return_pct', sa.Float, nullable=False),
        sa.Column('mean_realized_return_pct', sa.Float, nullable=False),
        sa.Column('mean_absolute_error_pct', sa.Float, nullable=False),
        sa.Column('root_mean_squared_error_pct', sa.Float, nullable=False),
        sa.Column('directional_accuracy_pct', sa.Float, nullable=False),
        sa.Column('win_rate_pct', sa.Float, nullable=False),
        sa.Column('calibration_error_pct', sa.Float, nullable=False),
        sa.Column('sharpe_ratio', sa.Float, nullable=True),
        sa.Column('information_ratio', sa.Float, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.ForeignKeyConstraint(['hypothesis_definition_id'], ['beta_hypothesis_definitions.id']),
    )
    
    op.create_index('idx_calibration_hypothesis', 'beta_calibration_metrics', ['hypothesis_definition_id'])
    op.create_index('idx_calibration_period', 'beta_calibration_metrics', ['evaluation_period_end'])

def downgrade():
    op.drop_table('beta_calibration_metrics')
    op.drop_table('beta_prediction_accuracy_log')
```

#### Step 2: Add Models to models.py

**File:** `equity_tracker/src/beta/db/models.py`

**Add after existing models:**
```python
class BetaPredictionAccuracyLog(BetaBase):
    __tablename__ = "beta_prediction_accuracy_log"
    __table_args__ = (
        Index("idx_prediction_accuracy_hypothesis", "hypothesis_definition_id"),
        Index("idx_prediction_accuracy_confidence", "confidence_band"),
        Index("idx_prediction_accuracy_time", "prediction_time"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    hypothesis_definition_id: Mapped[str] = mapped_column(
        ForeignKey("beta_hypothesis_definitions.id"),
        nullable=False,
    )
    signal_observation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    execution_signal_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    predicted_return_pct: Mapped[float] = mapped_column(Float, nullable=False)
    realized_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    prediction_error_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    directional_match: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False)
    confidence_band: Mapped[str] = mapped_column(String(20), nullable=False)
    prediction_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    realization_time: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaCalibrationMetrics(BetaBase):
    __tablename__ = "beta_calibration_metrics"
    __table_args__ = (
        Index("idx_calibration_hypothesis", "hypothesis_definition_id"),
        Index("idx_calibration_period", "evaluation_period_end"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    hypothesis_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_hypothesis_definitions.id"),
        nullable=True,
    )
    confidence_band: Mapped[str] = mapped_column(String(20), nullable=False)
    evaluation_period_start: Mapped[date] = mapped_column(Date, nullable=False)
    evaluation_period_end: Mapped[date] = mapped_column(Date, nullable=False)
    prediction_count: Mapped[int] = mapped_column(Integer, nullable=False)
    realized_count: Mapped[int] = mapped_column(Integer, nullable=False)
    mean_predicted_return_pct: Mapped[float] = mapped_column(Float, nullable=False)
    mean_realized_return_pct: Mapped[float] = mapped_column(Float, nullable=False)
    mean_absolute_error_pct: Mapped[float] = mapped_column(Float, nullable=False)
    root_mean_squared_error_pct: Mapped[float] = mapped_column(Float, nullable=False)
    directional_accuracy_pct: Mapped[float] = mapped_column(Float, nullable=False)
    win_rate_pct: Mapped[float] = mapped_column(Float, nullable=False)
    calibration_error_pct: Mapped[float] = mapped_column(Float, nullable=False)
    sharpe_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    information_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
```

#### Step 3: Create Prediction Accuracy Service

**File:** `equity_tracker/src/beta/services/prediction_accuracy_service.py` (NEW)

**Key Methods:**
- `log_prediction()` - Record prediction when signal fires
- `update_realized_outcome()` - Update with actual result
- `compute_calibration_metrics()` - Calculate MAE, RMSE, directional accuracy
- `get_hypothesis_accuracy()` - Get accuracy metrics for specific hypothesis
- `get_overall_accuracy()` - Get system-wide accuracy metrics

**See Implementation Plan document for full code.**

#### Step 4: Integration Points

**Files to Modify:**

1. **`equity_tracker/src/beta/services/hypothesis_belief_service.py`**
   - Import `prediction_accuracy_service`
   - In method that generates signals, call `log_prediction()`
   - Example location: After computing expected return, before returning signal

2. **`equity_tracker/src/beta/services/execution_outcome_service.py`**
   - Import `prediction_accuracy_service`
   - In method that computes realized outcomes, call `update_realized_outcome()`
   - Example location: After computing actual return from labels

3. **`equity_tracker/src/beta/runtime_manager.py`**
   - Add daily job to compute calibration metrics
   - Call `compute_calibration_metrics()` for last 30/60/90 days
   - Store results in database

#### Step 5: Testing

**File:** `equity_tracker/tests/test_services/test_prediction_accuracy_service.py` (NEW)

**Test Cases:**
- Test prediction logging
- Test outcome updates
- Test calibration metrics computation
- Test accuracy by confidence band
- Test MAE/RMSE calculations
- Test directional accuracy
- Test Sharpe ratio calculation

---

### Week 3-4: Data Quality Monitoring

#### Implementation Steps

1. **Create Migration:** `019_data_quality_monitoring.py`
   - `beta_data_quality_snapshots` table
   - `beta_feature_quality_log` table

2. **Add Models:** Update `models.py`
   - `BetaDataQualitySnapshot`
   - `BetaFeatureQualityLog`

3. **Create Service:** `data_quality_service.py`
   - `compute_quality_snapshot()` - Hourly quality assessment
   - `validate_signal_data_quality()` - Pre-signal validation gate
   - `get_quality_trends()` - Historical quality trends

4. **Integration:**
   - `runtime_manager.py` - Add hourly quality snapshot job
   - `execution_signal_service.py` - Validate quality before generating signals
   - Block signals if quality < 95%

5. **Testing:** `test_data_quality_service.py`

---

### Week 5-6: Signal Explainability

#### Implementation Steps

1. **Create Migration:** `020_signal_explainability.py`
   - `beta_signal_feature_contributions` table
   - `beta_signal_historical_analogs` table

2. **Add Models:** Update `models.py`
   - `BetaSignalFeatureContribution`
   - `BetaSignalHistoricalAnalog`

3. **Create Service:** `signal_explainability_service.py`
   - `explain_signal()` - Generate comprehensive explanation
   - `_analyze_feature_contributions()` - Rank features by contribution
   - `_find_historical_analogs()` - Find similar past signals

4. **Integration:**
   - `execution_signal_service.py` - Call explainability service when generating signals
   - `hypothesis_belief_service.py` - Include explainability in belief assessment

5. **Testing:** `test_signal_explainability_service.py`

---

### Week 7-8: Hypothesis Lifecycle Management

#### Implementation Steps

1. **Create Migration:** `021_hypothesis_lifecycle.py`
   - `beta_hypothesis_lifecycle_events` table
   - `beta_hypothesis_performance_history` table

2. **Add Models:** Update `models.py`
   - `BetaHypothesisLifecycleEvent`
   - `BetaHypothesisPerformanceHistory`

3. **Create Service:** `hypothesis_lifecycle_service.py`
   - `evaluate_lifecycle_actions()` - Daily evaluation
   - `_should_retire()` - Check retirement criteria
   - `_should_prune()` - Check pruning criteria
   - `_retire_hypothesis()` - Retire failed hypothesis
   - `_prune_hypothesis()` - Remove from bottom 20%

4. **Integration:**
   - `runtime_manager.py` - Add daily lifecycle evaluation job
   - Automatic retirement after 5 consecutive losses
   - Automatic pruning of bottom 20% monthly

5. **Testing:** `test_hypothesis_lifecycle_service.py`

---

## Decision Gates

### After Week 8 (Phase 1 Complete)

**Evaluate:**
- Prediction accuracy > 52%? (better than random)
- Calibration error < 20%?
- Data quality > 95%?

**Decision:**
- ✅ All criteria met → Proceed to Phase 2
- ⚠️ Some criteria met → Fix issues, re-evaluate in 2 weeks
- ❌ No criteria met → Consider shutting down Beta

---

## Development Workflow

### For Each Feature

1. **Create Feature Branch**
   ```bash
   git checkout -b feature/beta-phase1-prediction-accuracy
   ```

2. **Implement Database Changes**
   - Create Alembic migration
   - Add models to `models.py`
   - Test migration up/down

3. **Implement Service**
   - Create new service file
   - Implement core methods
   - Add type hints and docstrings

4. **Integration**
   - Wire into existing services
   - Update runtime manager if needed
   - Add configuration if needed

5. **Testing**
   - Write unit tests
   - Write integration tests
   - Manual validation

6. **Documentation**
   - Update API docs
   - Update user guides
   - Add inline comments

7. **Code Review**
   - Self-review checklist
   - Peer review
   - Address feedback

8. **Deployment**
   - Deploy to staging
   - Run migration
   - Validate functionality
   - Deploy to production

---

## Testing Strategy

### Unit Tests
- Test each service method in isolation
- Mock database and external dependencies
- Aim for 80%+ code coverage
- Use pytest fixtures for test data

### Integration Tests
- Test service interactions
- Use test database with realistic data
- Validate end-to-end workflows
- Test error handling

### Manual Validation
- Generate test signals and verify logging
- Check calibration metrics computation
- Validate data quality snapshots
- Review lifecycle events

---

## Monitoring and Alerts

### Metrics to Track

**Prediction Accuracy:**
- Overall accuracy (daily)
- Accuracy by confidence band (daily)
- MAE, RMSE trends (weekly)
- Calibration error (daily)

**Data Quality:**
- Feature coverage % (hourly)
- Stale feature count (hourly)
- Missing feature count (hourly)
- Quality score (hourly)

**Hypothesis Lifecycle:**
- Active hypothesis count (daily)
- Retired hypothesis count (daily)
- Pruned hypothesis count (monthly)
- Status distribution (daily)

### Alerts

**Critical:**
- Prediction accuracy < 50% for 3 consecutive days
- Data quality < 90% for 6 consecutive hours
- Calibration error > 30%

**Warning:**
- Prediction accuracy < 52% for 1 day
- Data quality < 95% for 1 hour
- No new validated hypotheses in 30 days

---

## Rollback Plan

### If Implementation Fails

1. **Revert Database Migrations**
   ```bash
   alembic downgrade -1
   ```

2. **Disable New Services**
   - Comment out service imports
   - Remove from runtime manager
   - Restore previous behavior

3. **Document Lessons Learned**
   - What went wrong?
   - What would we do differently?
   - What did we learn?

### If Phase 1 Results Are Poor

**After Week 8, if prediction accuracy < 52%:**

**Option 1: Pivot Strategy**
- Focus on simpler signals
- Expand to different markets
- Use different features

**Option 2: Shut Down Beta**
- Document findings
- Archive code
- Focus resources on core system

**Option 3: Continue Research**
- Extend Phase 1 by 4 weeks
- Deep-dive into why accuracy is poor
- Try alternative approaches

---

## Success Criteria

### Week 8 Deliverables

- ✅ All predictions logged with confidence scores
- ✅ Realized outcomes tracked systematically
- ✅ Calibration metrics computed daily
- ✅ Data quality monitored hourly
- ✅ Signal explanations generated
- ✅ Hypothesis lifecycle management active
- ✅ Clear evidence whether system has predictive capability

### Key Questions Answered

1. **Does the system have any edge?** Prediction accuracy > 52%?
2. **Are predictions well-calibrated?** Do 70% confidence signals win 70%?
3. **Is data quality sufficient?** Coverage > 95%, staleness < 48h?
4. **Are hypotheses being managed?** Automatic retirement and pruning working?

---

## Next Actions

### This Week

1. **Review and Approve Plan** - Stakeholder sign-off
2. **Set Up Development Environment** - Ensure all developers have access
3. **Create Phase 1 Branch** - `feature/beta-phase1-foundation`
4. **Start Week 1-2 Implementation** - Prediction accuracy tracking

### Week 1 Tasks

- [ ] Create migration `018_prediction_accuracy_tracking.py`
- [ ] Add models to `models.py`
- [ ] Implement `prediction_accuracy_service.py`
- [ ] Integrate with `hypothesis_belief_service.py`
- [ ] Integrate with `execution_outcome_service.py`
- [ ] Add calibration job to `runtime_manager.py`
- [ ] Write unit tests
- [ ] Manual validation
- [ ] Code review
- [ ] Deploy to staging

---

## Conclusion

This document provides a concrete roadmap for implementing Phase 1 of the Beta enhancement plan. The focus is on building a validation framework to prove or disprove whether the system has any predictive capability.

**Key Principle:** Measure everything, fail fast, make decisions based on evidence.

**Timeline:** 8 weeks to Phase 1 completion and first decision gate.

**Expected Outcome:** By Week 8, we'll know definitively whether the Beta system has any edge, or if we should shut it down and focus resources elsewhere.

---

**For detailed code examples and full service implementations, see [Beta Implementation Plan](12-BETA-IMPLEMENTATION-PLAN.md).**