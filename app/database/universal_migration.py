import logging
from datetime import datetime

from sqlalchemy import select, text

from app.config import settings
from app.database.database import AsyncSessionLocal, engine
from app.database.models import WebApiToken
from app.utils.security import hash_api_token


logger = logging.getLogger(__name__)


async def get_database_type() -> str:
    return engine.dialect.name


async def check_table_exists(table_name: str) -> bool:
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            if db_type == "sqlite":
                result = await conn.execute(
                    text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table_name"),
                    {"table_name": table_name},
                )
                return result.fetchone() is not None

            if db_type == "postgresql":
                result = await conn.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = :table_name
                        """
                    ),
                    {"table_name": table_name},
                )
                return result.fetchone() is not None

            if db_type == "mysql":
                result = await conn.execute(
                    text(
                        """
                        SELECT table_name
                        FROM information_schema.tables
                        WHERE table_schema = DATABASE() AND table_name = :table_name
                        """
                    ),
                    {"table_name": table_name},
                )
                return result.fetchone() is not None
    except Exception as error:
        logger.error("Ошибка проверки существования таблицы %s: %s", table_name, error)

    return False


async def check_column_exists(table_name: str, column_name: str) -> bool:
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            if db_type == "sqlite":
                result = await conn.execute(text(f"PRAGMA table_info({table_name})"))
                return any(row[1] == column_name for row in result.fetchall())

            if db_type == "postgresql":
                result = await conn.execute(
                    text(
                        """
                        SELECT column_name
                        FROM information_schema.columns
                        WHERE table_name = :table_name AND column_name = :column_name
                        """
                    ),
                    {"table_name": table_name, "column_name": column_name},
                )
                return result.fetchone() is not None

            if db_type == "mysql":
                result = await conn.execute(
                    text(
                        """
                        SELECT COLUMN_NAME
                        FROM information_schema.COLUMNS
                        WHERE TABLE_NAME = :table_name AND COLUMN_NAME = :column_name
                        """
                    ),
                    {"table_name": table_name, "column_name": column_name},
                )
                return result.fetchone() is not None
    except Exception as error:
        logger.error(
            "Ошибка проверки существования колонки %s.%s: %s",
            table_name,
            column_name,
            error,
        )

    return False


async def check_constraint_exists(table_name: str, constraint_name: str) -> bool:
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            if db_type == "postgresql":
                result = await conn.execute(
                    text(
                        """
                        SELECT 1
                        FROM information_schema.table_constraints
                        WHERE table_schema = 'public'
                          AND table_name = :table_name
                          AND constraint_name = :constraint_name
                        """
                    ),
                    {"table_name": table_name, "constraint_name": constraint_name},
                )
                return result.fetchone() is not None

            if db_type == "mysql":
                result = await conn.execute(
                    text(
                        """
                        SELECT 1
                        FROM information_schema.table_constraints
                        WHERE table_schema = DATABASE()
                          AND table_name = :table_name
                          AND constraint_name = :constraint_name
                        """
                    ),
                    {"table_name": table_name, "constraint_name": constraint_name},
                )
                return result.fetchone() is not None

            if db_type == "sqlite":
                result = await conn.execute(text(f"PRAGMA foreign_key_list({table_name})"))
                return any(row[5] == constraint_name for row in result.fetchall())
    except Exception as error:
        logger.error(
            "Ошибка проверки существования ограничения %s для %s: %s",
            constraint_name,
            table_name,
            error,
        )

    return False


async def ensure_default_web_api_token() -> bool:
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

    except Exception as error:
        logger.error("❌ Ошибка создания дефолтного веб-API токена: %s", error)
        return False


async def add_subscription_crypto_link_column() -> bool:
    column_exists = await check_column_exists("subscriptions", "subscription_crypto_link")
    if column_exists:
        logger.info("Колонка subscription_crypto_link уже существует")
        return True

    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            if db_type == "sqlite":
                await conn.execute(
                    text(
                        "ALTER TABLE subscriptions ADD COLUMN subscription_crypto_link TEXT NULL"
                    )
                )
            elif db_type == "postgresql":
                await conn.execute(
                    text(
                        "ALTER TABLE subscriptions ADD COLUMN subscription_crypto_link TEXT NULL"
                    )
                )
            elif db_type == "mysql":
                await conn.execute(
                    text(
                        "ALTER TABLE subscriptions ADD COLUMN subscription_crypto_link VARCHAR(255) NULL"
                    )
                )
            else:
                logger.warning("Неподдерживаемый тип БД для добавления crypto link: %s", db_type)
                return False

        logger.info("✅ Колонка subscription_crypto_link добавлена")
        return True
    except Exception as error:
        logger.error("❌ Ошибка добавления subscription_crypto_link: %s", error)
        return False


async def ensure_support_audit_logs_table() -> bool:
    try:
        async with engine.begin() as conn:
            if await check_table_exists("support_audit_logs"):
                logger.info("ℹ️ Таблица support_audit_logs уже существует")
                return True

            db_type = await get_database_type()

            if db_type == "sqlite":
                create_sql = """
                CREATE TABLE support_audit_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor_user_id INTEGER NULL,
                    actor_telegram_id BIGINT NOT NULL,
                    is_moderator BOOLEAN NOT NULL DEFAULT 0,
                    action VARCHAR(50) NOT NULL,
                    ticket_id INTEGER NULL,
                    target_user_id INTEGER NULL,
                    details JSON NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (actor_user_id) REFERENCES users(id),
                    FOREIGN KEY (ticket_id) REFERENCES tickets(id),
                    FOREIGN KEY (target_user_id) REFERENCES users(id)
                );
                CREATE INDEX idx_support_audit_logs_ticket ON support_audit_logs(ticket_id);
                CREATE INDEX idx_support_audit_logs_actor ON support_audit_logs(actor_telegram_id);
                CREATE INDEX idx_support_audit_logs_action ON support_audit_logs(action);
                """
            elif db_type == "postgresql":
                create_sql = """
                CREATE TABLE support_audit_logs (
                    id SERIAL PRIMARY KEY,
                    actor_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    actor_telegram_id BIGINT NOT NULL,
                    is_moderator BOOLEAN NOT NULL DEFAULT FALSE,
                    action VARCHAR(50) NOT NULL,
                    ticket_id INTEGER NULL REFERENCES tickets(id) ON DELETE SET NULL,
                    target_user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                    details JSON NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                );
                CREATE INDEX idx_support_audit_logs_ticket ON support_audit_logs(ticket_id);
                CREATE INDEX idx_support_audit_logs_actor ON support_audit_logs(actor_telegram_id);
                CREATE INDEX idx_support_audit_logs_action ON support_audit_logs(action);
                """
            else:
                create_sql = """
                CREATE TABLE support_audit_logs (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    actor_user_id INT NULL,
                    actor_telegram_id BIGINT NOT NULL,
                    is_moderator BOOLEAN NOT NULL DEFAULT 0,
                    action VARCHAR(50) NOT NULL,
                    ticket_id INT NULL,
                    target_user_id INT NULL,
                    details JSON NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX idx_support_audit_logs_ticket ON support_audit_logs(ticket_id);
                CREATE INDEX idx_support_audit_logs_actor ON support_audit_logs(actor_telegram_id);
                CREATE INDEX idx_support_audit_logs_action ON support_audit_logs(action);
                """

            await conn.execute(text(create_sql))

        logger.info("✅ Таблица support_audit_logs создана")
        return True
    except Exception as error:
        logger.error("❌ Ошибка создания таблицы support_audit_logs: %s", error)
        return False


async def ensure_promo_groups_setup() -> bool:
    logger.info("=== НАСТРОЙКА ПРОМО ГРУПП ===")

    try:
        promo_table_exists = await check_table_exists("promo_groups")

        async with engine.begin() as conn:
            db_type = await get_database_type()

            if not promo_table_exists:
                if db_type == "sqlite":
                    await conn.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS promo_groups (
                                id INTEGER PRIMARY KEY AUTOINCREMENT,
                                name VARCHAR(255) NOT NULL,
                                server_discount_percent INTEGER NOT NULL DEFAULT 0,
                                traffic_discount_percent INTEGER NOT NULL DEFAULT 0,
                                device_discount_percent INTEGER NOT NULL DEFAULT 0,
                                is_default BOOLEAN NOT NULL DEFAULT 0,
                                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                            )
                            """
                        )
                    )
                    await conn.execute(
                        text(
                            "CREATE UNIQUE INDEX IF NOT EXISTS uq_promo_groups_name ON promo_groups(name)"
                        )
                    )
                elif db_type == "postgresql":
                    await conn.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS promo_groups (
                                id SERIAL PRIMARY KEY,
                                name VARCHAR(255) NOT NULL,
                                server_discount_percent INTEGER NOT NULL DEFAULT 0,
                                traffic_discount_percent INTEGER NOT NULL DEFAULT 0,
                                device_discount_percent INTEGER NOT NULL DEFAULT 0,
                                is_default BOOLEAN NOT NULL DEFAULT FALSE,
                                created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                                updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                                CONSTRAINT uq_promo_groups_name UNIQUE (name)
                            )
                            """
                        )
                    )
                else:
                    await conn.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS promo_groups (
                                id INT AUTO_INCREMENT PRIMARY KEY,
                                name VARCHAR(255) NOT NULL,
                                server_discount_percent INT NOT NULL DEFAULT 0,
                                traffic_discount_percent INT NOT NULL DEFAULT 0,
                                device_discount_percent INT NOT NULL DEFAULT 0,
                                is_default TINYINT(1) NOT NULL DEFAULT 0,
                                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                                UNIQUE KEY uq_promo_groups_name (name)
                            ) ENGINE=InnoDB
                            """
                        )
                    )

                logger.info("Создана таблица promo_groups")

            if db_type == "postgresql" and not await check_constraint_exists(
                "promo_groups", "uq_promo_groups_name"
            ):
                try:
                    await conn.execute(
                        text(
                            "ALTER TABLE promo_groups ADD CONSTRAINT uq_promo_groups_name UNIQUE (name)"
                        )
                    )
                except Exception as error:
                    logger.warning(
                        "Не удалось добавить уникальное ограничение uq_promo_groups_name: %s",
                        error,
                    )

            period_discounts_column_exists = await check_column_exists(
                "promo_groups", "period_discounts"
            )

            if not period_discounts_column_exists:
                if db_type == "sqlite":
                    await conn.execute(
                        text("ALTER TABLE promo_groups ADD COLUMN period_discounts JSON")
                    )
                    await conn.execute(
                        text(
                            "UPDATE promo_groups SET period_discounts = '{}' WHERE period_discounts IS NULL"
                        )
                    )
                elif db_type == "postgresql":
                    await conn.execute(
                        text("ALTER TABLE promo_groups ADD COLUMN period_discounts JSONB")
                    )
                    await conn.execute(
                        text(
                            "UPDATE promo_groups SET period_discounts = '{}'::jsonb WHERE period_discounts IS NULL"
                        )
                    )
                else:
                    await conn.execute(
                        text("ALTER TABLE promo_groups ADD COLUMN period_discounts JSON")
                    )
                    await conn.execute(
                        text(
                            "UPDATE promo_groups SET period_discounts = JSON_OBJECT() WHERE period_discounts IS NULL"
                        )
                    )

                logger.info("Добавлена колонка promo_groups.period_discounts")

            auto_assign_column_exists = await check_column_exists(
                "promo_groups", "auto_assign_total_spent_kopeks"
            )

            if not auto_assign_column_exists:
                column_def = "INTEGER DEFAULT 0" if db_type != "mysql" else "INT DEFAULT 0"
                await conn.execute(
                    text(
                        f"ALTER TABLE promo_groups ADD COLUMN auto_assign_total_spent_kopeks {column_def}"
                    )
                )
                logger.info(
                    "Добавлена колонка promo_groups.auto_assign_total_spent_kopeks"
                )

            addon_discount_column_exists = await check_column_exists(
                "promo_groups", "apply_discounts_to_addons"
            )

            if not addon_discount_column_exists:
                column_def = (
                    "BOOLEAN NOT NULL DEFAULT 0"
                    if db_type == "sqlite"
                    else "BOOLEAN NOT NULL DEFAULT FALSE"
                )
                if db_type == "mysql":
                    column_def = "TINYINT(1) NOT NULL DEFAULT 0"

                await conn.execute(
                    text(
                        f"ALTER TABLE promo_groups ADD COLUMN apply_discounts_to_addons {column_def}"
                    )
                )
                logger.info(
                    "Добавлена колонка promo_groups.apply_discounts_to_addons"
                )

        return True
    except Exception as error:
        logger.error("Ошибка настройки промогрупп: %s", error)
        return False


