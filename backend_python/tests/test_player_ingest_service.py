"""Тесты services/player_ingest_service.py — без Redis: парсинг сообщения
и апсерт в Postgres отдельно от механики Streams (та — test_redis_stream.py)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from models.ark_lookups import GameMap
from models.player import Player
from models.topology import Server
from services.player_ingest_service import ingest_player_sighting, parse_sighting


def _make_server(session) -> Server:
    map_code = f"map-{uuid.uuid4().hex[:8]}"
    session.add(GameMap(code=map_code, name="Test Map"))
    session.flush()
    server = Server(name="Test Server", map_code=map_code)
    session.add(server)
    session.commit()
    return server


## --- parse_sighting ---


def test_parse_sighting_accepts_full_message():
    server_id = uuid.uuid4()
    fields = {
        "server_id": str(server_id),
        "platform_id": "steam:1",
        "character_name": "Rex",
        "level": "42",
        "x": "1.5",
        "y": "2.5",
        "z": "3.5",
    }

    sighting = parse_sighting(fields)

    assert sighting is not None
    assert sighting.server_id == server_id
    assert sighting.platform_id == "steam:1"
    assert sighting.character_name == "Rex"
    assert sighting.level == 42


def test_parse_sighting_accepts_partial_message_missing_optional_fields():
    fields = {"server_id": str(uuid.uuid4()), "platform_id": "steam:2"}

    sighting = parse_sighting(fields)

    assert sighting is not None
    assert sighting.character_name is None
    assert sighting.level is None


def test_parse_sighting_rejects_missing_server_id():
    assert parse_sighting({"platform_id": "steam:3"}) is None


def test_parse_sighting_rejects_missing_platform_id():
    assert parse_sighting({"server_id": str(uuid.uuid4())}) is None


def test_parse_sighting_rejects_blank_platform_id():
    assert parse_sighting({"server_id": str(uuid.uuid4()), "platform_id": "   "}) is None


def test_parse_sighting_rejects_garbage_server_id():
    assert parse_sighting({"server_id": "not-a-uuid", "platform_id": "steam:4"}) is None


## --- ingest_player_sighting ---


def test_ingest_creates_player_and_person(db_session):
    server = _make_server(db_session)
    sighting = parse_sighting(
        {"server_id": str(server.id), "platform_id": "steam:5", "character_name": "Rex", "level": "10", "x": "1", "y": "2", "z": "3"}
    )

    player = ingest_player_sighting(db_session, sighting)

    assert player.server_id == server.id
    assert player.platform_id == "steam:5"
    assert player.character_name == "Rex"
    assert player.level == 10
    assert player.x == 1.0 and player.y == 2.0 and player.z == 3.0
    assert player.last_seen_at is not None


def test_ingest_upserts_same_player_on_second_sighting(db_session):
    server = _make_server(db_session)
    first = parse_sighting({"server_id": str(server.id), "platform_id": "steam:6", "level": "1"})
    ingest_player_sighting(db_session, first)

    second = parse_sighting({"server_id": str(server.id), "platform_id": "steam:6", "level": "2"})
    ingest_player_sighting(db_session, second)

    stmt = select(Player).where(Player.server_id == server.id, Player.platform_id == "steam:6")
    rows = list(db_session.execute(stmt).scalars())
    assert len(rows) == 1
    assert rows[0].level == 2


def test_ingest_partial_update_does_not_clobber_existing_fields(db_session):
    server = _make_server(db_session)
    full = parse_sighting(
        {"server_id": str(server.id), "platform_id": "steam:7", "character_name": "Rex", "level": "5"}
    )
    ingest_player_sighting(db_session, full)

    position_only = parse_sighting({"server_id": str(server.id), "platform_id": "steam:7", "x": "9", "y": "9", "z": "9"})
    updated = ingest_player_sighting(db_session, position_only)

    assert updated.character_name == "Rex"  # не затёрто отсутствием поля в новом сообщении
    assert updated.level == 5
    assert updated.x == 9.0


def test_ingest_uses_explicit_observed_at_when_given(db_session):
    server = _make_server(db_session)
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    sighting = parse_sighting(
        {"server_id": str(server.id), "platform_id": "steam:8", "observed_at": observed_at.isoformat()}
    )

    player = ingest_player_sighting(db_session, sighting)

    assert player.last_seen_at == observed_at
