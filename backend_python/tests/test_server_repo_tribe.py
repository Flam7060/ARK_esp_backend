"""Тесты repositories/server_repo.get_or_create_tribe — резолв трайбы по
ark_tribe_id (настоящий числовой ARK team/tribe id), с фоллбеком и
backfill'ом по имени для legacy-строк без id."""

from __future__ import annotations

import uuid

from models.ark_lookups import GameMap
from models.topology import Server
from repositories.server_repo import get_or_create_tribe


def _make_server(session) -> Server:
    map_code = f"map-{uuid.uuid4().hex[:8]}"
    session.add(GameMap(code=map_code, name="Test Map"))
    session.flush()
    server = Server(name="Test Server", map_code=map_code)
    session.add(server)
    session.commit()
    return server


def test_no_tribe_name_returns_none(db_session):
    server = _make_server(db_session)
    assert get_or_create_tribe(db_session, server.id, None, ark_tribe_id=12345) is None


def test_creates_tribe_with_ark_tribe_id(db_session):
    server = _make_server(db_session)
    tribe = get_or_create_tribe(db_session, server.id, "TestTribe", ark_tribe_id=555)
    assert tribe is not None
    assert tribe.name == "TestTribe"
    assert tribe.ark_tribe_id == 555


def test_second_call_with_same_id_returns_same_row(db_session):
    server = _make_server(db_session)
    first = get_or_create_tribe(db_session, server.id, "TestTribe", ark_tribe_id=777)
    second = get_or_create_tribe(db_session, server.id, "TestTribe", ark_tribe_id=777)
    assert first.id == second.id


def test_lookup_by_id_wins_over_name_on_rename(db_session):
    """Трайба переименовалась в игре -- тот же ark_tribe_id должен найти
    существующую строку и обновить имя, не создать вторую."""
    server = _make_server(db_session)
    original = get_or_create_tribe(db_session, server.id, "OldName", ark_tribe_id=888)
    db_session.flush()

    renamed = get_or_create_tribe(db_session, server.id, "NewName", ark_tribe_id=888)

    assert renamed.id == original.id
    assert renamed.name == "NewName"


def test_legacy_row_without_id_backfills_on_next_sighting(db_session):
    """Строка создана до фикса (без ark_tribe_id, только по имени) -- первое
    сообщение с настоящим id должно найти её по имени и проставить id, а не
    создать дубликат."""
    server = _make_server(db_session)
    legacy = get_or_create_tribe(db_session, server.id, "LegacyTribe", ark_tribe_id=None)
    assert legacy.ark_tribe_id is None
    db_session.flush()

    backfilled = get_or_create_tribe(db_session, server.id, "LegacyTribe", ark_tribe_id=999)

    assert backfilled.id == legacy.id
    assert backfilled.ark_tribe_id == 999


def test_zero_or_negative_id_treated_as_unknown(db_session):
    server = _make_server(db_session)
    tribe = get_or_create_tribe(db_session, server.id, "WildOrUnclaimed", ark_tribe_id=0)
    assert tribe is not None
    assert tribe.ark_tribe_id is None