async def ensure_server_promo_groups_setup() -> bool:
    logger.info("=== НАСТРОЙКА ДОСТУПА СЕРВЕРОВ К ПРОМОГРУППАМ ===")

    try:
        table_exists = await check_table_exists("server_squad_promo_groups")

        async with engine.begin() as conn:
            db_type = await get_database_type()

            if not table_exists:
                if db_type == "sqlite":
                    create_table_sql = """
                    CREATE TABLE server_squad_promo_groups (
                        server_squad_id INTEGER NOT NULL,
                        promo_group_id INTEGER NOT NULL,
                        PRIMARY KEY (server_squad_id, promo_group_id),
                        FOREIGN KEY (server_squad_id) REFERENCES server_squads(id) ON DELETE CASCADE,
                        FOREIGN KEY (promo_group_id) REFERENCES promo_groups(id) ON DELETE CASCADE
                    );
                    """
                elif db_type == "postgresql":
                    create_table_sql = """
                    CREATE TABLE server_squad_promo_groups (
                        server_squad_id INTEGER NOT NULL REFERENCES server_squads(id) ON DELETE CASCADE,
                        promo_group_id INTEGER NOT NULL REFERENCES promo_groups(id) ON DELETE CASCADE,
                        PRIMARY KEY (server_squad_id, promo_group_id)
                    );
                    """
                else:
                    create_table_sql = """
                    CREATE TABLE server_squad_promo_groups (
                        server_squad_id INT NOT NULL,
                        promo_group_id INT NOT NULL,
                        PRIMARY KEY (server_squad_id, promo_group_id),
                        FOREIGN KEY (server_squad_id) REFERENCES server_squads(id) ON DELETE CASCADE,
                        FOREIGN KEY (promo_group_id) REFERENCES promo_groups(id) ON DELETE CASCADE
                    );
                    """

                await conn.execute(text(create_table_sql))
                await conn.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS idx_server_squad_promo_groups_promo "
                        "ON server_squad_promo_groups(promo_group_id)"
                    )
                )
                logger.info("✅ Таблица server_squad_promo_groups создана")
            else:
                logger.info("ℹ️ Таблица server_squad_promo_groups уже существует")

            default_query = (
                "SELECT id FROM promo_groups WHERE is_default IS TRUE LIMIT 1"
                if db_type == "postgresql"
                else "SELECT id FROM promo_groups WHERE is_default = 1 LIMIT 1"
            )
            default_result = await conn.execute(text(default_query))
            default_row = default_result.fetchone()

            if not default_row:
                logger.warning("⚠️ Не найдена базовая промогруппа для назначения серверам")
                return True

            default_group_id = default_row[0]

            servers_result = await conn.execute(text("SELECT id FROM server_squads"))
            server_ids = [row[0] for row in servers_result.fetchall()]

            assigned_count = 0
            for server_id in server_ids:
                existing = await conn.execute(
                    text(
                        "SELECT 1 FROM server_squad_promo_groups "
                        "WHERE server_squad_id = :sid LIMIT 1"
                    ),
                    {"sid": server_id},
                )
                if existing.fetchone():
                    continue

                await conn.execute(
                    text(
                        "INSERT INTO server_squad_promo_groups (server_squad_id, promo_group_id) "
                        "VALUES (:sid, :gid)"
                    ),
                    {"sid": server_id, "gid": default_group_id},
                )
                assigned_count += 1

            if assigned_count:
                logger.info(
                    "✅ Базовая промогруппа назначена %s серверам",
                    assigned_count,
                )
            else:
                logger.info("ℹ️ Все серверы уже имеют назначенные промогруппы")

        return True
    except Exception as error:
        logger.error("Ошибка настройки server_squad_promo_groups: %s", error)
        return False


