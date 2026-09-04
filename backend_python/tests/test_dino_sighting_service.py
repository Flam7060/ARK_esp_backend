"""Тесты services/dino_sighting_service.py — прежде всего того, что дикие
существа не доезжают до Postgres вовсе.

Дикие раньше составляли основной объём записей в `tamed_dino`, не принося
ничего долговечного: у них нет владельца и устойчивой идентичности, а
«тут кто-то есть прямо сейчас» отвечает живой Redis-слой. Отсечка стоит на
двух рубежах — на релее (hub.maybeStream) и здесь; эти тесты про второй.
"""

from __future__ import annotations

from sqlalchemy import select

from models.tamed_dino import TamedDino
from services.dino_sighting_service import ingest_dino_sighting, parse_sighting


def _fields(**overrides: str) -> dict[str, str]:
    base = {
        "server_ip": "203.0.113.7:7777",
        "object_hash": "hash-abc",
        "class": "Rex_Character_BP_C",
        "tribe_name": "Тестовое племя",
        "team": "1387",
        "x": "100",
        "y": "200",
        "z": "300",
    }
    base.update(overrides)
    return base


def _stored(session, object_hash: str) -> list[TamedDino]:
    return list(session.execute(select(TamedDino).where(TamedDino.object_hash == object_hash)).scalars())


def test_wild_dino_is_not_stored(db_session):
    sighting = parse_sighting(_fields(object_hash="hash-wild", tamed="false"))
    assert sighting is not None

    ingest_dino_sighting(db_session, sighting)

    assert _stored(db_session, "hash-wild") == []


def test_tamed_dino_is_stored(db_session):
    sighting = parse_sighting(_fields(object_hash="hash-tamed", tamed="true"))
    assert sighting is not None

    ingest_dino_sighting(db_session, sighting)

    rows = _stored(db_session, "hash-tamed")
    assert len(rows) == 1
    assert rows[0].x == 100


def test_message_without_tamed_field_is_not_stored(db_session):
    """Сообщение от старой сборки релея (поля ещё нет) трактуется как
    дикое: лучше не записать, чем записать неизвестно что."""
    fields = _fields(object_hash="hash-legacy")
    fields.pop("tamed", None)

    sighting = parse_sighting(fields)
    assert sighting is not None
    assert sighting.tamed is False

    ingest_dino_sighting(db_session, sighting)

    assert _stored(db_session, "hash-legacy") == []
