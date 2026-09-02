"""Payload-схемы телеметрии — калька §5.1/§5.2 telemetry-api-v1.md.

Только `kind: "structures"` реализован по HTTP: `ally_positions` идёт по
живому каналу (`ark_relay`, УК-2 в доке — "не HTTP-снимок"), а
`enemy_positions` — открытый вопрос §9.2 ("не является рекомендацией
делать"), не реализуется здесь до отдельного решения владельца продукта.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

MAX_ITEMS_PER_SNAPSHOT = 2000
# FR-А2/§9.4: max_world_coord ограничивает разумные пределы карты ARK;
# крупнейшие официальные карты держатся в пределах этого порядка величины
# в игровых unreal units.
MAX_WORLD_COORD = 1_000_000.0


class TurretPayload(BaseModel):
    ammo: float
    range: float
    enabled: bool
    powered: bool


class StructureItem(BaseModel):
    class_: str = Field(alias="class")
    kind_hint: str | None = None
    x: float
    y: float
    z: float
    tribe_name: str
    team: int
    item_count: int | None = None
    has_item_count: bool
    turret: TurretPayload | None = None

    model_config = {"populate_by_name": True}

    @field_validator("x", "y", "z")
    @classmethod
    def _coord_in_bounds(cls, v: float) -> float:
        if abs(v) > MAX_WORLD_COORD:
            # Само по себе исключение здесь превращается в rejected[]
            # записи на уровне роутера (§6, "Частичная валидация внутри
            # снимка"), а не в отказ всего снимка.
            raise ValueError("coord_out_of_bounds")
        return v


class StructureSnapshot(BaseModel):
    snapshot_id: UUID
    client_id: UUID
    client_version: str
    tribe_id: UUID
    server_id: str
    map_id: str
    observed_at: datetime
    kind: Literal["structures"]
    items: list[StructureItem]

    @field_validator("items")
    @classmethod
    def _max_items(cls, v: list[StructureItem]) -> list[StructureItem]:
        if len(v) > MAX_ITEMS_PER_SNAPSHOT:
            raise ValueError(f"snapshot exceeds max_items_per_snapshot={MAX_ITEMS_PER_SNAPSHOT}")
        return v


class RejectedItem(BaseModel):
    index: int
    code: str


class SnapshotResult(BaseModel):
    accepted: int
    rejected: list[RejectedItem]


class StructureOut(BaseModel):
    id: UUID
    tribe_id: UUID
    map_id: str
    struct_key: str
    class_: str = Field(serialization_alias="class")
    kind_hint: str | None
    x: float | None
    y: float | None
    z: float | None
    item_count: int | None
    has_item_count: bool
    ammo: float | None
    range: float | None
    enabled: bool | None
    powered: bool | None
    first_seen_at: datetime
    last_seen_at: datetime
    status: str

    model_config = {"from_attributes": True}


class StructurePage(BaseModel):
    items: list[StructureOut]
    next_cursor: str | None
