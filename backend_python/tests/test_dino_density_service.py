"""Тесты чистых функций services/dino_density_service.py — разбора ключа
комнаты, индекса ячейки и границы окна.

Без Redis и Postgres намеренно: это ровно те три места, где легко получить
тихо неправильную карту (порт, отрезанный от адреса; слипшиеся клетки по
разные стороны нуля; окно, съехавшее на секунды), и каждое из них
проверяется в отрыве от инфраструктуры.
"""

from __future__ import annotations

from datetime import UTC, datetime

from services.dino_density_service import bucket_start_for, cell_index, server_ip_from_room_key

GROUP = "1e2d3c4b-5a69-4f80-9a1b-2c3d4e5f6a7b"


def test_server_ip_keeps_its_port():
    """server_ip это 'ip:port' — в нём своё двоеточие, и наивный split(':')
    отрезал бы порт, схлопнув разные серверы в один."""
    key = f"ark:group:{GROUP}:server:203.0.113.7:7777:entities"

    assert server_ip_from_room_key(key, GROUP) == "203.0.113.7:7777"


def test_foreign_key_shape_is_rejected():
    assert server_ip_from_room_key(f"ark:group:{GROUP}:server:203.0.113.7:7777", GROUP) is None
    assert server_ip_from_room_key("ark:something:else:entities", GROUP) is None


def test_cell_index_floors_negative_coordinates():
    """int() округляет к нулю, и клетки по разные стороны от нуля слипались
    бы попарно — половина карты с удвоенной плотностью."""
    assert cell_index(-1.0, 10_000) == -1
    assert cell_index(-10_000.0, 10_000) == -1
    assert cell_index(-10_001.0, 10_000) == -2
    assert cell_index(0.0, 10_000) == 0
    assert cell_index(9_999.0, 10_000) == 0
    assert cell_index(10_000.0, 10_000) == 1


def test_cell_index_separates_neighbouring_cells():
    assert cell_index(-1.0, 10_000) != cell_index(1.0, 10_000)


def test_bucket_start_snaps_down_to_window():
    moment = datetime(2026, 9, 4, 13, 47, 31, tzinfo=UTC)

    assert bucket_start_for(moment, 3600) == datetime(2026, 9, 4, 13, 0, tzinfo=UTC)


def test_bucket_start_is_stable_inside_one_window():
    a = datetime(2026, 9, 4, 13, 0, 0, tzinfo=UTC)
    b = datetime(2026, 9, 4, 13, 59, 59, tzinfo=UTC)

    assert bucket_start_for(a, 3600) == bucket_start_for(b, 3600)
