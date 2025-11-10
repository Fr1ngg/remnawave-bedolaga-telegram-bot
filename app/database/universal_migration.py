import logging
from datetime import datetime

from sqlalchemy import select, text

from app.config import settings
from app.database.database import AsyncSessionLocal, engine
from app.database.models import WebApiToken
from app.utils.security import hash_api_token

logger = logging.getLogger(__name__)


async def get_database_type() -> str:
    """Return the name of the current database dialect."""
    return engine.dialect.name


async def check_table_exists(table_name: str) -> bool:
    """Check whether the given table exists in the connected database."""
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            if db_type == "sqlite":
                result = await conn.execute(
                    text(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name=:table_name"
                    ),
                    {"table_name": table_name},
                )
                return result.fetchone() is not None

            if db_type == "postgresql":
                result = await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public' AND table_name = :table_name"
                    ),
                    {"table_name": table_name},
                )
                return result.fetchone() is not None

            if db_type == "mysql":
                result = await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = DATABASE() AND table_name = :table_name"
                    ),
                    {"table_name": table_name},
                )
                return result.fetchone() is not None

    except Exception as error:  # pragma: no cover - logging defensive branch
        logger.error("Ошибка проверки существования таблицы %s: %s", table_name, error)

    return False


async def check_column_exists(table_name: str, column_name: str) -> bool:
    """Check whether the given column exists in the table."""
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            if db_type == "sqlite":
                result = await conn.execute(text(f"PRAGMA table_info({table_name})"))
                columns = result.fetchall()
                return any(column[1] == column_name for column in columns)

            if db_type == "postgresql":
                result = await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = :table_name AND column_name = :column_name"
                    ),
                    {"table_name": table_name, "column_name": column_name},
                )
                return result.fetchone() is not None

            if db_type == "mysql":
                result = await conn.execute(
                    text(
                        "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
                        "WHERE TABLE_NAME = :table_name AND COLUMN_NAME = :column_name"
                    ),
                    {"table_name": table_name, "column_name": column_name},
                )
                return result.fetchone() is not None

    except Exception as error:  # pragma: no cover - logging defensive branch
        logger.error(
            "Ошибка проверки существования колонки %s.%s: %s",
            table_name,
            column_name,
            error,
        )

    return False


async def ensure_default_web_api_token() -> bool:
    """Ensure that a bootstrap API token from the configuration exists."""
    default_token = (settings.WEB_API_DEFAULT_TOKEN or "").strip()
    if not default_token:
        return True

    token_name = (settings.WEB_API_DEFAULT_TOKEN_NAME or "Bootstrap Token").strip()

    try:
        async with AsyncSessionLocal() as session:
            token_hash = hash_api_token(default_token, settings.WEB_API_TOKEN_HASH_ALGORITHM)
            result = await session.execute(
                select(WebApiToken).where(WebApiToken.token_hash == token_hash)
            )
            existing = result.scalar_one_or_none()

            if existing:
                updated = False

                if not existing.is_active:
                    existing.is_active = True
                    updated = True

                if token_name and existing.name != token_name:
                    existing.name = token_name
                    updated = True

                if updated:
                    existing.updated_at = datetime.utcnow()
                    await session.commit()

                return True

            token = WebApiToken(
                name=token_name or "Bootstrap Token",
                token_hash=token_hash,
                token_prefix=default_token[:12],
                description="Автоматически создан при миграции",
                created_by="migration",
                is_active=True,
            )
            session.add(token)
            await session.commit()
            logger.info("✅ Создан дефолтный токен веб-API из конфигурации")
            return True

    except Exception as error:  # pragma: no cover - logging defensive branch
        logger.error("❌ Ошибка создания дефолтного веб-API токена: %s", error)
        return False


