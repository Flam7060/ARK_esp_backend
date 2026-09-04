"""ORM-модели единой БД AUTH+ARK (см. DBML-спеку в чате/доке проекта) —
полной от одного `import models`, как того требует Alembic autogenerate
(см. migrations/env.py).

`StructureLegasy` (models/structure_legacy.py) — MVP-заглушка телеметрии,
ещё в работе (structure_repo.py, structure_query_service.py,
structure_flush.py, routers/v1/tribes.py). Имя/таблица не пересекается с
новой схемой (`ArkStructure`/`ark_structure` vs `Structure`/`structure`)
— обе части могут жить в одном `Base.metadata` одновременно.

`UserLegasy` снесён целиком (models/user_legacy.py + весь CRUD-стек
вокруг него) — был эталонным шаблоном архитектуры, не настоящей
сущностью системы; таблица `user` роняется миграцией
`drop_legacy_user_table`.
"""

from models.account import Account
from models.account_password_reset_token import AccountPasswordResetToken
from models.activation_key import ActivationKey
from models.admin import Admin
from models.api_key import ApiKey, ApiKeyScope
from models.ark_lookups import GameMap, LogEventType, Species, StructureClass
from models.ark_structure import ArkStructure, ArkStructureGenerator, ArkStructureTurret
from models.auth_lookups import (
    AccountStatus,
    ActivationKeyOrigin,
    ActivationKeyStatus,
    AdminRole,
    AdminStatus,
    ApiKeyStatus,
    GroupRole,
    InviteStatus,
)
from models.dino_density import DinoDensity
from models.fingerprint import ComponentType, Fingerprint, FingerprintComponent
from models.player import Player
from models.sharing import GroupInviteToken, GroupMember, SharingGroup
from models.tamed_dino import TamedDino
from models.topology import Cluster, Person, Server
from models.tribe import Tribe
from models.tribe_log import TribeLog


from models.structure_legacy import Structure as StructureLegasy


__all__ = [
    "Account",
    "AccountPasswordResetToken",
    "AccountStatus",
    "ActivationKey",
    "ActivationKeyOrigin",
    "ActivationKeyStatus",
    "Admin",
    "AdminRole",
    "AdminStatus",
    "ApiKey",
    "ApiKeyScope",
    "ApiKeyStatus",
    "ArkStructure",
    "ArkStructureGenerator",
    "ArkStructureTurret",
    "Cluster",
    "ComponentType",
    "DinoDensity",
    "Fingerprint",
    "FingerprintComponent",
    "GameMap",
    "GroupInviteToken",
    "GroupMember",
    "GroupRole",
    "InviteStatus",
    "LogEventType",
    "Person",
    "Player",
    "Server",
    "SharingGroup",
    "Species",
    "StructureClass",
    "TamedDino",
    "Tribe",
    "TribeLog",
    "StructureLegasy",
]
