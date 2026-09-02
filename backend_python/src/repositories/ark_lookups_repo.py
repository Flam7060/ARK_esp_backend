"""Автосоздание строк-справочников (`structure_class`, `species`) для
классов игры, встреченных впервые через Redis Stream ingestion --
ark_relay пересылает голое имя класса из памяти игры
(`kopt::Actor::class_name`), а не заранее заведённый код справочника, и
ждать, пока кто-то вручную заведёт каждый новый класс/вид, значило бы
терять данные до этого момента."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.ark_lookups import Species, StructureClass


def get_or_create_structure_class(session: Session, code: str, *, is_turret: bool) -> StructureClass:
    """`category`/`label` заполняются грубо (сам код класса) -- это
    справочник для FK-целостности `ark_structure.class_code`, не витрина;
    уточнить category/label вручную позже дешевле, чем блокировать
    ingestion на классификацию каждого нового класса игры."""
    row = session.get(StructureClass, code)
    if row is not None:
        return row

    row = StructureClass(
        code=code,
        category="turret" if is_turret else "unknown",
        label=code,
        is_turret=is_turret,
    )
    session.add(row)
    try:
        session.flush()
    except Exception:
        session.rollback()
        row = session.get(StructureClass, code)
        if row is None:
            raise
    return row


def get_or_create_species(session: Session, blueprint_path: str) -> Species:
    row = session.execute(select(Species).where(Species.blueprint_path == blueprint_path)).scalar_one_or_none()
    if row is not None:
        return row

    row = Species(blueprint_path=blueprint_path, display_name=blueprint_path)
    session.add(row)
    try:
        session.flush()
    except Exception:
        session.rollback()
        row = session.execute(select(Species).where(Species.blueprint_path == blueprint_path)).scalar_one_or_none()
        if row is None:
            raise
    return row
