"""dino_density -- тепловая карта ручных дино вместо строки на животное

Revision ID: b7c1d2e3f4a5
Revises: a1b2c3d4e5f6
Create Date: 2026-09-04 00:00:00.000000

Заменяет ценность, которую `tamed_dino` не давала: у дино нет настоящей
идентичности, строка-на-животное копила объём без информации. Здесь одна
строка на (сервер, ячейка сетки, трайб, окно времени) — "где сгущение
питомцев чьего трайба".

Дикие в эту таблицу не попадают вовсе: relay перестал писать их в
durable-поток совсем (hub.maybeStream), они остаются только в живом
Redis-слое для ESP.

`cell_size_units` входит в уникальный ключ намеренно: это параметр замера.
Поменяется размер ячейки — старые строки останутся интерпретируемыми и
явно несравнимыми с новыми, вместо молчаливого смешивания.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b7c1d2e3f4a5'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'dino_density',
        sa.Column('id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('server_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            'tribe_id', postgresql.UUID(as_uuid=True), nullable=False,
            comment="Обязателен: строка без владельца не несёт смысла (карта отвечает 'чья масса'), "
                    "а NULL в составе уникального ключа ломал бы ON CONFLICT — в Postgres NULL != NULL, "
                    "и такие ячейки копились бы дубликатами на каждый замер",
        ),
        sa.Column(
            'cell_size_units', sa.Integer(), nullable=False,
            comment="Размер ячейки в игровых юнитах. В ключе, а не константой в коде: "
                    "поменяется — старые строки останутся интерпретируемыми и явно несравнимыми с новыми",
        ),
        sa.Column('cell_x', sa.Integer(), nullable=False, comment="floor(x / cell_size_units)"),
        sa.Column('cell_y', sa.Integer(), nullable=False, comment="floor(y / cell_size_units)"),
        sa.Column(
            'bucket_start', sa.DateTime(timezone=True), nullable=False,
            comment="Начало временного окна агрегации",
        ),
        sa.Column(
            'count_max', sa.Integer(), nullable=False,
            comment="Пик за окно — какая масса там вообще стояла",
        ),
        sa.Column(
            'count_last', sa.Integer(), nullable=False,
            comment="Последний замер в окне. Отдельно от count_max: max без last не отличает "
                    "'было и разошлось' от 'стоит сейчас', last без max теряет пик между заходами скаута",
        ),
        sa.Column(
            'observed_at', sa.DateTime(timezone=True), nullable=False,
            comment="Когда ячейку последний раз обновляли",
        ),
        sa.Column('reported_by_account_id', postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(['server_id'], ['server.id']),
        sa.ForeignKeyConstraint(['tribe_id'], ['tribe.id']),
        sa.ForeignKeyConstraint(['reported_by_account_id'], ['account.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'server_id', 'cell_size_units', 'cell_x', 'cell_y', 'tribe_id', 'bucket_start',
            name='uq_dino_density_cell',
        ),
    )
    op.create_index('ix_dino_density_server_bucket', 'dino_density', ['server_id', 'bucket_start'])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_dino_density_server_bucket', table_name='dino_density')
    op.drop_table('dino_density')
