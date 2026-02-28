"""Backfill native currency prices for historical price records.

Revision ID: 008
Revises: 007
Create Date: 2026-02-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from decimal import Decimal

# revision identifiers, used by Alembic.
revision = '008'
down_revision = '007'
branch_labels = None
depends_on = None


def upgrade():
    """Backfill close_price_original_ccy for records where it's NULL."""
    connection = op.get_bind()
    
    # For each security with missing native currency prices
    # Try to backfill using FX rates if available
    query = """
    SELECT DISTINCT ph.security_id, s.currency
    FROM price_history ph
    JOIN securities s ON ph.security_id = s.id
    WHERE ph.close_price_original_ccy IS NULL
    AND s.currency != 'GBP'
    """
    
    results = connection.execute(sa.text(query)).fetchall()
    
    for security_id, currency in results:
        # For each security, get all price history records with NULL native prices
        # and available FX rates
        update_query = """
        UPDATE price_history
        SET close_price_original_ccy = close_price_gbp * fx_rate
        WHERE security_id = :security_id
        AND close_price_original_ccy IS NULL
        AND fx_rate IS NOT NULL
        AND fx_rate > 0
        """
        
        connection.execute(
            sa.text(update_query),
            {"security_id": security_id}
        )


def downgrade():
    """Set native currency prices back to NULL."""
    connection = op.get_bind()
    
    # Only reset prices that we backfilled (those that match the formula)
    # To be safe, we won't try to reverse this
    pass
