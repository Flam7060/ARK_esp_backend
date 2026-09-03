"""Репозиторий `server`/`tribe` для ingestion из Redis Stream
(structure_sighting_service.py/dino_sighting_service.py) — persistence без
бизнес-правил, тем же принципом, что repositories/player_repo.py.

`server_ip` — реальный идентификатор игрового сервера (DTO-sharing plan
§4: два разных сервера могут крутить одну и ту же карту, `map_id` один
этого не различает). Настоящее обогащение через A2S_INFO (имя сервера,
код карты) сюда намеренно не входит — отдельная, самостоятельная задача
(нужен рабочий UDP-опрос произвольных ARK-серверов, который негде
проверить без реального сервера под рукой); до неё сервер создаётся с
`map_code='unknown'` (см. миграция f1a2b3c4d5e6) и именем-заглушкой из
самого IP, а дальше это правится или задачей A2S-обогащения, или руками."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.topology import Server
from models.tribe import Tribe

_UNKNOWN_MAP_CODE = "unknown"


def _split_ip_port(server_ip: str) -> tuple[str, int | None]:
    """"ip:port" -> (ip, port). `server.ip_address` is Postgres INET, which
    rejects a ":port" suffix outright (caught by an actual end-to-end test
    against real Postgres, not assumed) -- port lives in its own column
    (models/topology.py::Server.port). Splits on the LAST colon, which is
    correct for "1.2.3.4:7777" and for a bracketed "[::1]:7777"; a bare
    (unbracketed) IPv6 address with no port is out of scope -- ARK servers
    are IPv4 in practice, and the Go relay always sends "ip:port" as one
    string (never a bare IP), so a missing colon is treated as malformed
    rather than "IPv6, no port"."""
    host, _, port_str = server_ip.rpartition(":")
    if not host:
        raise ValueError(f"server_ip {server_ip!r} has no ':port' suffix")
    try:
        port = int(port_str)
    except ValueError as exc:
        raise ValueError(f"server_ip {server_ip!r} has a non-numeric port") from exc
    return host.strip("[]"), port


def get_or_create_server_by_ip(session: Session, server_ip: str) -> Server:
    """(ip_address, port) уникальна как пара (миграция f1a2b3c4d5e6) -- под
    конкурентной вставкой полагаемся на эту constraint, не на "прочитали
    пусто, значит можно вставлять": IntegrityError на INSERT ловится и
    превращается в повторный SELECT, а не всплывает как ошибка обработки
    сообщения."""
    ip, port = _split_ip_port(server_ip)

    server = session.execute(
        select(Server).where(Server.ip_address == ip, Server.port == port)
    ).scalar_one_or_none()
    if server is not None:
        return server

    server = Server(name=server_ip, ip_address=ip, port=port, map_code=_UNKNOWN_MAP_CODE)
    session.add(server)
    try:
        session.flush()
    except Exception:
        # Другой консьюмер/поток успел вставить эту же запись между нашим
        # SELECT и INSERT -- откатываем неудачную вставку и берём то, что
        # уже есть, вместо падения на кривом сообщении.
        session.rollback()
        server = session.execute(
            select(Server).where(Server.ip_address == ip, Server.port == port)
        ).scalar_one_or_none()
        if server is None:
            raise
    return server


def get_or_create_tribe(
    session: Session, server_id: uuid.UUID, tribe_name: str | None, ark_tribe_id: int | None = None
) -> Tribe | None:
    """None, когда tribe_name не пришёл -- анклеймленная/неизвестная
    принадлежность остаётся NULL (tribe_id nullable на ark_structure/
    tamed_dino), а не привязывается к выдуманному племени.

    ark_tribe_id -- настоящий числовой ARK TargetingTeam/TribeID, когда
    отправитель его знает (Entity.Team на проводе; 0/отрицательный трактуется
    как "неизвестен", как для клиентов, которые его вообще не шлют). Когда
    задан -- приоритетный ключ поиска: имя трайбы меняется (переименование
    в игре), team-id для владельца не меняется, а uq_tribe_server_name
    держит уникальность по (server_id, name), не по id -- поиск сперва по
    id, потом фоллбек на имя, не наоборот, иначе переименованная трайба
    задвоится вместо переиспользования старой строки."""
    if not tribe_name:
        return None
    has_id = ark_tribe_id is not None and ark_tribe_id > 0

    if has_id:
        tribe = session.execute(
            select(Tribe).where(Tribe.server_id == server_id, Tribe.ark_tribe_id == ark_tribe_id)
        ).scalar_one_or_none()
        if tribe is not None:
            if tribe.name != tribe_name:
                tribe.name = tribe_name  # трайба переименовалась в игре
            return tribe

    tribe = session.execute(
        select(Tribe).where(Tribe.server_id == server_id, Tribe.name == tribe_name)
    ).scalar_one_or_none()
    if tribe is not None:
        if has_id and tribe.ark_tribe_id is None:
            tribe.ark_tribe_id = ark_tribe_id  # legacy-строка без id, дошёл настоящий -- backfill
        return tribe

    tribe = Tribe(server_id=server_id, name=tribe_name, ark_tribe_id=ark_tribe_id if has_id else None)
    session.add(tribe)
    try:
        session.flush()
    except Exception:
        session.rollback()
        tribe = session.execute(
            select(Tribe).where(Tribe.server_id == server_id, Tribe.name == tribe_name)
        ).scalar_one_or_none()
        if tribe is None:
            raise
    return tribe
