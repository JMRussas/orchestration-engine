#  Orchestration Engine - Migration 015
#
#  Add plan_xml column to plans table for XML plan storage.
#  Existing plans remain in plan_json; new plans write both columns.
#
#  Depends on: 014_add_api_keys_and_claim_tracking
#  Used by:    services/planner.py, services/decomposer.py

"""Add plan_xml column to plans table.

Revision ID: 015
Revises: 014
Create Date: 2026-03-06
"""

from alembic import op
import sqlalchemy as sa

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("plans") as batch_op:
        batch_op.add_column(sa.Column("plan_xml", sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table("plans") as batch_op:
        batch_op.drop_column("plan_xml")