async def add_promo_group_priority_column() -> bool:
    """Добавляет колонку priority в таблицу promo_groups."""
    column_exists = await check_column_exists("promo_groups", "priority")
    if column_exists:
        logger.info("Колонка priority уже существует в promo_groups")
        return True

    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            if db_type == "sqlite":
                column_def = "INTEGER NOT NULL DEFAULT 0"
            elif db_type == "postgresql":
                column_def = "INTEGER NOT NULL DEFAULT 0"
            else:  # MySQL и другие совместимые диалекты
                column_def = "INT NOT NULL DEFAULT 0"

            await conn.execute(
                text(f"ALTER TABLE promo_groups ADD COLUMN priority {column_def}")
            )

            if db_type in {"postgresql", "sqlite"}:
                await conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS idx_promo_groups_priority "
                        "ON promo_groups(priority DESC)"
                    )
                )
            else:  # MySQL
                await conn.execute(
                    text("CREATE INDEX idx_promo_groups_priority ON promo_groups(priority DESC)")
                )

        logger.info("✅ Добавлена колонка priority в promo_groups с индексом")
        return True

    except Exception as error:
        logger.error("Ошибка добавления колонки priority: %s", error)
        return False


async def create_user_promo_groups_table() -> bool:
    """Создает таблицу user_promo_groups для связи Many-to-Many между users и promo_groups."""
    table_exists = await check_table_exists("user_promo_groups")
    if table_exists:
        logger.info("ℹ️ Таблица user_promo_groups уже существует")
        return True

    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            if db_type == "sqlite":
                create_sql = """
                CREATE TABLE user_promo_groups (
                    user_id INTEGER NOT NULL,
                    promo_group_id INTEGER NOT NULL,
                    assigned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    assigned_by VARCHAR(50) DEFAULT 'system',
                    PRIMARY KEY (user_id, promo_group_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (promo_group_id) REFERENCES promo_groups(id) ON DELETE CASCADE
                );
                """
                index_sql = "CREATE INDEX idx_user_promo_groups_user_id ON user_promo_groups(user_id);"
            elif db_type == "postgresql":
                create_sql = """
                CREATE TABLE user_promo_groups (
                    user_id INTEGER NOT NULL,
                    promo_group_id INTEGER NOT NULL,
                    assigned_at TIMESTAMP DEFAULT NOW(),
                    assigned_by VARCHAR(50) DEFAULT 'system',
                    PRIMARY KEY (user_id, promo_group_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (promo_group_id) REFERENCES promo_groups(id) ON DELETE CASCADE
                );
                """
                index_sql = "CREATE INDEX idx_user_promo_groups_user_id ON user_promo_groups(user_id);"
            else:  # MySQL и совместимые
                create_sql = """
                CREATE TABLE user_promo_groups (
                    user_id INT NOT NULL,
                    promo_group_id INT NOT NULL,
                    assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    assigned_by VARCHAR(50) DEFAULT 'system',
                    PRIMARY KEY (user_id, promo_group_id),
                    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY (promo_group_id) REFERENCES promo_groups(id) ON DELETE CASCADE
                );
                """
                index_sql = "CREATE INDEX idx_user_promo_groups_user_id ON user_promo_groups(user_id);"

            await conn.execute(text(create_sql))
            await conn.execute(text(index_sql))
            logger.info("✅ Таблица user_promo_groups создана с индексом")
            return True

    except Exception as error:  # pragma: no cover - logging defensive branch
        logger.error("❌ Ошибка создания таблицы user_promo_groups: %s", error)
        return False


async def migrate_existing_user_promo_groups_data() -> bool:
    """Переносит существующие связи users.promo_group_id в таблицу user_promo_groups."""
    try:
        table_exists = await check_table_exists("user_promo_groups")
        if not table_exists:
            logger.warning(
                "⚠️ Таблица user_promo_groups не существует, пропускаем миграцию данных"
            )
            return False

        column_exists = await check_column_exists("users", "promo_group_id")
        if not column_exists:
            logger.warning(
                "⚠️ Колонка users.promo_group_id не существует, пропускаем миграцию данных"
            )
            return True

        async with engine.begin() as conn:
            result = await conn.execute(text("SELECT COUNT(*) FROM user_promo_groups"))
            count = result.scalar()

            if count and count > 0:
                logger.info(
                    "ℹ️ В таблице user_promo_groups уже есть %s записей, пропускаем миграцию",
                    count,
                )
                return True

            db_type = await get_database_type()

            if db_type == "sqlite":
                migrate_sql = """
                INSERT INTO user_promo_groups (user_id, promo_group_id, assigned_at, assigned_by)
                SELECT id, promo_group_id, CURRENT_TIMESTAMP, 'system'
                FROM users
                WHERE promo_group_id IS NOT NULL
                """
            else:  # PostgreSQL и MySQL
                migrate_sql = """
                INSERT INTO user_promo_groups (user_id, promo_group_id, assigned_at, assigned_by)
                SELECT id, promo_group_id, NOW(), 'system'
                FROM users
                WHERE promo_group_id IS NOT NULL
                """

            result = await conn.execute(text(migrate_sql))
            migrated_count = getattr(result, "rowcount", 0)
            logger.info("✅ Перенесено %s связей пользователей с промогруппами", migrated_count)
            return True

    except Exception as error:  # pragma: no cover - logging defensive branch
        logger.error("❌ Ошибка миграции данных user_promo_groups: %s", error)
        return False


