#!/usr/bin/env python3
"""Создание учётки `admin` — единственный способ завести админа (см.
`models.admin.Admin`: "Заводится только через CLI (нет self-signup
эндпоинта)"). Нет HTTP-ручки и не будет: /v1/admins с открытой регистрацией
свёл бы на нет саму идею изоляции привилегий.

Запуск (из backend_python/, с окружением, в котором читается .env —
т.е. те же DB_*/SECURITY_PEPPER, что видит сам ark_backend):

    uv run python scripts/create_admin.py --username root --role superadmin

Внутри docker compose (переменные уже в контейнере, ничего добавлять не
надо; рабочая директория контейнера — /app/src, поэтому путь до скрипта
абсолютный, не относительно неё):

    docker compose exec backend_python python /app/scripts/create_admin.py --username root --role superadmin

(или короче — `make create-admin` из backend/, см. Makefile)

Пароль запрашивается интерактивно (getpass, с подтверждением) — не флагом
командной строки: аргументы процесса видны через `ps aux` всем в системе и
оседают в шелл-истории, а пароль первого/единственного суперадмина обычно
не тот секрет, которым можно так рисковать.
"""

from __future__ import annotations

import argparse
import getpass
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy.exc import IntegrityError  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402

from core.db import get_engine  # noqa: E402
from core.passwords import hash_password  # noqa: E402
from models.admin import Admin  # noqa: E402


def _prompt_password() -> str:
    while True:
        password = getpass.getpass("Пароль: ")
        if len(password) < 12:
            print("Пароль короче 12 символов — введите ещё раз.", file=sys.stderr)
            continue
        confirm = getpass.getpass("Повторите пароль: ")
        if password != confirm:
            print("Пароли не совпадают — введите ещё раз.", file=sys.stderr)
            continue
        return password


def create_admin(
    session: Session,
    *,
    username: str,
    password: str,
    role_code: str,
    display_name: str | None,
    created_by_admin_id: uuid.UUID | None,
) -> Admin:
    admin = Admin(
        username=username,
        display_name=display_name,
        password_hash=hash_password(password),
        role_code=role_code,
        created_by_admin_id=created_by_admin_id,
    )
    session.add(admin)
    session.commit()
    session.refresh(admin)
    return admin


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--username", required=True, help="уникальный логин админки")
    parser.add_argument("--display-name", default=None, help="отображаемое имя (опционально)")
    parser.add_argument(
        "--role",
        default="admin",
        help="код роли из admin_role (seed: superadmin, admin, support, developer). По умолчанию: admin",
    )
    parser.add_argument(
        "--created-by",
        default=None,
        help="UUID админа-создателя (audit-цепочка). Пусто — только для самого первого админа",
    )
    args = parser.parse_args()

    created_by_admin_id: uuid.UUID | None = None
    if args.created_by:
        try:
            created_by_admin_id = uuid.UUID(args.created_by)
        except ValueError:
            print(f"error: --created-by не похож на UUID: {args.created_by!r}", file=sys.stderr)
            return 2

    password = _prompt_password()

    session_factory = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)
    with session_factory() as session:
        try:
            admin = create_admin(
                session,
                username=args.username,
                password=password,
                role_code=args.role,
                display_name=args.display_name,
                created_by_admin_id=created_by_admin_id,
            )
        except IntegrityError as exc:
            session.rollback()
            # Различить "такой username уже есть" и "такого role_code/
            # created_by нет в справочнике/таблице" по тексту ошибки СУБД
            # не всегда надёжно — обе печатаются как есть, оператор CLI
            # читает исходную причину сам, а не догадку скрипта.
            print(f"error: не удалось создать admin — {exc.orig}", file=sys.stderr)
            return 1

    print(f"OK: admin создан — id={admin.id} username={admin.username} role={admin.role_code}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