async def fix_foreign_keys_for_user_deletion() -> bool:
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            if db_type == "postgresql":
                try:
                    await conn.execute(
                        text(
                            """
                            ALTER TABLE user_messages
                            DROP CONSTRAINT IF EXISTS user_messages_created_by_fkey;
                            """
                        )
                    )
                    await conn.execute(
                        text(
                            """
                            ALTER TABLE user_messages
                            ADD CONSTRAINT user_messages_created_by_fkey
                            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL;
                            """
                        )
                    )
                    logger.info("Обновлен внешний ключ user_messages.created_by")
                except Exception as error:
                    logger.warning("Ошибка обновления FK user_messages: %s", error)

                try:
                    await conn.execute(
                        text(
                            """
                            ALTER TABLE promocodes
                            DROP CONSTRAINT IF EXISTS promocodes_created_by_fkey;
                            """
                        )
                    )
                    await conn.execute(
                        text(
                            """
                            ALTER TABLE promocodes
                            ADD CONSTRAINT promocodes_created_by_fkey
                            FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL;
                            """
                        )
                    )
                    logger.info("Обновлен внешний ключ promocodes.created_by")
                except Exception as error:
                    logger.warning("Ошибка обновления FK promocodes: %s", error)

            logger.info("Внешние ключи обновлены для безопасного удаления пользователей")
            return True
    except Exception as error:
        logger.error("Ошибка обновления внешних ключей: %s", error)
        return False