async def add_promocode_promo_group_column() -> bool:
    """Добавляет колонку promo_group_id в таблицу promocodes."""
    column_exists = await check_column_exists("promocodes", "promo_group_id")
    if column_exists:
        logger.info("Колонка promo_group_id уже существует в promocodes")
        return True

    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            if db_type == "sqlite":
                await conn.execute(
                    text("ALTER TABLE promocodes ADD COLUMN promo_group_id INTEGER")
                )
            elif db_type == "postgresql":
                await conn.execute(
                    text("ALTER TABLE promocodes ADD COLUMN promo_group_id INTEGER")
                )
                try:
                    await conn.execute(
                        text(
                            """
                            ALTER TABLE promocodes
                            ADD CONSTRAINT fk_promocodes_promo_group
                            FOREIGN KEY (promo_group_id)
                            REFERENCES promo_groups(id)
                            ON DELETE SET NULL
                            """
                        )
                    )
                except Exception:  # pragma: no cover - constraint may already exist
                    logger.debug("Уже существует ограничение fk_promocodes_promo_group")

                await conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS idx_promocodes_promo_group_id "
                        "ON promocodes(promo_group_id)"
                    )
                )
            else:  # MySQL и совместимые
                await conn.execute(
                    text(
                        """
                        ALTER TABLE promocodes
                        ADD COLUMN promo_group_id INT,
                        ADD CONSTRAINT fk_promocodes_promo_group
                        FOREIGN KEY (promo_group_id)
                        REFERENCES promo_groups(id)
                        ON DELETE SET NULL
                        """
                    )
                )
                await conn.execute(
                    text("CREATE INDEX idx_promocodes_promo_group_id ON promocodes(promo_group_id)")
                )

        logger.info("✅ Добавлена колонка promo_group_id в promocodes")
        return True

    except Exception as error:  # pragma: no cover - logging defensive branch
        logger.error("❌ Ошибка добавления promo_group_id в promocodes: %s", error)
        return False


async def run_universal_migration() -> bool:
    """Выполняет только актуальные миграции последних обновлений."""
    logger.info("=== НАЧАЛО УНИВЕРСАЛЬНОЙ МИГРАЦИИ (упрощённый режим) ===")

    try:
        db_type = await get_database_type()
        logger.info("Тип базы данных: %s", db_type)

        priority_ready = await add_promo_group_priority_column()
        if priority_ready:
            logger.info("✅ Колонка priority в promo_groups готова")
        else:
            logger.warning("⚠️ Проблемы с добавлением priority в promo_groups")

        user_promo_groups_ready = await create_user_promo_groups_table()
        if user_promo_groups_ready:
            logger.info("✅ Таблица user_promo_groups готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей user_promo_groups")

        data_migrated = await migrate_existing_user_promo_groups_data()
        if data_migrated:
            logger.info("✅ Данные пользователей перенесены в user_promo_groups")
        else:
            logger.warning("⚠️ Проблемы с миграцией данных user_promo_groups")

        promocode_column_ready = await add_promocode_promo_group_column()
        if promocode_column_ready:
            logger.info("✅ Колонка promo_group_id в promocodes готова")
        else:
            logger.warning("⚠️ Проблемы с добавлением promo_group_id в promocodes")

        logger.info("=== МИГРАЦИЯ ЗАВЕРШЕНА ===")
        return True

    except Exception as error:  # pragma: no cover - logging defensive branch
        logger.error("=== ОШИБКА ВЫПОЛНЕНИЯ МИГРАЦИИ: %s ===", error)
        return False
