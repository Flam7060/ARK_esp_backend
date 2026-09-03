"""account.active_group_id -- one account, one active sharing group

Revision ID: a1b2c3d4e5f6
Revises: f1a2b3c4d5e6
Create Date: 2026-09-03 00:00:00.000000

Replaces the client-supplied group_id in the sharing wire handshake
(kopt::Publisher::start / protocol handshakeRequest): the relay now
resolves which group an account's sightings route into purely from
account_id, via this column mirrored into Redis (core/group_cache.py's
set_active_group/clear_active_group) -- the client no longer states, and
the relay no longer trusts, a client-declared group_id at all.

ON DELETE SET NULL: deleting the active group must not leave a dangling
FK -- the account simply has no live sharing target until it joins/
creates another one.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'account',
        sa.Column(
            'active_group_id', postgresql.UUID(as_uuid=True), nullable=True,
            comment="Единственная группа, куда сейчас льётся шеринг -- resolve на relay-стороне по account_id, без group_id от клиента",
        ),
    )
    op.create_foreign_key(
        'fk_account_active_group_id', 'account', 'sharing_group',
        ['active_group_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint('fk_account_active_group_id', 'account', type_='foreignkey')
    op.drop_column('account', 'active_group_id')
