"""Построители Redis-ключей — единственное место, где живёт формат
`ark:tribe:*`. §8.1/§8.2 telemetry-api-v1.md: ключ на сущность/постройку
общий для `ark_backend` и `ark_relay` (Go), поэтому оба сервиса обязаны
использовать один и тот же формат — здесь для Python-стороны, в
`ark_relay/internal/store/redis.go` (entityHashKey/tribeIndexKey) —
для Go. Меняешь один — синхронно меняй второй.
"""

from __future__ import annotations


def structure_hash_key(tribe_id: str, struct_key: str) -> str:
    return f"ark:tribe:{tribe_id}:structure:{struct_key}"


def structure_scan_pattern(tribe_id: str = "*") -> str:
    return f"ark:tribe:{tribe_id}:structure:*"


def entity_hash_key(tribe_id: str, entity_key: str) -> str:
    """Тот же ключ, что и entityHashKey в ark_relay — используется здесь
    только для чтения "живой" карты (§7.2), запись — исключительно из
    ark_relay."""
    return f"ark:tribe:{tribe_id}:entity:{entity_key}"


def tribe_entities_index_key(tribe_id: str) -> str:
    return f"ark:tribe:{tribe_id}:entities"
