"""Тесты services/player_ingest_service.py — без Redis: парсинг сообщения
и апсерт в Postgres отдельно от механики Streams (та — test_redis_stream.py).

server_ip (не готовый server_id) -- контракт приведён в соответствие с
structure_sighting_service.py/dino_sighting_service.py: Go-продюсер знает
только строку подключения, сервер резолвится/создаётся на этой стороне
(server_repo.get_or_create_server_by_ip), тем же путём, что и там."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from models.player import Player
from services.player_ingest_service import ingest_player_sighting, parse_sighting

## --- parse_sighting ---


def test_parse_sighting_accepts_full_message():
    fields = {
        "server_ip": "203.0.113.5:7777",
        "platform_id": "steam:1",
        "character_name": "Rex",
        "level": "42",
        "x": "1.5",
        "y": "2.5",
        "z": "3.5",
    }

    sighting = parse_sighting(fields)

    assert sighting is not None
    assert sighting.server_ip == "203.0.113.5:7777"
    assert sighting.platform_id == "steam:1"
    assert sighting.character_name == "Rex"
    assert sighting.level == 42


def test_parse_sighting_accepts_partial_message_missing_optional_fields():
    fields = {"server_ip": "203.0.113.5:7777", "platform_id": "steam:2"}

    sighting = parse_sighting(fields)

    assert sighting is not None
    assert sighting.character_name is None
    assert sighting.level is None


def test_parse_sighting_rejects_missing_server_ip():
    assert parse_sighting({"platform_id": "steam:3"}) is None


def test_parse_sighting_rejects_missing_platform_id():
    assert parse_sighting({"server_ip": "203.0.113.5:7777"}) is None


def test_parse_sighting_rejects_blank_platform_id():
    assert parse_sighting({"server_ip": "203.0.113.5:7777", "platform_id": "   "}) is None


## --- ingest_player_sighting ---


def test_ingest_creates_player_and_person(db_session):
    sighting = parse_sighting(
        {
            "server_ip": "203.0.113.10:7777",
            "platform_id": "steam:5",
            "character_name": "Rex",
            "level": "10",
            "x": "1",
            "y": "2",
            "z": "3",
        }
    )

    player = ingest_player_sighting(db_session, sighting)

    assert player.platform_id == "steam:5"
    assert player.character_name == "Rex"
    assert player.level == 10
    assert player.x == 1.0 and player.y == 2.0 and player.z == 3.0
    assert player.last_seen_at is not None


def test_ingest_resolves_same_server_by_ip_across_calls(db_session):
    first = parse_sighting({"server_ip": "203.0.113.11:7777", "platform_id": "steam:9"})
    second = parse_sighting({"server_ip": "203.0.113.11:7777", "platform_id": "steam:10"})

    p1 = ingest_player_sighting(db_session, first)
    p2 = ingest_player_sighting(db_session, second)

    assert p1.server_id == p2.server_id


def test_ingest_upserts_same_player_on_second_sighting(db_session):
    first = parse_sighting({"server_ip": "203.0.113.12:7777", "platform_id": "steam:6", "level": "1"})
    ingest_player_sighting(db_session, first)

    second = parse_sighting({"server_ip": "203.0.113.12:7777", "platform_id": "steam:6", "level": "2"})
    ingest_player_sighting(db_session, second)

    stmt = select(Player).where(Player.platform_id == "steam:6")
    rows = list(db_session.execute(stmt).scalars())
    assert len(rows) == 1
    assert rows[0].level == 2


def test_ingest_partial_update_does_not_clobber_existing_fields(db_session):
    full = parse_sighting(
        {"server_ip": "203.0.113.13:7777", "platform_id": "steam:7", "character_name": "Rex", "level": "5"}
    )
    ingest_player_sighting(db_session, full)

    position_only = parse_sighting(
        {"server_ip": "203.0.113.13:7777", "platform_id": "steam:7", "x": "9", "y": "9", "z": "9"}
    )
    updated = ingest_player_sighting(db_session, position_only)

    assert updated.character_name == "Rex"  # не затёрто отсутствием поля в новом сообщении
    assert updated.level == 5
    assert updated.x == 9.0


def test_ingest_uses_explicit_observed_at_when_given(db_session):
    observed_at = datetime(2026, 1, 1, tzinfo=UTC)
    sighting = parse_sighting(
        {"server_ip": "203.0.113.14:7777", "platform_id": "steam:8", "observed_at": observed_at.isoformat()}
    )

    player = ingest_player_sighting(db_session, sighting)

    assert player.last_seen_at == observed_at