async def create_subscription_conversions_table() -> bool:
    table_exists = await check_table_exists("subscription_conversions")
    if table_exists:
        logger.info("Таблица subscription_conversions уже существует")
        return True

    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            if db_type == "sqlite":
                create_sql = """
                CREATE TABLE subscription_conversions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    converted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    trial_duration_days INTEGER NULL,
                    payment_method VARCHAR(50) NULL,
                    first_payment_amount_kopeks INTEGER NULL,
                    first_paid_period_days INTEGER NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );
                CREATE INDEX idx_subscription_conversions_user_id ON subscription_conversions(user_id);
                CREATE INDEX idx_subscription_conversions_converted_at ON subscription_conversions(converted_at);
                """
            elif db_type == "postgresql":
                create_sql = """
                CREATE TABLE subscription_conversions (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    converted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    trial_duration_days INTEGER NULL,
                    payment_method VARCHAR(50) NULL,
                    first_payment_amount_kopeks INTEGER NULL,
                    first_paid_period_days INTEGER NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );
                CREATE INDEX idx_subscription_conversions_user_id ON subscription_conversions(user_id);
                CREATE INDEX idx_subscription_conversions_converted_at ON subscription_conversions(converted_at);
                """
            else:
                create_sql = """
                CREATE TABLE subscription_conversions (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    user_id INT NOT NULL,
                    converted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    trial_duration_days INT NULL,
                    payment_method VARCHAR(50) NULL,
                    first_payment_amount_kopeks INT NULL,
                    first_paid_period_days INT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                );
                CREATE INDEX idx_subscription_conversions_user_id ON subscription_conversions(user_id);
                CREATE INDEX idx_subscription_conversions_converted_at ON subscription_conversions(converted_at);
                """

            await conn.execute(text(create_sql))

        logger.info("✅ Таблица subscription_conversions успешно создана")
        return True
    except Exception as error:
        logger.error("Ошибка создания таблицы subscription_conversions: %s", error)
        return False


