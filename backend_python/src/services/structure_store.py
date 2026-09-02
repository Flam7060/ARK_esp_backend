"""Redis-кэш построек (§8.2) — буфер перед записью в Postgres.

Реализует FR-5 (идентичность постройки без стабильного игрового Addr) и
UC-1 шаг 4 (upsert по этому ключу).
"""

from __future__ import annotations

from datetime import UTC, datetime

from redis.asyncio import Redis

from routers.v1.schemas.telemetry import StructureItem
from services.redis_keys import structure_hash_key

# FR-5: "постройки не двигаются, и два наблюдения одной постройки в разные
# моменты почти всегда лягут в один и тот же квадрат" — округление, а не
# точное совпадение координат, компенсирует погрешность чтения памяти.
DEFAULT_DEDUP_RADIUS = 300.0

# §8.2: постройки не эфемерны, как игроки — держим кэш долго; если ключ всё
# же вымоется по памяти до переноса в Postgres, следующий снимок клиента
# его пересоздаст без потери данных сверх интервала между снимками.
STRUCTURE_CACHE_TTL_SECONDS = 24 * 60 * 60


def _grid(coord: float, radius: float) -> int:
    return round(coord / radius)


def build_struct_key(map_id: str, class_: str, x: float, y: float, z: float, radius: float = DEFAULT_DEDUP_RADIUS) -> str:
    """Составной ключ FR-5: map_id + class + округлённая координата.

    map_id входит в сам struct_key (а не только в отдельную колонку
    Postgres), потому что Redis-ключ §8.2 параметризован только по
    tribe_id и struct_key — без него две одинаковые постройки на разных
    картах одного трайба схлопнулись бы в одну запись.
    """
    gx, gy, gz = _grid(x, radius), _grid(y, radius), _grid(z, radius)
    return f"{class_}:{map_id}:{gx}:{gy}:{gz}"


async def upsert_cached_structure(
    redis: Redis,
    tribe_id: str,
    map_id: str,
    item: StructureItem,
    radius: float = DEFAULT_DEDUP_RADIUS,
) -> str:
    """UC-1 шаг 4: пишет/обновляет постройку в Redis-кэше, помечает dirty.

    first_seen_at выставляется один раз (HSETNX), last_seen_at и остальные
    поля — на каждый апдейт (HSET) — ровно то различие, которое требует
    UC-1 ("first_seen_at" не трогается при повторных апдейтах, §8.2).
    """
    struct_key = build_struct_key(map_id, item.class_, item.x, item.y, item.z, radius)
    key = structure_hash_key(tribe_id, struct_key)
    now = datetime.now(UTC).isoformat()

    turret = item.turret
    mapping: dict[str, str] = {
        "class": item.class_,
        "kind_hint": item.kind_hint or "",
        "x": str(item.x),
        "y": str(item.y),
        "z": str(item.z),
        "item_count": "" if item.item_count is None else str(item.item_count),
        "has_item_count": "1" if item.has_item_count else "0",
        "ammo": "" if turret is None else str(turret.ammo),
        "range": "" if turret is None else str(turret.range),
        "enabled": "" if turret is None else ("1" if turret.enabled else "0"),
        "powered": "" if turret is None else ("1" if turret.powered else "0"),
        "last_seen_at": now,
        "dirty": "1",
    }

    async with redis.pipeline(transaction=True) as pipe:
        pipe.hsetnx(key, "first_seen_at", now)
        pipe.hset(key, mapping=mapping)
        pipe.expire(key, STRUCTURE_CACHE_TTL_SECONDS)
        await pipe.execute()

    return struct_key
