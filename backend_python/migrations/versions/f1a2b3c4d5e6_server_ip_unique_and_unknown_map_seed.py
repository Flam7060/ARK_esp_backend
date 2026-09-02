"""server.port + (ip_address, port) unique + seed game_map(code='unknown')

Revision ID: f1a2b3c4d5e6
Revises: 897ac91a9d4c
Create Date: 2026-09-02 00:00:00.000000

Needed for the Go relay's structure/dino Redis Stream consumers
(services/structure_sighting_service.py, dino_sighting_service.py): they
resolve a Server row by server_ip (the relay's identity string, "ip:port"
-- see DTO-sharing plan §4), and "find or create" is only race-safe with a
real unique constraint underneath the find-then-insert, not just an
application-level check.

server.ip_address is INET, which does NOT accept a ":port" suffix (caught
by an actual end-to-end test against real Postgres, not assumed) -- port
is a new, separate column, and the identity constraint covers the pair,
not ip_address alone.

The seed row (code='unknown') is the map_code an auto-discovered server
gets before real A2S_INFO enrichment fills in the actual map -- server.
map_code is NOT NULL, so a server can't be created at all without some
row to point at.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = '897ac91a9d4c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'server',
        sa.Column(
            'port', sa.Integer(), nullable=True,
            comment="Игровой порт отдельно от ip_address: INET не принимает 'ip:port'",
        ),
    )
    op.create_index(
        'uq_server_ip_port', 'server', ['ip_address', 'port'], unique=True,
        postgresql_where=sa.text('ip_address IS NOT NULL'),
    )
    op.execute(
        "INSERT INTO game_map (code, name) VALUES ('unknown', 'Unknown') "
        "ON CONFLICT (code) DO NOTHING"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_server_ip_port', table_name='server')
    op.drop_column('server', 'port')
    op.execute("DELETE FROM game_map WHERE code = 'unknown'")