async def fix_subscription_duplicates_universal() -> bool:
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()
            result = await conn.execute(
                text(
                    """
                    SELECT user_id, COUNT(*) as count
                    FROM subscriptions
                    GROUP BY user_id
                    HAVING COUNT(*) > 1
                    """
                )
            )
            duplicates = result.fetchall()

            if not duplicates:
                logger.info("Дублирующихся подписок не найдено")
                return True

            logger.info(
                "Найдено %s пользователей с дублирующимися подписками",
                len(duplicates),
            )

            for user_id_row, _ in duplicates:
                if db_type == "sqlite":
                    delete_sql = text(
                        """
                        DELETE FROM subscriptions
                        WHERE user_id = :user_id AND id NOT IN (
                            SELECT MAX(id)
                            FROM subscriptions
                            WHERE user_id = :user_id
                        )
                        """
                    )
                else:
                    delete_sql = text(
                        """
                        DELETE FROM subscriptions
                        WHERE user_id = :user_id AND id NOT IN (
                            SELECT max_id FROM (
                                SELECT MAX(id) as max_id
                                FROM subscriptions
                                WHERE user_id = :user_id
                            ) as subquery
                        )
                        """
                    )

                await conn.execute(delete_sql, {"user_id": user_id_row})

            logger.info("Дубликаты подписок очищены")
            return True
    except Exception as error:
        logger.error("Ошибка при очистке дублирующихся подписок: %s", error)
        return False


