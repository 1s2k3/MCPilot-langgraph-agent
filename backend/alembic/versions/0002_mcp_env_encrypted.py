"""mcp_servers.env 加密列（安全评审发现 4：env 与 headers 对等加密）

Revision ID: 0002
Revises: 0001
Create Date: 2026-08-20

"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("mcp_servers", sa.Column("env_encrypted", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("mcp_servers", "env_encrypted")