async def run_universal_migration() -> bool:
    logger.info("=== НАЧАЛО УНИВЕРСАЛЬНОЙ МИГРАЦИИ ===")

    try:
        db_type = await get_database_type()
        logger.info("Тип базы данных: %s", db_type)

        logger.info("=== ДОБАВЛЕНИЕ КОЛОНКИ CRYPTO LINK ДЛЯ ПОДПИСОК ===")
        crypto_link_added = await add_subscription_crypto_link_column()
        if crypto_link_added:
            logger.info("✅ Колонка subscription_crypto_link готова")
        else:
            logger.warning("⚠️ Проблемы с добавлением subscription_crypto_link")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ АУДИТА ПОДДЕРЖКИ ===")
        support_audit_ready = await ensure_support_audit_logs_table()
        if support_audit_ready:
            logger.info("✅ Таблица support_audit_logs готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей support_audit_logs")

        logger.info("=== НАСТРОЙКА ПРОМО ГРУПП ===")
        promo_groups_ready = await ensure_promo_groups_setup()
        if promo_groups_ready:
            logger.info("✅ Промогруппы приведены к актуальному состоянию")
        else:
            logger.warning("⚠️ Проблемы с настройкой промогрупп")

        server_promo_groups_ready = await ensure_server_promo_groups_setup()
        if server_promo_groups_ready:
            logger.info("✅ Доступ серверов по промогруппам настроен")
        else:
            logger.warning("⚠️ Проблемы с настройкой доступа серверов к промогруппам")

        logger.info("=== ОБНОВЛЕНИЕ ВНЕШНИХ КЛЮЧЕЙ ===")
        fk_updated = await fix_foreign_keys_for_user_deletion()
        if fk_updated:
            logger.info("✅ Внешние ключи обновлены")
        else:
            logger.warning("⚠️ Проблемы с обновлением внешних ключей")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ КОНВЕРСИЙ ПОДПИСОК ===")
        conversions_created = await create_subscription_conversions_table()
        if conversions_created:
            logger.info("✅ Таблица subscription_conversions готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей subscription_conversions")

        duplicates_fixed = await fix_subscription_duplicates_universal()
        if duplicates_fixed:
            logger.info("✅ Дубликаты подписок устранены")
        else:
            logger.warning("⚠️ Не удалось устранить дубликаты подписок")

        logger.info("=== МИГРАЦИЯ ЗАВЕРШЕНА УСПЕШНО ===")
        return True
    except Exception as error:
        logger.error("=== ОШИБКА ВЫПОЛНЕНИЯ МИГРАЦИИ: %s ===", error)
        return False
