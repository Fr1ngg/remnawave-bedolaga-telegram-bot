import logging
from datetime import datetime
from typing import List, Tuple

from sqlalchemy import inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.database import AsyncSessionLocal, engine
from app.database.models import WebApiToken
from app.utils.security import hash_api_token

logger = logging.getLogger(__name__)


async def get_database_type():
    return engine.dialect.name


async def table_exists(session: AsyncSession, table_name: str) -> bool:
    """Проверяет существование таблицы в текущей базе данных."""

    db_type = await get_database_type()

    if db_type == "sqlite":
        result = await session.execute(
            text(
                """
                SELECT name
                FROM sqlite_master
                WHERE type = 'table' AND name = :table_name
                """
            ),
            {"table_name": table_name},
        )
        return result.fetchone() is not None

    if db_type == "postgresql":
        result = await session.execute(
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
        result = await session.execute(
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

    return False


async def column_exists(
    session: AsyncSession, table_name: str, column_name: str
) -> bool:
    """Проверяет существование колонки в таблице."""

    db_type = await get_database_type()

    if db_type == "sqlite":
        result = await session.execute(text(f"PRAGMA table_info({table_name})"))
        columns = result.fetchall()
        return any(col[1] == column_name for col in columns)

    if db_type == "postgresql":
        result = await session.execute(
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
        result = await session.execute(
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

    return False


def is_object_exists_error(error: Exception) -> bool:
    message = str(error).lower()
    known_markers = (
        "already exists",
        "duplicate",
        "exists",
        "already defined",
    )
    return any(marker in message for marker in known_markers)


async def execute_ddl_statements(statements: List[dict]) -> bool:
    """Выполняет DDL запросы последовательно с отдельными транзакциями."""

    for statement in statements:
        sql = statement.get("sql", "").strip().rstrip(";")
        if not sql:
            continue

        success_message = statement.get("success") or f"✅ Запрос выполнен: {sql.splitlines()[0]}"
        exists_message = statement.get("exists") or "ℹ️ Объект уже существует, пропускаем"
        error_message = statement.get("error") or "❌ Ошибка выполнения DDL-запроса"

        try:
            async with engine.begin() as conn:
                await conn.execute(text(sql))
            logger.info(success_message)
        except Exception as error:
            if is_object_exists_error(error):
                logger.info(exists_message)
                continue

            logger.error("%s: %s", error_message, error)
            return False

    return True


async def ensure_required_tables(
    session: AsyncSession, target_table: str, required_tables: List[str]
) -> bool:
    """Проверяет существование необходимых родительских таблиц."""

    for required_table in required_tables:
        if not await table_exists(session, required_table):
            logger.warning(
                "⚠️ Таблица %s не существует, пропускаем создание %s",
                required_table,
                target_table,
            )
            return False

    return True


def make_table_statement(sql: str, table_name: str) -> dict:
    return {
        "sql": sql,
        "success": f"✅ Таблица {table_name} создана",
        "exists": f"ℹ️ Таблица {table_name} уже существует",
        "error": f"❌ Ошибка создания таблицы {table_name}",
    }


def make_index_statement(index_name: str, sql: str) -> dict:
    return {
        "sql": sql,
        "success": f"✅ Создан индекс {index_name}",
        "exists": f"ℹ️ Индекс {index_name} уже существует",
        "error": f"❌ Ошибка создания индекса {index_name}",
    }


async def create_table_from_definition(
    table_name: str,
    definition: dict,
) -> bool:
    logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ %s ===", table_name.upper())

    async with AsyncSessionLocal() as session:
        required_tables = definition.get("required_tables") or []
        if required_tables and not await ensure_required_tables(
            session, table_name, required_tables
        ):
            return False

        if await table_exists(session, table_name):
            logger.info("ℹ️ Таблица %s уже существует", table_name)
            return True

    db_type = await get_database_type()
    statements_by_db = definition.get("statements", {})
    statements = statements_by_db.get(db_type)

    if statements is None:
        logger.error(
            "Неподдерживаемый тип БД %s для создания таблицы %s", db_type, table_name
        )
        return False

    if not await execute_ddl_statements(statements):
        return False

    logger.info("✅ Таблица %s готова", table_name)
    return True


def build_statements_from_sql(raw_sql: str, table_name: str) -> List[dict]:
    statements: List[dict] = []
    for part in raw_sql.strip().split(";"):
        statement = part.strip()
        if not statement:
            continue

        upper_statement = statement.upper()

        if upper_statement.startswith("CREATE TABLE"):
            statements.append(make_table_statement(statement, table_name))
            continue

        if upper_statement.startswith("CREATE UNIQUE INDEX"):
            tokens = statement.split()
            index_name = tokens[3] if len(tokens) > 3 else "index"
            statements.append(make_index_statement(index_name, statement))
            continue

        if upper_statement.startswith("CREATE INDEX"):
            tokens = statement.split()
            index_name = tokens[2] if len(tokens) > 2 else "index"
            statements.append(make_index_statement(index_name, statement))
            continue

        statements.append({"sql": statement})

    return statements


CRYPTOBOT_PAYMENTS_DEFINITION = {
    "required_tables": ["users", "transactions"],
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE cryptobot_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                invoice_id VARCHAR(255) UNIQUE NOT NULL,
                amount VARCHAR(50) NOT NULL,
                asset VARCHAR(10) NOT NULL,
                status VARCHAR(50) NOT NULL,
                description TEXT NULL,
                payload TEXT NULL,
                bot_invoice_url TEXT NULL,
                mini_app_invoice_url TEXT NULL,
                web_app_invoice_url TEXT NULL,
                paid_at DATETIME NULL,
                transaction_id INTEGER NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (transaction_id) REFERENCES transactions(id)
            );

            CREATE INDEX idx_cryptobot_payments_user_id ON cryptobot_payments(user_id);
            CREATE INDEX idx_cryptobot_payments_invoice_id ON cryptobot_payments(invoice_id);
            CREATE INDEX idx_cryptobot_payments_status ON cryptobot_payments(status);
            """,
            "cryptobot_payments",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE cryptobot_payments (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL,
                invoice_id VARCHAR(255) UNIQUE NOT NULL,
                amount VARCHAR(50) NOT NULL,
                asset VARCHAR(10) NOT NULL,
                status VARCHAR(50) NOT NULL,
                description TEXT NULL,
                payload TEXT NULL,
                bot_invoice_url TEXT NULL,
                mini_app_invoice_url TEXT NULL,
                web_app_invoice_url TEXT NULL,
                paid_at TIMESTAMP NULL,
                transaction_id INTEGER NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (transaction_id) REFERENCES transactions(id)
            );

            CREATE INDEX idx_cryptobot_payments_user_id ON cryptobot_payments(user_id);
            CREATE INDEX idx_cryptobot_payments_invoice_id ON cryptobot_payments(invoice_id);
            CREATE INDEX idx_cryptobot_payments_status ON cryptobot_payments(status);
            """,
            "cryptobot_payments",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE cryptobot_payments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                invoice_id VARCHAR(255) UNIQUE NOT NULL,
                amount VARCHAR(50) NOT NULL,
                asset VARCHAR(10) NOT NULL,
                status VARCHAR(50) NOT NULL,
                description TEXT NULL,
                payload TEXT NULL,
                bot_invoice_url TEXT NULL,
                mini_app_invoice_url TEXT NULL,
                web_app_invoice_url TEXT NULL,
                paid_at DATETIME NULL,
                transaction_id INT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (transaction_id) REFERENCES transactions(id)
            );

            CREATE INDEX idx_cryptobot_payments_user_id ON cryptobot_payments(user_id);
            CREATE INDEX idx_cryptobot_payments_invoice_id ON cryptobot_payments(invoice_id);
            CREATE INDEX idx_cryptobot_payments_status ON cryptobot_payments(status);
            """,
            "cryptobot_payments",
        ),
    },
}


HELEKET_PAYMENTS_DEFINITION = {
    "required_tables": ["users", "transactions"],
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE heleket_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                uuid VARCHAR(255) UNIQUE NOT NULL,
                order_id VARCHAR(128) UNIQUE NOT NULL,
                amount VARCHAR(50) NOT NULL,
                currency VARCHAR(10) NOT NULL,
                payer_amount VARCHAR(50) NULL,
                payer_currency VARCHAR(10) NULL,
                exchange_rate DOUBLE PRECISION NULL,
                discount_percent INTEGER NULL,
                status VARCHAR(50) NOT NULL,
                payment_url TEXT NULL,
                metadata_json JSON NULL,
                paid_at DATETIME NULL,
                expires_at DATETIME NULL,
                transaction_id INTEGER NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (transaction_id) REFERENCES transactions(id)
            );

            CREATE INDEX idx_heleket_payments_user_id ON heleket_payments(user_id);
            CREATE INDEX idx_heleket_payments_uuid ON heleket_payments(uuid);
            CREATE INDEX idx_heleket_payments_order_id ON heleket_payments(order_id);
            CREATE INDEX idx_heleket_payments_status ON heleket_payments(status);
            """,
            "heleket_payments",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE heleket_payments (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                uuid VARCHAR(255) UNIQUE NOT NULL,
                order_id VARCHAR(128) UNIQUE NOT NULL,
                amount VARCHAR(50) NOT NULL,
                currency VARCHAR(10) NOT NULL,
                payer_amount VARCHAR(50) NULL,
                payer_currency VARCHAR(10) NULL,
                exchange_rate DOUBLE PRECISION NULL,
                discount_percent INTEGER NULL,
                status VARCHAR(50) NOT NULL,
                payment_url TEXT NULL,
                metadata_json JSON NULL,
                paid_at TIMESTAMP NULL,
                expires_at TIMESTAMP NULL,
                transaction_id INTEGER NULL REFERENCES transactions(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX idx_heleket_payments_user_id ON heleket_payments(user_id);
            CREATE INDEX idx_heleket_payments_uuid ON heleket_payments(uuid);
            CREATE INDEX idx_heleket_payments_order_id ON heleket_payments(order_id);
            CREATE INDEX idx_heleket_payments_status ON heleket_payments(status);
            """,
            "heleket_payments",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE heleket_payments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                uuid VARCHAR(255) UNIQUE NOT NULL,
                order_id VARCHAR(128) UNIQUE NOT NULL,
                amount VARCHAR(50) NOT NULL,
                currency VARCHAR(10) NOT NULL,
                payer_amount VARCHAR(50) NULL,
                payer_currency VARCHAR(10) NULL,
                exchange_rate DOUBLE NULL,
                discount_percent INT NULL,
                status VARCHAR(50) NOT NULL,
                payment_url TEXT NULL,
                metadata_json JSON NULL,
                paid_at DATETIME NULL,
                expires_at DATETIME NULL,
                transaction_id INT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (transaction_id) REFERENCES transactions(id)
            );

            CREATE INDEX idx_heleket_payments_user_id ON heleket_payments(user_id);
            CREATE INDEX idx_heleket_payments_uuid ON heleket_payments(uuid);
            CREATE INDEX idx_heleket_payments_order_id ON heleket_payments(order_id);
            CREATE INDEX idx_heleket_payments_status ON heleket_payments(status);
            """,
            "heleket_payments",
        ),
    },
}


MULENPAY_PAYMENTS_DEFINITION = {
    "required_tables": ["users", "transactions"],
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE mulenpay_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                mulen_payment_id INTEGER NULL,
                uuid VARCHAR(255) NOT NULL UNIQUE,
                amount_kopeks INTEGER NOT NULL,
                currency VARCHAR(10) NOT NULL DEFAULT 'RUB',
                description TEXT NULL,
                status VARCHAR(50) NOT NULL DEFAULT 'created',
                is_paid BOOLEAN DEFAULT 0,
                paid_at DATETIME NULL,
                payment_url TEXT NULL,
                metadata_json JSON NULL,
                callback_payload JSON NULL,
                transaction_id INTEGER NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (transaction_id) REFERENCES transactions(id)
            );

            CREATE INDEX idx_mulenpay_uuid ON mulenpay_payments(uuid);
            CREATE INDEX idx_mulenpay_payment_id ON mulenpay_payments(mulen_payment_id);
            """,
            "mulenpay_payments",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE mulenpay_payments (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                mulen_payment_id INTEGER NULL,
                uuid VARCHAR(255) NOT NULL UNIQUE,
                amount_kopeks INTEGER NOT NULL,
                currency VARCHAR(10) NOT NULL DEFAULT 'RUB',
                description TEXT NULL,
                status VARCHAR(50) NOT NULL DEFAULT 'created',
                is_paid BOOLEAN NOT NULL DEFAULT FALSE,
                paid_at TIMESTAMP NULL,
                payment_url TEXT NULL,
                metadata_json JSON NULL,
                callback_payload JSON NULL,
                transaction_id INTEGER NULL REFERENCES transactions(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX idx_mulenpay_uuid ON mulenpay_payments(uuid);
            CREATE INDEX idx_mulenpay_payment_id ON mulenpay_payments(mulen_payment_id);
            """,
            "mulenpay_payments",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE mulenpay_payments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                mulen_payment_id INT NULL,
                uuid VARCHAR(255) NOT NULL UNIQUE,
                amount_kopeks INT NOT NULL,
                currency VARCHAR(10) NOT NULL DEFAULT 'RUB',
                description TEXT NULL,
                status VARCHAR(50) NOT NULL DEFAULT 'created',
                is_paid BOOLEAN NOT NULL DEFAULT 0,
                paid_at DATETIME NULL,
                payment_url TEXT NULL,
                metadata_json JSON NULL,
                callback_payload JSON NULL,
                transaction_id INT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (transaction_id) REFERENCES transactions(id)
            );

            CREATE INDEX idx_mulenpay_uuid ON mulenpay_payments(uuid);
            CREATE INDEX idx_mulenpay_payment_id ON mulenpay_payments(mulen_payment_id);
            """,
            "mulenpay_payments",
        ),
    },
}


PAL24_PAYMENTS_DEFINITION = {
    "required_tables": ["users", "transactions"],
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE pal24_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                invoice_id VARCHAR(64) UNIQUE NOT NULL,
                amount_kopeks INTEGER NOT NULL,
                currency VARCHAR(10) NOT NULL,
                status VARCHAR(50) NOT NULL,
                payment_url TEXT NULL,
                metadata_json JSON NULL,
                paid_at DATETIME NULL,
                transaction_id INTEGER NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (transaction_id) REFERENCES transactions(id)
            );

            CREATE INDEX idx_pal24_payments_user_id ON pal24_payments(user_id);
            CREATE INDEX idx_pal24_payments_invoice_id ON pal24_payments(invoice_id);
            CREATE INDEX idx_pal24_payments_status ON pal24_payments(status);
            """,
            "pal24_payments",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE pal24_payments (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                invoice_id VARCHAR(64) UNIQUE NOT NULL,
                amount_kopeks INTEGER NOT NULL,
                currency VARCHAR(10) NOT NULL,
                status VARCHAR(50) NOT NULL,
                payment_url TEXT NULL,
                metadata_json JSON NULL,
                paid_at TIMESTAMP NULL,
                transaction_id INTEGER NULL REFERENCES transactions(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX idx_pal24_payments_user_id ON pal24_payments(user_id);
            CREATE INDEX idx_pal24_payments_invoice_id ON pal24_payments(invoice_id);
            CREATE INDEX idx_pal24_payments_status ON pal24_payments(status);
            """,
            "pal24_payments",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE pal24_payments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                invoice_id VARCHAR(64) UNIQUE NOT NULL,
                amount_kopeks INT NOT NULL,
                currency VARCHAR(10) NOT NULL,
                status VARCHAR(50) NOT NULL,
                payment_url TEXT NULL,
                metadata_json JSON NULL,
                paid_at DATETIME NULL,
                transaction_id INT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (transaction_id) REFERENCES transactions(id)
            );

            CREATE INDEX idx_pal24_payments_user_id ON pal24_payments(user_id);
            CREATE INDEX idx_pal24_payments_invoice_id ON pal24_payments(invoice_id);
            CREATE INDEX idx_pal24_payments_status ON pal24_payments(status);
            """,
            "pal24_payments",
        ),
    },
}


WATA_PAYMENTS_DEFINITION = {
    "required_tables": ["users", "transactions"],
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE wata_payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                payment_link_id VARCHAR(64) NOT NULL,
                amount_kopeks INTEGER NOT NULL,
                currency VARCHAR(10) NOT NULL,
                status VARCHAR(50) NOT NULL,
                payment_url TEXT NULL,
                metadata_json JSON NULL,
                paid_at DATETIME NULL,
                transaction_id INTEGER NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (transaction_id) REFERENCES transactions(id)
            );

            CREATE INDEX idx_wata_payments_user_id ON wata_payments(user_id);
            CREATE INDEX idx_wata_payments_payment_link ON wata_payments(payment_link_id);
            CREATE INDEX idx_wata_payments_status ON wata_payments(status);
            """,
            "wata_payments",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE wata_payments (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                payment_link_id VARCHAR(64) NOT NULL,
                amount_kopeks INTEGER NOT NULL,
                currency VARCHAR(10) NOT NULL,
                status VARCHAR(50) NOT NULL,
                payment_url TEXT NULL,
                metadata_json JSON NULL,
                paid_at TIMESTAMP NULL,
                transaction_id INTEGER NULL REFERENCES transactions(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX idx_wata_payments_user_id ON wata_payments(user_id);
            CREATE INDEX idx_wata_payments_payment_link ON wata_payments(payment_link_id);
            CREATE INDEX idx_wata_payments_status ON wata_payments(status);
            """,
            "wata_payments",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE wata_payments (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                payment_link_id VARCHAR(64) NOT NULL,
                amount_kopeks INT NOT NULL,
                currency VARCHAR(10) NOT NULL,
                status VARCHAR(50) NOT NULL,
                payment_url TEXT NULL,
                metadata_json JSON NULL,
                paid_at DATETIME NULL,
                transaction_id INT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (transaction_id) REFERENCES transactions(id)
            );

            CREATE INDEX idx_wata_payments_user_id ON wata_payments(user_id);
            CREATE INDEX idx_wata_payments_payment_link ON wata_payments(payment_link_id);
            CREATE INDEX idx_wata_payments_status ON wata_payments(status);
            """,
            "wata_payments",
        ),
    },
}


PROMO_OFFER_TEMPLATES_DEFINITION = {
    "required_tables": ["users"],
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE promo_offer_templates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(255) NOT NULL,
                offer_type VARCHAR(50) NOT NULL,
                message_text TEXT NOT NULL,
                button_text VARCHAR(255) NOT NULL,
                valid_hours INTEGER NOT NULL DEFAULT 24,
                discount_percent INTEGER NOT NULL DEFAULT 0,
                bonus_amount_kopeks INTEGER NOT NULL DEFAULT 0,
                active_discount_hours INTEGER NULL,
                test_duration_hours INTEGER NULL,
                test_squad_uuids TEXT NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_by INTEGER NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE INDEX ix_promo_offer_templates_type ON promo_offer_templates(offer_type);
            """,
            "promo_offer_templates",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE IF NOT EXISTS promo_offer_templates (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                offer_type VARCHAR(50) NOT NULL,
                message_text TEXT NOT NULL,
                button_text VARCHAR(255) NOT NULL,
                valid_hours INTEGER NOT NULL DEFAULT 24,
                discount_percent INTEGER NOT NULL DEFAULT 0,
                bonus_amount_kopeks INTEGER NOT NULL DEFAULT 0,
                active_discount_hours INTEGER NULL,
                test_duration_hours INTEGER NULL,
                test_squad_uuids JSON NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_by INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS ix_promo_offer_templates_type ON promo_offer_templates(offer_type);
            """,
            "promo_offer_templates",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE IF NOT EXISTS promo_offer_templates (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                offer_type VARCHAR(50) NOT NULL,
                message_text TEXT NOT NULL,
                button_text VARCHAR(255) NOT NULL,
                valid_hours INT NOT NULL DEFAULT 24,
                discount_percent INT NOT NULL DEFAULT 0,
                bonus_amount_kopeks INT NOT NULL DEFAULT 0,
                active_discount_hours INT NULL,
                test_duration_hours INT NULL,
                test_squad_uuids JSON NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_by INT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY(created_by) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE INDEX ix_promo_offer_templates_type ON promo_offer_templates(offer_type);
            """,
            "promo_offer_templates",
        ),
    },
}


MAIN_MENU_BUTTONS_DEFINITION = {
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE main_menu_buttons (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text VARCHAR(64) NOT NULL,
                action_type VARCHAR(20) NOT NULL,
                action_value TEXT NOT NULL,
                visibility VARCHAR(20) NOT NULL DEFAULT 'all',
                is_active BOOLEAN NOT NULL DEFAULT 1,
                display_order INTEGER NOT NULL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS ix_main_menu_buttons_order ON main_menu_buttons(display_order, id);
            """,
            "main_menu_buttons",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE IF NOT EXISTS main_menu_buttons (
                id SERIAL PRIMARY KEY,
                text VARCHAR(64) NOT NULL,
                action_type VARCHAR(20) NOT NULL,
                action_value TEXT NOT NULL,
                visibility VARCHAR(20) NOT NULL DEFAULT 'all',
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                display_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS ix_main_menu_buttons_order ON main_menu_buttons(display_order, id);
            """,
            "main_menu_buttons",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE IF NOT EXISTS main_menu_buttons (
                id INT AUTO_INCREMENT PRIMARY KEY,
                text VARCHAR(64) NOT NULL,
                action_type VARCHAR(20) NOT NULL,
                action_value TEXT NOT NULL,
                visibility VARCHAR(20) NOT NULL DEFAULT 'all',
                is_active BOOLEAN NOT NULL DEFAULT 1,
                display_order INT NOT NULL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            );

            CREATE INDEX ix_main_menu_buttons_order ON main_menu_buttons(display_order, id);
            """,
            "main_menu_buttons",
        ),
    },
}


PROMO_OFFER_LOGS_DEFINITION = {
    "required_tables": ["users", "discount_offers"],
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE IF NOT EXISTS promo_offer_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                offer_id INTEGER NULL REFERENCES discount_offers(id) ON DELETE SET NULL,
                action VARCHAR(50) NOT NULL,
                source VARCHAR(100) NULL,
                percent INTEGER NULL,
                effect_type VARCHAR(50) NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS ix_promo_offer_logs_user_action ON promo_offer_logs(user_id, action);
            """,
            "promo_offer_logs",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE IF NOT EXISTS promo_offer_logs (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NULL REFERENCES users(id) ON DELETE SET NULL,
                offer_id INTEGER NULL REFERENCES discount_offers(id) ON DELETE SET NULL,
                action VARCHAR(50) NOT NULL,
                source VARCHAR(100) NULL,
                percent INTEGER NULL,
                effect_type VARCHAR(50) NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS ix_promo_offer_logs_user_action ON promo_offer_logs(user_id, action);
            """,
            "promo_offer_logs",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE IF NOT EXISTS promo_offer_logs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NULL,
                offer_id INT NULL,
                action VARCHAR(50) NOT NULL,
                source VARCHAR(100) NULL,
                percent INT NULL,
                effect_type VARCHAR(50) NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX ix_promo_offer_logs_user_action ON promo_offer_logs(user_id, action);
            """,
            "promo_offer_logs",
        ),
    },
}


SUBSCRIPTION_TEMP_ACCESS_DEFINITION = {
    "required_tables": ["users", "subscriptions", "discount_offers"],
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE subscription_temporary_access (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                subscription_id INTEGER NOT NULL,
                squad_uuid VARCHAR(64) NULL,
                expires_at DATETIME NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
            );

            CREATE INDEX idx_subscription_temp_access_user_id ON subscription_temporary_access(user_id);
            CREATE INDEX idx_subscription_temp_access_subscription_id ON subscription_temporary_access(subscription_id);
            """,
            "subscription_temporary_access",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE subscription_temporary_access (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                subscription_id INTEGER NOT NULL REFERENCES subscriptions(id),
                squad_uuid VARCHAR(64) NULL,
                expires_at TIMESTAMP NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX idx_subscription_temp_access_user_id ON subscription_temporary_access(user_id);
            CREATE INDEX idx_subscription_temp_access_subscription_id ON subscription_temporary_access(subscription_id);
            """,
            "subscription_temporary_access",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE subscription_temporary_access (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                subscription_id INT NOT NULL,
                squad_uuid VARCHAR(64) NULL,
                expires_at DATETIME NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (subscription_id) REFERENCES subscriptions(id)
            );

            CREATE INDEX idx_subscription_temp_access_user_id ON subscription_temporary_access(user_id);
            CREATE INDEX idx_subscription_temp_access_subscription_id ON subscription_temporary_access(subscription_id);
            """,
            "subscription_temporary_access",
        ),
    },
}


USER_MESSAGES_DEFINITION = {
    "required_tables": ["users"],
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE user_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                message_type VARCHAR(50) NOT NULL,
                message_data JSON NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX idx_user_messages_user_id ON user_messages(user_id);
            CREATE INDEX idx_user_messages_type ON user_messages(message_type);
            """,
            "user_messages",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE user_messages (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id),
                message_type VARCHAR(50) NOT NULL,
                message_data JSONB NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX idx_user_messages_user_id ON user_messages(user_id);
            CREATE INDEX idx_user_messages_type ON user_messages(message_type);
            """,
            "user_messages",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE user_messages (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                message_type VARCHAR(50) NOT NULL,
                message_data JSON NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE INDEX idx_user_messages_user_id ON user_messages(user_id);
            CREATE INDEX idx_user_messages_type ON user_messages(message_type);
            """,
            "user_messages",
        ),
    },
}


PROMO_GROUPS_DEFINITION = {
    "required_tables": ["promocodes"],
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE IF NOT EXISTS promo_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(255) NOT NULL,
                description TEXT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_promo_groups_name ON promo_groups(name);
            """,
            "promo_groups",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE IF NOT EXISTS promo_groups (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_promo_groups_name ON promo_groups(name);
            """,
            "promo_groups",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE IF NOT EXISTS promo_groups (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                description TEXT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            );

            CREATE INDEX idx_promo_groups_name ON promo_groups(name);
            """,
            "promo_groups",
        ),
    },
}


WELCOME_TEXTS_DEFINITION = {
    "required_tables": ["users"],
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE welcome_texts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text_content TEXT NOT NULL,
                is_active BOOLEAN DEFAULT 1,
                is_enabled BOOLEAN DEFAULT 1 NOT NULL,
                created_by INTEGER NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE INDEX idx_welcome_texts_active ON welcome_texts(is_active);
            CREATE INDEX idx_welcome_texts_enabled ON welcome_texts(is_enabled);
            CREATE INDEX idx_welcome_texts_updated ON welcome_texts(updated_at);
            """,
            "welcome_texts",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE welcome_texts (
                id SERIAL PRIMARY KEY,
                text_content TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                is_enabled BOOLEAN DEFAULT TRUE NOT NULL,
                created_by INTEGER NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE INDEX idx_welcome_texts_active ON welcome_texts(is_active);
            CREATE INDEX idx_welcome_texts_enabled ON welcome_texts(is_enabled);
            CREATE INDEX idx_welcome_texts_updated ON welcome_texts(updated_at);
            """,
            "welcome_texts",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE welcome_texts (
                id INT AUTO_INCREMENT PRIMARY KEY,
                text_content TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                is_enabled BOOLEAN DEFAULT TRUE NOT NULL,
                created_by INT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
            );

            CREATE INDEX idx_welcome_texts_active ON welcome_texts(is_active);
            CREATE INDEX idx_welcome_texts_enabled ON welcome_texts(is_enabled);
            CREATE INDEX idx_welcome_texts_updated ON welcome_texts(updated_at);
            """,
            "welcome_texts",
        ),
    },
}


SUBSCRIPTION_CONVERSIONS_DEFINITION = {
    "required_tables": ["users"],
    "statements": {
        "sqlite": build_statements_from_sql(
            """
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
            """,
            "subscription_conversions",
        ),
        "postgresql": build_statements_from_sql(
            """
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
            """,
            "subscription_conversions",
        ),
        "mysql": build_statements_from_sql(
            """
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
            """,
            "subscription_conversions",
        ),
    },
}


SERVER_SQUAD_PROMO_GROUPS_DEFINITION = {
    "required_tables": ["server_squads", "promo_groups"],
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE server_squad_promo_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                squad_uuid VARCHAR(64) NOT NULL,
                promo_group_id INTEGER NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE UNIQUE INDEX idx_server_squad_promo_groups_unique ON server_squad_promo_groups(squad_uuid, promo_group_id);
            """,
            "server_squad_promo_groups",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE server_squad_promo_groups (
                id SERIAL PRIMARY KEY,
                squad_uuid VARCHAR(64) NOT NULL,
                promo_group_id INTEGER NOT NULL REFERENCES promo_groups(id),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            CREATE UNIQUE INDEX idx_server_squad_promo_groups_unique ON server_squad_promo_groups(squad_uuid, promo_group_id);
            """,
            "server_squad_promo_groups",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE server_squad_promo_groups (
                id INT AUTO_INCREMENT PRIMARY KEY,
                squad_uuid VARCHAR(64) NOT NULL,
                promo_group_id INT NOT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                FOREIGN KEY (promo_group_id) REFERENCES promo_groups(id)
            );

            CREATE UNIQUE INDEX idx_server_squad_promo_groups_unique ON server_squad_promo_groups(squad_uuid, promo_group_id);
            """,
            "server_squad_promo_groups",
        ),
    },
}


SYSTEM_SETTINGS_DEFINITION = {
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE system_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key VARCHAR(255) NOT NULL UNIQUE,
                value TEXT NULL,
                description TEXT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """,
            "system_settings",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE system_settings (
                id SERIAL PRIMARY KEY,
                key VARCHAR(255) NOT NULL UNIQUE,
                value TEXT NULL,
                description TEXT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
            """,
            "system_settings",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE system_settings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                key VARCHAR(255) NOT NULL UNIQUE,
                value TEXT NULL,
                description TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """,
            "system_settings",
        ),
    },
}


WEB_API_TOKENS_DEFINITION = {
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE web_api_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(255) NOT NULL,
                token_hash VARCHAR(128) NOT NULL UNIQUE,
                token_prefix VARCHAR(32) NOT NULL,
                description TEXT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                expires_at DATETIME NULL,
                last_used_at DATETIME NULL,
                last_used_ip VARCHAR(64) NULL,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_by VARCHAR(255) NULL
            );

            CREATE INDEX idx_web_api_tokens_active ON web_api_tokens(is_active);
            CREATE INDEX idx_web_api_tokens_prefix ON web_api_tokens(token_prefix);
            CREATE INDEX idx_web_api_tokens_last_used ON web_api_tokens(last_used_at);
            """,
            "web_api_tokens",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE web_api_tokens (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                token_hash VARCHAR(128) NOT NULL UNIQUE,
                token_prefix VARCHAR(32) NOT NULL,
                description TEXT NULL,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW(),
                expires_at TIMESTAMP NULL,
                last_used_at TIMESTAMP NULL,
                last_used_ip VARCHAR(64) NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_by VARCHAR(255) NULL
            );

            CREATE INDEX idx_web_api_tokens_active ON web_api_tokens(is_active);
            CREATE INDEX idx_web_api_tokens_prefix ON web_api_tokens(token_prefix);
            CREATE INDEX idx_web_api_tokens_last_used ON web_api_tokens(last_used_at);
            """,
            "web_api_tokens",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE web_api_tokens (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                token_hash VARCHAR(128) NOT NULL UNIQUE,
                token_prefix VARCHAR(32) NOT NULL,
                description TEXT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NULL,
                last_used_at TIMESTAMP NULL,
                last_used_ip VARCHAR(64) NULL,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_by VARCHAR(255) NULL
            ) ENGINE=InnoDB;

            CREATE INDEX idx_web_api_tokens_active ON web_api_tokens(is_active);
            CREATE INDEX idx_web_api_tokens_prefix ON web_api_tokens(token_prefix);
            CREATE INDEX idx_web_api_tokens_last_used ON web_api_tokens(last_used_at);
            """,
            "web_api_tokens",
        ),
    },
}


PRIVACY_POLICIES_DEFINITION = {
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE privacy_policies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language VARCHAR(10) NOT NULL UNIQUE,
                content TEXT NOT NULL,
                is_enabled BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """,
            "privacy_policies",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE privacy_policies (
                id SERIAL PRIMARY KEY,
                language VARCHAR(10) NOT NULL UNIQUE,
                content TEXT NOT NULL,
                is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
            """,
            "privacy_policies",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE privacy_policies (
                id INT AUTO_INCREMENT PRIMARY KEY,
                language VARCHAR(10) NOT NULL UNIQUE,
                content TEXT NOT NULL,
                is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            );
            """,
            "privacy_policies",
        ),
    },
}


PUBLIC_OFFERS_DEFINITION = {
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE public_offers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language VARCHAR(10) NOT NULL UNIQUE,
                content TEXT NOT NULL,
                is_enabled BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """,
            "public_offers",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE public_offers (
                id SERIAL PRIMARY KEY,
                language VARCHAR(10) NOT NULL UNIQUE,
                content TEXT NOT NULL,
                is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
            """,
            "public_offers",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE public_offers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                language VARCHAR(10) NOT NULL UNIQUE,
                content TEXT NOT NULL,
                is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            );
            """,
            "public_offers",
        ),
    },
}


FAQ_SETTINGS_DEFINITION = {
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE faq_settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language VARCHAR(10) NOT NULL UNIQUE,
                is_enabled BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """,
            "faq_settings",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE faq_settings (
                id SERIAL PRIMARY KEY,
                language VARCHAR(10) NOT NULL UNIQUE,
                is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );
            """,
            "faq_settings",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE faq_settings (
                id INT AUTO_INCREMENT PRIMARY KEY,
                language VARCHAR(10) NOT NULL UNIQUE,
                is_enabled BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            );
            """,
            "faq_settings",
        ),
    },
}


FAQ_PAGES_DEFINITION = {
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE faq_pages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                language VARCHAR(10) NOT NULL,
                title VARCHAR(255) NOT NULL,
                content TEXT NOT NULL,
                display_order INTEGER NOT NULL DEFAULT 0,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX idx_faq_pages_language ON faq_pages(language);
            """,
            "faq_pages",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE faq_pages (
                id SERIAL PRIMARY KEY,
                language VARCHAR(10) NOT NULL,
                title VARCHAR(255) NOT NULL,
                content TEXT NOT NULL,
                display_order INTEGER NOT NULL DEFAULT 0,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT NOW(),
                updated_at TIMESTAMP DEFAULT NOW()
            );

            CREATE INDEX idx_faq_pages_language ON faq_pages(language);
            CREATE INDEX idx_faq_pages_order ON faq_pages(language, display_order);
            """,
            "faq_pages",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE faq_pages (
                id INT AUTO_INCREMENT PRIMARY KEY,
                language VARCHAR(10) NOT NULL,
                title VARCHAR(255) NOT NULL,
                content TEXT NOT NULL,
                display_order INT NOT NULL DEFAULT 0,
                is_active BOOLEAN NOT NULL DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB;

            CREATE INDEX idx_faq_pages_language ON faq_pages(language);
            CREATE INDEX idx_faq_pages_order ON faq_pages(language, display_order);
            """,
            "faq_pages",
        ),
    },
}


USER_PROMO_GROUPS_DEFINITION = {
    "required_tables": ["users", "promo_groups"],
    "statements": {
        "sqlite": build_statements_from_sql(
            """
            CREATE TABLE user_promo_groups (
                user_id INTEGER NOT NULL,
                promo_group_id INTEGER NOT NULL,
                assigned_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                assigned_by VARCHAR(50) DEFAULT 'system',
                PRIMARY KEY (user_id, promo_group_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (promo_group_id) REFERENCES promo_groups(id) ON DELETE CASCADE
            );

            CREATE INDEX idx_user_promo_groups_user_id ON user_promo_groups(user_id);
            """,
            "user_promo_groups",
        ),
        "postgresql": build_statements_from_sql(
            """
            CREATE TABLE user_promo_groups (
                user_id INTEGER NOT NULL,
                promo_group_id INTEGER NOT NULL,
                assigned_at TIMESTAMP DEFAULT NOW(),
                assigned_by VARCHAR(50) DEFAULT 'system',
                PRIMARY KEY (user_id, promo_group_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (promo_group_id) REFERENCES promo_groups(id) ON DELETE CASCADE
            );

            CREATE INDEX idx_user_promo_groups_user_id ON user_promo_groups(user_id);
            """,
            "user_promo_groups",
        ),
        "mysql": build_statements_from_sql(
            """
            CREATE TABLE user_promo_groups (
                user_id INT NOT NULL,
                promo_group_id INT NOT NULL,
                assigned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                assigned_by VARCHAR(50) DEFAULT 'system',
                PRIMARY KEY (user_id, promo_group_id),
                FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
                FOREIGN KEY (promo_group_id) REFERENCES promo_groups(id) ON DELETE CASCADE
            );

            CREATE INDEX idx_user_promo_groups_user_id ON user_promo_groups(user_id);
            """,
            "user_promo_groups",
        ),
    },
}


SUPPORT_AUDIT_LOGS_DEFINITION = {
    "required_tables": ["users", "tickets"],
    "statements": {
        "sqlite": build_statements_from_sql(
            """
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
            """,
            "support_audit_logs",
        ),
        "postgresql": build_statements_from_sql(
            """
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
            """,
            "support_audit_logs",
        ),
        "mysql": build_statements_from_sql(
            """
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
            """,
            "support_audit_logs",
        ),
    },
}


async def sync_postgres_sequences() -> bool:
    """Ensure PostgreSQL sequences match the current max values after restores."""

    db_type = await get_database_type()

    if db_type != "postgresql":
        logger.debug("Пропускаем синхронизацию последовательностей: тип БД %s", db_type)
        return True

    try:
        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    """
                    SELECT
                        cols.table_schema,
                        cols.table_name,
                        cols.column_name,
                        pg_get_serial_sequence(
                            format('%I.%I', cols.table_schema, cols.table_name),
                            cols.column_name
                        ) AS sequence_path
                    FROM information_schema.columns AS cols
                    WHERE cols.column_default LIKE 'nextval(%'
                      AND cols.table_schema NOT IN ('pg_catalog', 'information_schema')
                    """
                )
            )

            sequences = result.fetchall()

            if not sequences:
                logger.info("ℹ️ Не найдено последовательностей PostgreSQL для синхронизации")
                return True

            for table_schema, table_name, column_name, sequence_path in sequences:
                if not sequence_path:
                    continue

                max_result = await conn.execute(
                    text(
                        f'SELECT COALESCE(MAX("{column_name}"), 0) '
                        f'FROM "{table_schema}"."{table_name}"'
                    )
                )
                max_value = max_result.scalar() or 0

                parts = sequence_path.split('.')
                if len(parts) == 2:
                    seq_schema, seq_name = parts
                else:
                    seq_schema, seq_name = 'public', parts[-1]

                seq_schema = seq_schema.strip('"')
                seq_name = seq_name.strip('"')
                current_result = await conn.execute(
                    text(
                        f'SELECT last_value, is_called FROM "{seq_schema}"."{seq_name}"'
                    )
                )
                current_row = current_result.fetchone()

                if current_row:
                    current_last, is_called = current_row
                    current_next = current_last + 1 if is_called else current_last
                    if current_next > max_value:
                        continue

                await conn.execute(
                    text(
                        """
                        SELECT setval(:sequence_name, :new_value, TRUE)
                        """
                    ),
                    {"sequence_name": sequence_path, "new_value": max_value},
                )
                logger.info(
                    "🔄 Последовательность %s синхронизирована: MAX=%s, следующий ID=%s",
                    sequence_path,
                    max_value,
                    max_value + 1,
                )

        return True

    except Exception as error:
        logger.error("❌ Ошибка синхронизации последовательностей PostgreSQL: %s", error)
        return False

async def check_table_exists(table_name: str) -> bool:
    try:
        async with AsyncSessionLocal() as session:
            return await table_exists(session, table_name)
    except Exception as e:
        logger.error(f"Ошибка проверки существования таблицы {table_name}: {e}")
        return False


async def check_column_exists(table_name: str, column_name: str) -> bool:
    try:
        async with AsyncSessionLocal() as session:
            return await column_exists(session, table_name, column_name)
    except Exception as e:
        logger.error(f"Ошибка проверки существования колонки {column_name}: {e}")
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
                rows = result.fetchall()
                return any(row[5] == constraint_name for row in rows)

            return False

    except Exception as e:
        logger.error(
            f"Ошибка проверки существования ограничения {constraint_name} для {table_name}: {e}"
        )
        return False


async def check_index_exists(table_name: str, index_name: str) -> bool:
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            if db_type == "postgresql":
                result = await conn.execute(
                    text(
                        """
                    SELECT 1
                    FROM pg_indexes
                    WHERE schemaname = 'public'
                      AND tablename = :table_name
                      AND indexname = :index_name
                """
                    ),
                    {"table_name": table_name, "index_name": index_name},
                )
                return result.fetchone() is not None

            if db_type == "mysql":
                result = await conn.execute(
                    text(
                        """
                    SELECT 1
                    FROM information_schema.statistics
                    WHERE table_schema = DATABASE()
                      AND table_name = :table_name
                      AND index_name = :index_name
                """
                    ),
                    {"table_name": table_name, "index_name": index_name},
                )
                return result.fetchone() is not None

            if db_type == "sqlite":
                result = await conn.execute(text(f"PRAGMA index_list({table_name})"))
                rows = result.fetchall()
                return any(row[1] == index_name for row in rows)

            return False

    except Exception as e:
        logger.error(
            f"Ошибка проверки существования индекса {index_name} для {table_name}: {e}"
        )
        return False


async def fetch_duplicate_payment_links(conn) -> List[Tuple[str, int]]:
    result = await conn.execute(
        text(
            "SELECT payment_link_id, COUNT(*) AS cnt "
            "FROM wata_payments "
            "WHERE payment_link_id IS NOT NULL AND payment_link_id <> '' "
            "GROUP BY payment_link_id "
            "HAVING COUNT(*) > 1"
        )
    )
    return [(row[0], row[1]) for row in result.fetchall()]


def _build_dedup_suffix(base_suffix: str, record_id: int, max_length: int = 64) -> Tuple[str, int]:
    suffix = f"{base_suffix}{record_id}"
    trimmed_length = max_length - len(suffix)
    if trimmed_length < 1:
        # Fallback: use the record id only to stay within the limit.
        suffix = f"dup-{record_id}"
        trimmed_length = max_length - len(suffix)
    return suffix, trimmed_length


async def resolve_duplicate_payment_links(conn, db_type: str) -> bool:
    duplicates = await fetch_duplicate_payment_links(conn)

    if not duplicates:
        return True

    logger.warning(
        "Найдены дубликаты payment_link_id в wata_payments: %s",
        ", ".join(f"{link}×{count}" for link, count in duplicates[:5]),
    )

    for payment_link_id, _ in duplicates:
        result = await conn.execute(
            text(
                "SELECT id, payment_link_id FROM wata_payments "
                "WHERE payment_link_id = :payment_link_id "
                "ORDER BY id"
            ),
            {"payment_link_id": payment_link_id},
        )

        rows = result.fetchall()

        if not rows:
            continue

        # Skip the first occurrence to preserve the original link value.
        for duplicate_row in rows[1:]:
            record_id = duplicate_row[0]
            original_link = duplicate_row[1] or ""
            suffix, trimmed_length = _build_dedup_suffix("-dup-", record_id)
            new_base = original_link[:trimmed_length] if trimmed_length > 0 else ""
            new_link = f"{new_base}{suffix}" if new_base else suffix

            await conn.execute(
                text(
                    "UPDATE wata_payments SET payment_link_id = :new_link "
                    "WHERE id = :record_id"
                ),
                {"new_link": new_link, "record_id": record_id},
            )

    remaining_duplicates = await fetch_duplicate_payment_links(conn)

    if remaining_duplicates:
        logger.error(
            "Не удалось устранить дубликаты payment_link_id: %s",
            ", ".join(f"{link}×{count}" for link, count in remaining_duplicates[:5]),
        )
        return False

    logger.info("✅ Дубликаты payment_link_id устранены")
    return True


async def enforce_wata_payment_link_constraints(
    conn,
    db_type: str,
    unique_index_exists: bool,
    legacy_index_exists: bool,
) -> Tuple[bool, bool]:
    try:
        if db_type == "sqlite":
            await conn.execute(
                text(
                    "UPDATE wata_payments "
                    "SET payment_link_id = 'legacy-' || id "
                    "WHERE payment_link_id IS NULL OR payment_link_id = ''"
                )
            )

            if not await resolve_duplicate_payment_links(conn, db_type):
                return unique_index_exists, legacy_index_exists

            if not unique_index_exists:
                await conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_wata_payment_link "
                        "ON wata_payments(payment_link_id)"
                    )
                )
                logger.info("✅ Создан уникальный индекс uq_wata_payment_link для payment_link_id")
                unique_index_exists = True
            else:
                logger.info("ℹ️ Уникальный индекс для payment_link_id уже существует")

            if legacy_index_exists and unique_index_exists:
                await conn.execute(text("DROP INDEX IF EXISTS idx_wata_link_id"))
                logger.info("ℹ️ Удалён устаревший индекс idx_wata_link_id")
                legacy_index_exists = False

            return unique_index_exists, legacy_index_exists

        if db_type == "postgresql":
            await conn.execute(
                text(
                    "UPDATE wata_payments "
                    "SET payment_link_id = 'legacy-' || id::text "
                    "WHERE payment_link_id IS NULL OR payment_link_id = ''"
                )
            )

            await conn.execute(
                text(
                    "ALTER TABLE wata_payments "
                    "ALTER COLUMN payment_link_id SET NOT NULL"
                )
            )
            logger.info("✅ Колонка payment_link_id теперь NOT NULL")

            if not await resolve_duplicate_payment_links(conn, db_type):
                return unique_index_exists, legacy_index_exists

            if not unique_index_exists:
                await conn.execute(
                    text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS uq_wata_payment_link "
                        "ON wata_payments(payment_link_id)"
                    )
                )
                logger.info("✅ Создан уникальный индекс uq_wata_payment_link для payment_link_id")
                unique_index_exists = True
            else:
                logger.info("ℹ️ Уникальный индекс для payment_link_id уже существует")

            if legacy_index_exists and unique_index_exists:
                await conn.execute(text("DROP INDEX IF EXISTS idx_wata_link_id"))
                logger.info("ℹ️ Удалён устаревший индекс idx_wata_link_id")
                legacy_index_exists = False

            return unique_index_exists, legacy_index_exists

        if db_type == "mysql":
            await conn.execute(
                text(
                    "UPDATE wata_payments "
                    "SET payment_link_id = CONCAT('legacy-', id) "
                    "WHERE payment_link_id IS NULL OR payment_link_id = ''"
                )
            )

            await conn.execute(
                text(
                    "ALTER TABLE wata_payments "
                    "MODIFY COLUMN payment_link_id VARCHAR(64) NOT NULL"
                )
            )
            logger.info("✅ Колонка payment_link_id теперь NOT NULL")

            if not await resolve_duplicate_payment_links(conn, db_type):
                return unique_index_exists, legacy_index_exists

            if not unique_index_exists:
                await conn.execute(
                    text(
                        "CREATE UNIQUE INDEX uq_wata_payment_link "
                        "ON wata_payments(payment_link_id)"
                    )
                )
                logger.info("✅ Создан уникальный индекс uq_wata_payment_link для payment_link_id")
                unique_index_exists = True
            else:
                logger.info("ℹ️ Уникальный индекс для payment_link_id уже существует")

            if legacy_index_exists and unique_index_exists:
                await conn.execute(text("DROP INDEX idx_wata_link_id ON wata_payments"))
                logger.info("ℹ️ Удалён устаревший индекс idx_wata_link_id")
                legacy_index_exists = False

            return unique_index_exists, legacy_index_exists

        logger.warning(
            "⚠️ Неизвестный тип БД %s — не удалось усилить ограничения payment_link_id", db_type
        )
        return unique_index_exists, legacy_index_exists

    except Exception as e:
        logger.error(f"Ошибка настройки ограничений payment_link_id: {e}")
        return unique_index_exists, legacy_index_exists

async def create_cryptobot_payments_table():
    return await create_table_from_definition(
        "cryptobot_payments", CRYPTOBOT_PAYMENTS_DEFINITION
    )

async def create_heleket_payments_table():
    return await create_table_from_definition(
        "heleket_payments", HELEKET_PAYMENTS_DEFINITION
    )

async def create_mulenpay_payments_table():
    return await create_table_from_definition(
        "mulenpay_payments", MULENPAY_PAYMENTS_DEFINITION
    )

async def ensure_mulenpay_payment_schema() -> bool:
    logger.info("=== ОБНОВЛЕНИЕ СХЕМЫ MULEN PAY ===")

    table_exists = await check_table_exists("mulenpay_payments")
    if not table_exists:
        logger.warning("⚠️ Таблица mulenpay_payments отсутствует — создаём заново")
        return await create_mulenpay_payments_table()

    try:
        column_exists = await check_column_exists("mulenpay_payments", "mulen_payment_id")
        paid_at_column_exists = await check_column_exists("mulenpay_payments", "paid_at")
        index_exists = await check_index_exists("mulenpay_payments", "idx_mulenpay_payment_id")

        async with engine.begin() as conn:
            db_type = await get_database_type()

            if not column_exists:
                if db_type == "sqlite":
                    alter_sql = "ALTER TABLE mulenpay_payments ADD COLUMN mulen_payment_id INTEGER NULL"
                elif db_type == "postgresql":
                    alter_sql = "ALTER TABLE mulenpay_payments ADD COLUMN mulen_payment_id INTEGER NULL"
                elif db_type == "mysql":
                    alter_sql = "ALTER TABLE mulenpay_payments ADD COLUMN mulen_payment_id INT NULL"
                else:
                    logger.error(
                        "Неподдерживаемый тип БД для добавления mulen_payment_id в mulenpay_payments: %s",
                        db_type,
                    )
                    return False

                await conn.execute(text(alter_sql))
                logger.info("✅ Добавлена колонка mulenpay_payments.mulen_payment_id")
            else:
                logger.info("ℹ️ Колонка mulenpay_payments.mulen_payment_id уже существует")

            if not paid_at_column_exists:
                if db_type == "sqlite":
                    alter_paid_at_sql = "ALTER TABLE mulenpay_payments ADD COLUMN paid_at DATETIME NULL"
                elif db_type == "postgresql":
                    alter_paid_at_sql = "ALTER TABLE mulenpay_payments ADD COLUMN paid_at TIMESTAMP NULL"
                elif db_type == "mysql":
                    alter_paid_at_sql = "ALTER TABLE mulenpay_payments ADD COLUMN paid_at DATETIME NULL"
                else:
                    logger.error(
                        "Неподдерживаемый тип БД для добавления paid_at в mulenpay_payments: %s",
                        db_type,
                    )
                    return False

                await conn.execute(text(alter_paid_at_sql))
                logger.info("✅ Добавлена колонка mulenpay_payments.paid_at")
            else:
                logger.info("ℹ️ Колонка mulenpay_payments.paid_at уже существует")

            if not index_exists:
                if db_type == "sqlite":
                    create_index_sql = (
                        "CREATE INDEX IF NOT EXISTS idx_mulenpay_payment_id "
                        "ON mulenpay_payments(mulen_payment_id)"
                    )
                elif db_type == "postgresql":
                    create_index_sql = (
                        "CREATE INDEX IF NOT EXISTS idx_mulenpay_payment_id "
                        "ON mulenpay_payments(mulen_payment_id)"
                    )
                elif db_type == "mysql":
                    create_index_sql = (
                        "CREATE INDEX idx_mulenpay_payment_id "
                        "ON mulenpay_payments(mulen_payment_id)"
                    )
                else:
                    logger.error(
                        "Неподдерживаемый тип БД для создания индекса mulenpay_payment_id: %s",
                        db_type,
                    )
                    return False

                await conn.execute(text(create_index_sql))
                logger.info("✅ Создан индекс idx_mulenpay_payment_id")
            else:
                logger.info("ℹ️ Индекс idx_mulenpay_payment_id уже существует")

        return True

    except Exception as e:
        logger.error(f"Ошибка обновления схемы mulenpay_payments: {e}")
        return False


async def create_pal24_payments_table():
    return await create_table_from_definition(
        "pal24_payments", PAL24_PAYMENTS_DEFINITION
    )

async def create_wata_payments_table():
    return await create_table_from_definition(
        "wata_payments", WATA_PAYMENTS_DEFINITION
    )

async def ensure_wata_payment_schema() -> bool:
    try:
        table_exists = await check_table_exists("wata_payments")
        if not table_exists:
            logger.warning("⚠️ Таблица wata_payments отсутствует — создаём заново")
            return await create_wata_payments_table()

        db_type = await get_database_type()

        legacy_link_index_exists = await check_index_exists(
            "wata_payments", "idx_wata_link_id"
        )
        unique_link_index_exists = await check_index_exists(
            "wata_payments", "uq_wata_payment_link"
        )
        builtin_unique_index_exists = await check_index_exists(
            "wata_payments", "wata_payments_payment_link_id_key"
        )
        sqlite_auto_unique_exists = (
            await check_index_exists("wata_payments", "sqlite_autoindex_wata_payments_1")
            if db_type == "sqlite"
            else False
        )
        order_index_exists = await check_index_exists("wata_payments", "idx_wata_order_id")

        payment_link_column_exists = await check_column_exists(
            "wata_payments", "payment_link_id"
        )
        order_id_column_exists = await check_column_exists("wata_payments", "order_id")

        unique_index_exists = (
            unique_link_index_exists
            or builtin_unique_index_exists
            or sqlite_auto_unique_exists
        )

        async with engine.begin() as conn:
            if not payment_link_column_exists:
                if db_type == "sqlite":
                    await conn.execute(
                        text(
                            "ALTER TABLE wata_payments "
                            "ADD COLUMN payment_link_id VARCHAR(64) NOT NULL DEFAULT ''"
                        )
                    )
                    payment_link_column_exists = True
                    unique_index_exists = False
                elif db_type == "postgresql":
                    await conn.execute(
                        text(
                            "ALTER TABLE wata_payments "
                            "ADD COLUMN IF NOT EXISTS payment_link_id VARCHAR(64)"
                        )
                    )
                    payment_link_column_exists = True
                elif db_type == "mysql":
                    await conn.execute(
                        text("ALTER TABLE wata_payments ADD COLUMN payment_link_id VARCHAR(64)")
                    )
                    payment_link_column_exists = True
                else:
                    logger.warning(
                        "⚠️ Неизвестный тип БД %s — пропущено добавление payment_link_id",
                        db_type,
                    )

                if payment_link_column_exists:
                    logger.info("✅ Добавлена колонка payment_link_id в wata_payments")

            if payment_link_column_exists:
                unique_index_exists, legacy_link_index_exists = (
                    await enforce_wata_payment_link_constraints(
                        conn,
                        db_type,
                        unique_index_exists,
                        legacy_link_index_exists,
                    )
                )

            if not order_id_column_exists:
                if db_type == "sqlite":
                    await conn.execute(
                        text("ALTER TABLE wata_payments ADD COLUMN order_id VARCHAR(255)")
                    )
                    order_id_column_exists = True
                elif db_type == "postgresql":
                    await conn.execute(
                        text(
                            "ALTER TABLE wata_payments "
                            "ADD COLUMN IF NOT EXISTS order_id VARCHAR(255)"
                        )
                    )
                    order_id_column_exists = True
                elif db_type == "mysql":
                    await conn.execute(
                        text("ALTER TABLE wata_payments ADD COLUMN order_id VARCHAR(255)")
                    )
                    order_id_column_exists = True
                else:
                    logger.warning(
                        "⚠️ Неизвестный тип БД %s — пропущено добавление order_id",
                        db_type,
                    )

                if order_id_column_exists:
                    logger.info("✅ Добавлена колонка order_id в wata_payments")

            if not order_index_exists:
                if not order_id_column_exists:
                    logger.warning(
                        "⚠️ Пропущено создание индекса idx_wata_order_id — колонка order_id отсутствует"
                    )
                else:
                    index_created = False
                    if db_type in {"sqlite", "postgresql"}:
                        await conn.execute(
                            text(
                                "CREATE INDEX IF NOT EXISTS idx_wata_order_id ON wata_payments(order_id)"
                            )
                        )
                        index_created = True
                    elif db_type == "mysql":
                        await conn.execute(
                            text("CREATE INDEX idx_wata_order_id ON wata_payments(order_id)")
                        )
                        index_created = True
                    else:
                        logger.warning(
                            "⚠️ Неизвестный тип БД %s — пропущено создание индекса idx_wata_order_id",
                            db_type,
                        )

                    if index_created:
                        logger.info("✅ Создан индекс idx_wata_order_id")
            else:
                logger.info("ℹ️ Индекс idx_wata_order_id уже существует")

        return True

    except Exception as e:
        logger.error(f"Ошибка обновления схемы wata_payments: {e}")
        return False


async def create_discount_offers_table():
    table_exists = await check_table_exists('discount_offers')
    if table_exists:
        logger.info("Таблица discount_offers уже существует")
        return True

    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            if db_type == 'sqlite':
                await conn.execute(text("""
                    CREATE TABLE discount_offers (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        subscription_id INTEGER NULL,
                        notification_type VARCHAR(50) NOT NULL,
                        discount_percent INTEGER NOT NULL DEFAULT 0,
                        bonus_amount_kopeks INTEGER NOT NULL DEFAULT 0,
                        expires_at DATETIME NOT NULL,
                        claimed_at DATETIME NULL,
                        is_active BOOLEAN NOT NULL DEFAULT 1,
                        effect_type VARCHAR(50) NOT NULL DEFAULT 'percent_discount',
                        extra_data TEXT NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        FOREIGN KEY(subscription_id) REFERENCES subscriptions(id) ON DELETE SET NULL
                    )
                """))
                await conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS ix_discount_offers_user_type
                    ON discount_offers (user_id, notification_type)
                """))

            elif db_type == 'postgresql':
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS discount_offers (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        subscription_id INTEGER NULL REFERENCES subscriptions(id) ON DELETE SET NULL,
                        notification_type VARCHAR(50) NOT NULL,
                        discount_percent INTEGER NOT NULL DEFAULT 0,
                        bonus_amount_kopeks INTEGER NOT NULL DEFAULT 0,
                        expires_at TIMESTAMP NOT NULL,
                        claimed_at TIMESTAMP NULL,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        effect_type VARCHAR(50) NOT NULL DEFAULT 'percent_discount',
                        extra_data JSON NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                await conn.execute(text("""
                    CREATE INDEX IF NOT EXISTS ix_discount_offers_user_type
                    ON discount_offers (user_id, notification_type)
                """))

            elif db_type == 'mysql':
                await conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS discount_offers (
                        id INTEGER PRIMARY KEY AUTO_INCREMENT,
                        user_id INTEGER NOT NULL,
                        subscription_id INTEGER NULL,
                        notification_type VARCHAR(50) NOT NULL,
                        discount_percent INTEGER NOT NULL DEFAULT 0,
                        bonus_amount_kopeks INTEGER NOT NULL DEFAULT 0,
                        expires_at DATETIME NOT NULL,
                        claimed_at DATETIME NULL,
                        is_active BOOLEAN NOT NULL DEFAULT TRUE,
                        effect_type VARCHAR(50) NOT NULL DEFAULT 'percent_discount',
                        extra_data JSON NULL,
                        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                        CONSTRAINT fk_discount_offers_user FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                        CONSTRAINT fk_discount_offers_subscription FOREIGN KEY(subscription_id) REFERENCES subscriptions(id) ON DELETE SET NULL
                    )
                """))
                await conn.execute(text("""
                    CREATE INDEX ix_discount_offers_user_type
                    ON discount_offers (user_id, notification_type)
                """))

            else:
                raise ValueError(f"Unsupported database type: {db_type}")

        logger.info("✅ Таблица discount_offers успешно создана")
        return True

    except Exception as e:
        logger.error(f"Ошибка создания таблицы discount_offers: {e}")
        return False


async def ensure_discount_offer_columns():
    try:
        effect_exists = await check_column_exists('discount_offers', 'effect_type')
        extra_exists = await check_column_exists('discount_offers', 'extra_data')

        if effect_exists and extra_exists:
            return True

        async with engine.begin() as conn:
            db_type = await get_database_type()

            if not effect_exists:
                if db_type == 'sqlite':
                    await conn.execute(text(
                        "ALTER TABLE discount_offers ADD COLUMN effect_type VARCHAR(50) NOT NULL DEFAULT 'percent_discount'"
                    ))
                elif db_type == 'postgresql':
                    await conn.execute(text(
                        "ALTER TABLE discount_offers ADD COLUMN effect_type VARCHAR(50) NOT NULL DEFAULT 'percent_discount'"
                    ))
                elif db_type == 'mysql':
                    await conn.execute(text(
                        "ALTER TABLE discount_offers ADD COLUMN effect_type VARCHAR(50) NOT NULL DEFAULT 'percent_discount'"
                    ))
                else:
                    raise ValueError(f"Unsupported database type: {db_type}")

            if not extra_exists:
                if db_type == 'sqlite':
                    await conn.execute(text(
                        "ALTER TABLE discount_offers ADD COLUMN extra_data TEXT NULL"
                    ))
                elif db_type == 'postgresql':
                    await conn.execute(text(
                        "ALTER TABLE discount_offers ADD COLUMN extra_data JSON NULL"
                    ))
                elif db_type == 'mysql':
                    await conn.execute(text(
                        "ALTER TABLE discount_offers ADD COLUMN extra_data JSON NULL"
                    ))
                else:
                    raise ValueError(f"Unsupported database type: {db_type}")

        logger.info("✅ Колонки effect_type и extra_data для discount_offers проверены")
        return True

    except Exception as e:
        logger.error(f"Ошибка обновления колонок discount_offers: {e}")
        return False


async def ensure_user_promo_offer_discount_columns():
    try:
        percent_exists = await check_column_exists('users', 'promo_offer_discount_percent')
        source_exists = await check_column_exists('users', 'promo_offer_discount_source')
        expires_exists = await check_column_exists('users', 'promo_offer_discount_expires_at')

        if percent_exists and source_exists and expires_exists:
            return True

        async with engine.begin() as conn:
            db_type = await get_database_type()

            if not percent_exists:
                column_def = 'INTEGER NOT NULL DEFAULT 0'
                if db_type == 'mysql':
                    column_def = 'INT NOT NULL DEFAULT 0'
                await conn.execute(text(
                    f"ALTER TABLE users ADD COLUMN promo_offer_discount_percent {column_def}"
                ))

            if not source_exists:
                if db_type == 'sqlite':
                    column_def = 'TEXT NULL'
                elif db_type == 'postgresql':
                    column_def = 'VARCHAR(100) NULL'
                elif db_type == 'mysql':
                    column_def = 'VARCHAR(100) NULL'
                else:
                    raise ValueError(f"Unsupported database type: {db_type}")

                await conn.execute(text(
                    f"ALTER TABLE users ADD COLUMN promo_offer_discount_source {column_def}"
                ))

            if not expires_exists:
                if db_type == 'sqlite':
                    column_def = 'DATETIME NULL'
                elif db_type == 'postgresql':
                    column_def = 'TIMESTAMP NULL'
                elif db_type == 'mysql':
                    column_def = 'DATETIME NULL'
                else:
                    raise ValueError(f"Unsupported database type: {db_type}")

                await conn.execute(text(
                    f"ALTER TABLE users ADD COLUMN promo_offer_discount_expires_at {column_def}"
                ))

        logger.info("✅ Колонки promo_offer_discount_* для users проверены")
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления колонок promo_offer_discount_*: {e}")
        return False


async def ensure_promo_offer_template_active_duration_column() -> bool:
    try:
        column_exists = await check_column_exists('promo_offer_templates', 'active_discount_hours')

        async with engine.begin() as conn:
            db_type = await get_database_type()

            if not column_exists:
                if db_type == 'sqlite':
                    column_def = 'INTEGER NULL'
                elif db_type == 'postgresql':
                    column_def = 'INTEGER NULL'
                elif db_type == 'mysql':
                    column_def = 'INT NULL'
                else:
                    raise ValueError(f"Unsupported database type: {db_type}")

                await conn.execute(text(
                    f"ALTER TABLE promo_offer_templates ADD COLUMN active_discount_hours {column_def}"
                ))

            await conn.execute(text(
                "UPDATE promo_offer_templates "
                "SET active_discount_hours = valid_hours "
                "WHERE offer_type IN ('extend_discount', 'purchase_discount') "
                "AND (active_discount_hours IS NULL OR active_discount_hours <= 0)"
            ))

        logger.info("✅ Колонка active_discount_hours в promo_offer_templates актуальна")
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления active_discount_hours в promo_offer_templates: {e}")
        return False


async def migrate_discount_offer_effect_types():
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "UPDATE discount_offers SET effect_type = 'percent_discount' "
                "WHERE effect_type = 'balance_bonus'"
            ))
        logger.info("✅ Типы эффектов discount_offers обновлены на percent_discount")
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления типов эффектов discount_offers: {e}")
        return False


async def reset_discount_offer_bonuses():
    try:
        async with engine.begin() as conn:
            await conn.execute(text(
                "UPDATE discount_offers SET bonus_amount_kopeks = 0 WHERE bonus_amount_kopeks <> 0"
            ))
            await conn.execute(text(
                "UPDATE promo_offer_templates SET bonus_amount_kopeks = 0 WHERE bonus_amount_kopeks <> 0"
            ))
        logger.info("✅ Бонусы промо-предложений сброшены до нуля")
        return True
    except Exception as e:
        logger.error(f"Ошибка обнуления бонусов промо-предложений: {e}")
        return False


async def create_promo_offer_templates_table():
    return await create_table_from_definition(
        "promo_offer_templates", PROMO_OFFER_TEMPLATES_DEFINITION
    )

async def create_main_menu_buttons_table() -> bool:
    return await create_table_from_definition(
        "main_menu_buttons", MAIN_MENU_BUTTONS_DEFINITION
    )

async def create_promo_offer_logs_table() -> bool:
    return await create_table_from_definition(
        "promo_offer_logs", PROMO_OFFER_LOGS_DEFINITION
    )

async def create_subscription_temporary_access_table():
    return await create_table_from_definition(
        "subscription_temporary_access", SUBSCRIPTION_TEMP_ACCESS_DEFINITION
    )

async def create_user_messages_table():
    return await create_table_from_definition(
        "user_messages", USER_MESSAGES_DEFINITION
    )

async def ensure_promo_groups_setup():
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
                elif db_type == "mysql":
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
                else:
                    logger.error(f"Неподдерживаемый тип БД для promo_groups: {db_type}")
                    return False

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
                except Exception as e:
                    logger.warning(
                        f"Не удалось добавить уникальное ограничение uq_promo_groups_name: {e}"
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
                        text("UPDATE promo_groups SET period_discounts = '{}' WHERE period_discounts IS NULL")
                    )
                elif db_type == "postgresql":
                    await conn.execute(
                        text(
                            "ALTER TABLE promo_groups ADD COLUMN period_discounts JSONB"
                        )
                    )
                    await conn.execute(
                        text(
                            "UPDATE promo_groups SET period_discounts = '{}'::jsonb WHERE period_discounts IS NULL"
                        )
                    )
                elif db_type == "mysql":
                    await conn.execute(
                        text("ALTER TABLE promo_groups ADD COLUMN period_discounts JSON")
                    )
                    await conn.execute(
                        text(
                            "UPDATE promo_groups SET period_discounts = JSON_OBJECT() WHERE period_discounts IS NULL"
                        )
                    )
                else:
                    logger.error(
                        f"Неподдерживаемый тип БД для promo_groups.period_discounts: {db_type}"
                    )
                    return False

                logger.info("Добавлена колонка promo_groups.period_discounts")

            auto_assign_column_exists = await check_column_exists(
                "promo_groups", "auto_assign_total_spent_kopeks"
            )

            if not auto_assign_column_exists:
                if db_type == "sqlite":
                    await conn.execute(
                        text(
                            "ALTER TABLE promo_groups ADD COLUMN auto_assign_total_spent_kopeks INTEGER DEFAULT 0"
                        )
                    )
                elif db_type == "postgresql":
                    await conn.execute(
                        text(
                            "ALTER TABLE promo_groups ADD COLUMN auto_assign_total_spent_kopeks INTEGER DEFAULT 0"
                        )
                    )
                elif db_type == "mysql":
                    await conn.execute(
                        text(
                            "ALTER TABLE promo_groups ADD COLUMN auto_assign_total_spent_kopeks INT DEFAULT 0"
                        )
                    )
                else:
                    logger.error(
                        f"Неподдерживаемый тип БД для promo_groups.auto_assign_total_spent_kopeks: {db_type}"
                    )
                    return False

                logger.info(
                    "Добавлена колонка promo_groups.auto_assign_total_spent_kopeks"
                )

            addon_discount_column_exists = await check_column_exists(
                "promo_groups", "apply_discounts_to_addons"
            )
            priority_column_exists = await check_column_exists(
                "promo_groups", "priority"
            )

            if not addon_discount_column_exists:
                if db_type == "sqlite":
                    await conn.execute(
                        text(
                            "ALTER TABLE promo_groups ADD COLUMN apply_discounts_to_addons BOOLEAN NOT NULL DEFAULT 1"
                        )
                    )
                    await conn.execute(
                        text(
                            "UPDATE promo_groups SET apply_discounts_to_addons = 1 WHERE apply_discounts_to_addons IS NULL"
                        )
                    )
                elif db_type == "postgresql":
                    await conn.execute(
                        text(
                            "ALTER TABLE promo_groups ADD COLUMN apply_discounts_to_addons BOOLEAN NOT NULL DEFAULT TRUE"
                        )
                    )
                    await conn.execute(
                        text(
                            "UPDATE promo_groups SET apply_discounts_to_addons = TRUE WHERE apply_discounts_to_addons IS NULL"
                        )
                    )
                elif db_type == "mysql":
                    await conn.execute(
                        text(
                            "ALTER TABLE promo_groups ADD COLUMN apply_discounts_to_addons TINYINT(1) NOT NULL DEFAULT 1"
                        )
                    )
                    await conn.execute(
                        text(
                            "UPDATE promo_groups SET apply_discounts_to_addons = 1 WHERE apply_discounts_to_addons IS NULL"
                        )
                    )
                else:
                    logger.error(
                        f"Неподдерживаемый тип БД для promo_groups.apply_discounts_to_addons: {db_type}"
                    )
                    return False

                logger.info(
                    "Добавлена колонка promo_groups.apply_discounts_to_addons"
                )
                addon_discount_column_exists = True

            column_exists = await check_column_exists("users", "promo_group_id")

            if not column_exists:
                if db_type == "sqlite":
                    await conn.execute(text("ALTER TABLE users ADD COLUMN promo_group_id INTEGER"))
                elif db_type == "postgresql":
                    await conn.execute(text("ALTER TABLE users ADD COLUMN promo_group_id INTEGER"))
                elif db_type == "mysql":
                    await conn.execute(text("ALTER TABLE users ADD COLUMN promo_group_id INT"))
                else:
                    logger.error(f"Неподдерживаемый тип БД для promo_group_id: {db_type}")
                    return False

                logger.info("Добавлена колонка users.promo_group_id")

            auto_promo_flag_exists = await check_column_exists(
                "users", "auto_promo_group_assigned"
            )

            if not auto_promo_flag_exists:
                if db_type == "sqlite":
                    await conn.execute(
                        text(
                            "ALTER TABLE users ADD COLUMN auto_promo_group_assigned BOOLEAN DEFAULT 0"
                        )
                    )
                elif db_type == "postgresql":
                    await conn.execute(
                        text(
                            "ALTER TABLE users ADD COLUMN auto_promo_group_assigned BOOLEAN DEFAULT FALSE"
                        )
                    )
                elif db_type == "mysql":
                    await conn.execute(
                        text(
                            "ALTER TABLE users ADD COLUMN auto_promo_group_assigned TINYINT(1) DEFAULT 0"
                        )
                    )
                else:
                    logger.error(
                        f"Неподдерживаемый тип БД для users.auto_promo_group_assigned: {db_type}"
                    )
                    return False

                logger.info("Добавлена колонка users.auto_promo_group_assigned")

            threshold_column_exists = await check_column_exists(
                "users", "auto_promo_group_threshold_kopeks"
            )

            if not threshold_column_exists:
                if db_type == "sqlite":
                    await conn.execute(
                        text(
                            "ALTER TABLE users ADD COLUMN auto_promo_group_threshold_kopeks INTEGER NOT NULL DEFAULT 0"
                        )
                    )
                elif db_type == "postgresql":
                    await conn.execute(
                        text(
                            "ALTER TABLE users ADD COLUMN auto_promo_group_threshold_kopeks BIGINT NOT NULL DEFAULT 0"
                        )
                    )
                elif db_type == "mysql":
                    await conn.execute(
                        text(
                            "ALTER TABLE users ADD COLUMN auto_promo_group_threshold_kopeks BIGINT NOT NULL DEFAULT 0"
                        )
                    )
                else:
                    logger.error(
                        f"Неподдерживаемый тип БД для users.auto_promo_group_threshold_kopeks: {db_type}"
                    )
                    return False

                logger.info(
                    "Добавлена колонка users.auto_promo_group_threshold_kopeks"
                )

            index_exists = await check_index_exists("users", "ix_users_promo_group_id")

            if not index_exists:
                try:
                    if db_type == "sqlite":
                        await conn.execute(
                            text("CREATE INDEX IF NOT EXISTS ix_users_promo_group_id ON users(promo_group_id)")
                        )
                    elif db_type == "postgresql":
                        await conn.execute(
                            text("CREATE INDEX IF NOT EXISTS ix_users_promo_group_id ON users(promo_group_id)")
                        )
                    elif db_type == "mysql":
                        await conn.execute(
                            text("CREATE INDEX ix_users_promo_group_id ON users(promo_group_id)")
                        )
                    logger.info("Создан индекс ix_users_promo_group_id")
                except Exception as e:
                    logger.warning(f"Не удалось создать индекс ix_users_promo_group_id: {e}")

            default_group_name = "Базовый юзер"
            default_group_id = None

            result = await conn.execute(
                text(
                    "SELECT id, is_default FROM promo_groups WHERE name = :name LIMIT 1"
                ),
                {"name": default_group_name},
            )
            row = result.fetchone()

            if row:
                default_group_id = row[0]
                if not row[1]:
                    await conn.execute(
                        text(
                            "UPDATE promo_groups SET is_default = :is_default WHERE id = :group_id"
                        ),
                        {"is_default": True, "group_id": default_group_id},
                    )
            else:
                result = await conn.execute(
                    text(
                        "SELECT id FROM promo_groups WHERE is_default = :is_default LIMIT 1"
                    ),
                    {"is_default": True},
                )
                existing_default = result.fetchone()

                if existing_default:
                    default_group_id = existing_default[0]
                else:
                    insert_params = {
                        "name": default_group_name,
                        "is_default": True,
                    }

                    if priority_column_exists:
                        insert_params["priority"] = 0

                    if addon_discount_column_exists and priority_column_exists:
                        insert_sql = """
                            INSERT INTO promo_groups (
                                name,
                                priority,
                                server_discount_percent,
                                traffic_discount_percent,
                                device_discount_percent,
                                apply_discounts_to_addons,
                                is_default
                            ) VALUES (:name, :priority, 0, 0, 0, :apply_discounts_to_addons, :is_default)
                        """
                        insert_params["apply_discounts_to_addons"] = True
                    elif addon_discount_column_exists:
                        insert_sql = """
                            INSERT INTO promo_groups (
                                name,
                                server_discount_percent,
                                traffic_discount_percent,
                                device_discount_percent,
                                apply_discounts_to_addons,
                                is_default
                            ) VALUES (:name, 0, 0, 0, :apply_discounts_to_addons, :is_default)
                        """
                        insert_params["apply_discounts_to_addons"] = True
                    elif priority_column_exists:
                        insert_sql = """
                            INSERT INTO promo_groups (
                                name,
                                priority,
                                server_discount_percent,
                                traffic_discount_percent,
                                device_discount_percent,
                                is_default
                            ) VALUES (:name, :priority, 0, 0, 0, :is_default)
                        """
                    else:
                        insert_sql = """
                            INSERT INTO promo_groups (
                                name,
                                server_discount_percent,
                                traffic_discount_percent,
                                device_discount_percent,
                                is_default
                            ) VALUES (:name, 0, 0, 0, :is_default)
                        """

                    await conn.execute(text(insert_sql), insert_params)

                    result = await conn.execute(
                        text(
                            "SELECT id FROM promo_groups WHERE name = :name LIMIT 1"
                        ),
                        {"name": default_group_name},
                    )
                    row = result.fetchone()
                    default_group_id = row[0] if row else None

            if default_group_id is None:
                logger.error("Не удалось определить идентификатор базовой промо-группы")
                return False

            await conn.execute(
                text(
                    """
                    UPDATE users
                    SET promo_group_id = :group_id
                    WHERE promo_group_id IS NULL
                """
                ),
                {"group_id": default_group_id},
            )

            if db_type == "postgresql":
                constraint_exists = await check_constraint_exists(
                    "users", "fk_users_promo_group_id_promo_groups"
                )
                if not constraint_exists:
                    try:
                        await conn.execute(
                            text(
                                """
                                ALTER TABLE users
                                ADD CONSTRAINT fk_users_promo_group_id_promo_groups
                                FOREIGN KEY (promo_group_id)
                                REFERENCES promo_groups(id)
                                ON DELETE RESTRICT
                            """
                            )
                        )
                        logger.info("Добавлен внешний ключ users -> promo_groups")
                    except Exception as e:
                        logger.warning(
                            f"Не удалось добавить внешний ключ users.promo_group_id: {e}"
                        )

                try:
                    await conn.execute(
                        text(
                            "ALTER TABLE users ALTER COLUMN promo_group_id SET NOT NULL"
                        )
                    )
                except Exception as e:
                    logger.warning(
                        f"Не удалось сделать users.promo_group_id NOT NULL: {e}"
                    )

            elif db_type == "mysql":
                constraint_exists = await check_constraint_exists(
                    "users", "fk_users_promo_group_id_promo_groups"
                )
                if not constraint_exists:
                    try:
                        await conn.execute(
                            text(
                                """
                                ALTER TABLE users
                                ADD CONSTRAINT fk_users_promo_group_id_promo_groups
                                FOREIGN KEY (promo_group_id)
                                REFERENCES promo_groups(id)
                                ON DELETE RESTRICT
                            """
                            )
                        )
                        logger.info("Добавлен внешний ключ users -> promo_groups")
                    except Exception as e:
                        logger.warning(
                            f"Не удалось добавить внешний ключ users.promo_group_id: {e}"
                        )

                try:
                    await conn.execute(
                        text(
                            "ALTER TABLE users MODIFY promo_group_id INT NOT NULL"
                        )
                    )
                except Exception as e:
                    logger.warning(
                        f"Не удалось сделать users.promo_group_id NOT NULL: {e}"
                    )

            logger.info("✅ Промо группы настроены")
            return True

    except Exception as e:
        logger.error(f"Ошибка настройки промо групп: {e}")
        return False

async def add_welcome_text_is_enabled_column():
    column_exists = await check_column_exists('welcome_texts', 'is_enabled')
    if column_exists:
        logger.info("Колонка is_enabled уже существует в таблице welcome_texts")
        return True
    
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()
            
            if db_type == 'sqlite':
                alter_sql = "ALTER TABLE welcome_texts ADD COLUMN is_enabled BOOLEAN DEFAULT 1 NOT NULL"
            elif db_type == 'postgresql':
                alter_sql = "ALTER TABLE welcome_texts ADD COLUMN is_enabled BOOLEAN DEFAULT TRUE NOT NULL"
            elif db_type == 'mysql':
                alter_sql = "ALTER TABLE welcome_texts ADD COLUMN is_enabled BOOLEAN DEFAULT TRUE NOT NULL"
            else:
                logger.error(f"Неподдерживаемый тип БД для добавления колонки: {db_type}")
                return False
            
            await conn.execute(text(alter_sql))
            logger.info("✅ Поле is_enabled добавлено в таблицу welcome_texts")
            
            if db_type == 'sqlite':
                update_sql = "UPDATE welcome_texts SET is_enabled = 1 WHERE is_enabled IS NULL"
            else:
                update_sql = "UPDATE welcome_texts SET is_enabled = TRUE WHERE is_enabled IS NULL"
            
            result = await conn.execute(text(update_sql))
            updated_count = result.rowcount
            logger.info(f"Обновлено {updated_count} существующих записей welcome_texts")
            
            return True
            
    except Exception as e:
        logger.error(f"Ошибка при добавлении поля is_enabled: {e}")
        return False

async def create_welcome_texts_table():
    if await check_table_exists('welcome_texts'):
        logger.info('Таблица welcome_texts уже существует')
        return await add_welcome_text_is_enabled_column()

    created = await create_table_from_definition(
        'welcome_texts', WELCOME_TEXTS_DEFINITION
    )

    if not created:
        return False

    return True

async def add_media_fields_to_broadcast_history():
    logger.info("=== ДОБАВЛЕНИЕ ПОЛЕЙ МЕДИА В BROADCAST_HISTORY ===")
    
    media_fields = {
        'has_media': 'BOOLEAN DEFAULT FALSE',
        'media_type': 'VARCHAR(20)',
        'media_file_id': 'VARCHAR(255)', 
        'media_caption': 'TEXT'
    }
    
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()
            
            for field_name, field_type in media_fields.items():
                field_exists = await check_column_exists('broadcast_history', field_name)
                
                if not field_exists:
                    logger.info(f"Добавление поля {field_name} в таблицу broadcast_history")
                    
                    if db_type == 'sqlite':
                        if 'BOOLEAN' in field_type:
                            field_type = field_type.replace('BOOLEAN DEFAULT FALSE', 'BOOLEAN DEFAULT 0')
                    elif db_type == 'postgresql':
                        if 'BOOLEAN' in field_type:
                            field_type = field_type.replace('BOOLEAN DEFAULT FALSE', 'BOOLEAN DEFAULT FALSE')
                    elif db_type == 'mysql':
                        if 'BOOLEAN' in field_type:
                            field_type = field_type.replace('BOOLEAN DEFAULT FALSE', 'BOOLEAN DEFAULT FALSE')
                    
                    alter_sql = f"ALTER TABLE broadcast_history ADD COLUMN {field_name} {field_type}"
                    await conn.execute(text(alter_sql))
                    logger.info(f"✅ Поле {field_name} успешно добавлено")
                else:
                    logger.info(f"Поле {field_name} уже существует в broadcast_history")
            
            logger.info("✅ Все поля медиа в broadcast_history готовы")
            return True
            
    except Exception as e:
        logger.error(f"Ошибка при добавлении полей медиа в broadcast_history: {e}")
        return False


async def add_ticket_reply_block_columns():
    try:
        col_perm_exists = await check_column_exists('tickets', 'user_reply_block_permanent')
        col_until_exists = await check_column_exists('tickets', 'user_reply_block_until')

        if col_perm_exists and col_until_exists:
            return True

        async with engine.begin() as conn:
            db_type = await get_database_type()

            if not col_perm_exists:
                if db_type == 'sqlite':
                    alter_sql = "ALTER TABLE tickets ADD COLUMN user_reply_block_permanent BOOLEAN DEFAULT 0 NOT NULL"
                elif db_type == 'postgresql':
                    alter_sql = "ALTER TABLE tickets ADD COLUMN user_reply_block_permanent BOOLEAN DEFAULT FALSE NOT NULL"
                elif db_type == 'mysql':
                    alter_sql = "ALTER TABLE tickets ADD COLUMN user_reply_block_permanent BOOLEAN DEFAULT FALSE NOT NULL"
                else:
                    logger.error(f"Неподдерживаемый тип БД для добавления user_reply_block_permanent: {db_type}")
                    return False
                await conn.execute(text(alter_sql))
                logger.info("✅ Добавлена колонка tickets.user_reply_block_permanent")

            if not col_until_exists:
                if db_type == 'sqlite':
                    alter_sql = "ALTER TABLE tickets ADD COLUMN user_reply_block_until DATETIME NULL"
                elif db_type == 'postgresql':
                    alter_sql = "ALTER TABLE tickets ADD COLUMN user_reply_block_until TIMESTAMP NULL"
                elif db_type == 'mysql':
                    alter_sql = "ALTER TABLE tickets ADD COLUMN user_reply_block_until DATETIME NULL"
                else:
                    logger.error(f"Неподдерживаемый тип БД для добавления user_reply_block_until: {db_type}")
                    return False
                await conn.execute(text(alter_sql))
                logger.info("✅ Добавлена колонка tickets.user_reply_block_until")

            return True
    except Exception as e:
        logger.error(f"Ошибка добавления колонок блокировок в tickets: {e}")
        return False


async def add_ticket_sla_columns():
    try:
        col_exists = await check_column_exists('tickets', 'last_sla_reminder_at')
        if col_exists:
            return True
        async with engine.begin() as conn:
            db_type = await get_database_type()
            if db_type == 'sqlite':
                alter_sql = "ALTER TABLE tickets ADD COLUMN last_sla_reminder_at DATETIME NULL"
            elif db_type == 'postgresql':
                alter_sql = "ALTER TABLE tickets ADD COLUMN last_sla_reminder_at TIMESTAMP NULL"
            elif db_type == 'mysql':
                alter_sql = "ALTER TABLE tickets ADD COLUMN last_sla_reminder_at DATETIME NULL"
            else:
                logger.error(f"Неподдерживаемый тип БД для добавления last_sla_reminder_at: {db_type}")
                return False
            await conn.execute(text(alter_sql))
            logger.info("✅ Добавлена колонка tickets.last_sla_reminder_at")
            return True
    except Exception as e:
        logger.error(f"Ошибка добавления SLA колонки в tickets: {e}")
        return False


async def add_subscription_crypto_link_column() -> bool:
    column_exists = await check_column_exists('subscriptions', 'subscription_crypto_link')
    if column_exists:
        logger.info("ℹ️ Колонка subscription_crypto_link уже существует")
        return True

    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            if db_type == 'sqlite':
                await conn.execute(text("ALTER TABLE subscriptions ADD COLUMN subscription_crypto_link TEXT"))
            elif db_type == 'postgresql':
                await conn.execute(text("ALTER TABLE subscriptions ADD COLUMN subscription_crypto_link VARCHAR"))
            elif db_type == 'mysql':
                await conn.execute(text("ALTER TABLE subscriptions ADD COLUMN subscription_crypto_link VARCHAR(512)"))
            else:
                logger.error(f"Неподдерживаемый тип БД для добавления subscription_crypto_link: {db_type}")
                return False

            await conn.execute(text(
                "UPDATE subscriptions SET subscription_crypto_link = subscription_url "
                "WHERE subscription_crypto_link IS NULL OR subscription_crypto_link = ''"
            ))

        logger.info("✅ Добавлена колонка subscription_crypto_link в таблицу subscriptions")
        return True
    except Exception as e:
        logger.error(f"Ошибка добавления колонки subscription_crypto_link: {e}")
        return False


async def fix_foreign_keys_for_user_deletion():
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()
            
            if db_type == 'postgresql':
                try:
                    await conn.execute(text("""
                        ALTER TABLE user_messages 
                        DROP CONSTRAINT IF EXISTS user_messages_created_by_fkey;
                    """))
                    
                    await conn.execute(text("""
                        ALTER TABLE user_messages 
                        ADD CONSTRAINT user_messages_created_by_fkey 
                        FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL;
                    """))
                    logger.info("Обновлен внешний ключ user_messages.created_by")
                except Exception as e:
                    logger.warning(f"Ошибка обновления FK user_messages: {e}")
                
                try:
                    await conn.execute(text("""
                        ALTER TABLE promocodes 
                        DROP CONSTRAINT IF EXISTS promocodes_created_by_fkey;
                    """))
                    
                    await conn.execute(text("""
                        ALTER TABLE promocodes 
                        ADD CONSTRAINT promocodes_created_by_fkey 
                        FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL;
                    """))
                    logger.info("Обновлен внешний ключ promocodes.created_by")
                except Exception as e:
                    logger.warning(f"Ошибка обновления FK promocodes: {e}")
            
            logger.info("Внешние ключи обновлены для безопасного удаления пользователей")
            return True
            
    except Exception as e:
        logger.error(f"Ошибка обновления внешних ключей: {e}")
        return False

async def add_referral_system_columns():
    logger.info("=== МИГРАЦИЯ РЕФЕРАЛЬНОЙ СИСТЕМЫ ===")
    
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()
            
            column_exists = await check_column_exists('users', 'has_made_first_topup')
            
            if not column_exists:
                logger.info("Добавление колонки has_made_first_topup в таблицу users")
                
                if db_type == 'sqlite':
                    column_def = 'BOOLEAN DEFAULT 0'
                else:
                    column_def = 'BOOLEAN DEFAULT FALSE'
                
                await conn.execute(text(f"ALTER TABLE users ADD COLUMN has_made_first_topup {column_def}"))
                logger.info("Колонка has_made_first_topup успешно добавлена")
                
                logger.info("Обновление существующих пользователей...")
                
                if db_type == 'sqlite':
                    update_sql = """
                        UPDATE users 
                        SET has_made_first_topup = 1 
                        WHERE balance_kopeks > 0 OR has_had_paid_subscription = 1
                    """
                else:
                    update_sql = """
                        UPDATE users 
                        SET has_made_first_topup = TRUE 
                        WHERE balance_kopeks > 0 OR has_had_paid_subscription = TRUE
                    """
                
                result = await conn.execute(text(update_sql))
                updated_count = result.rowcount
                
                logger.info(f"Обновлено {updated_count} пользователей с has_made_first_topup = TRUE")
                logger.info("✅ Миграция реферальной системы завершена")
                
                return True
            else:
                logger.info("Колонка has_made_first_topup уже существует")
                return True
                
    except Exception as e:
        logger.error(f"Ошибка миграции реферальной системы: {e}")
        return False

async def create_subscription_conversions_table():
    return await create_table_from_definition(
        'subscription_conversions', SUBSCRIPTION_CONVERSIONS_DEFINITION
    )

async def fix_subscription_duplicates_universal():
    async with engine.begin() as conn:
        db_type = await get_database_type()
        logger.info(f"Обнаружен тип базы данных: {db_type}")
        
        try:
            result = await conn.execute(text("""
                SELECT user_id, COUNT(*) as count 
                FROM subscriptions 
                GROUP BY user_id 
                HAVING COUNT(*) > 1
            """))
            
            duplicates = result.fetchall()
            
            if not duplicates:
                logger.info("Дублирующихся подписок не найдено")
                return 0
                
            logger.info(f"Найдено {len(duplicates)} пользователей с дублирующимися подписками")
            
            total_deleted = 0
            
            for user_id_row, count in duplicates:
                user_id = user_id_row
                
                if db_type == 'sqlite':
                    delete_result = await conn.execute(text("""
                        DELETE FROM subscriptions 
                        WHERE user_id = :user_id AND id NOT IN (
                            SELECT MAX(id) 
                            FROM subscriptions 
                            WHERE user_id = :user_id
                        )
                    """), {"user_id": user_id})
                    
                elif db_type in ['postgresql', 'mysql']:
                    delete_result = await conn.execute(text("""
                        DELETE FROM subscriptions 
                        WHERE user_id = :user_id AND id NOT IN (
                            SELECT max_id FROM (
                                SELECT MAX(id) as max_id
                                FROM subscriptions 
                                WHERE user_id = :user_id
                            ) as subquery
                        )
                    """), {"user_id": user_id})
                
                else:
                    subs_result = await conn.execute(text("""
                        SELECT id FROM subscriptions 
                        WHERE user_id = :user_id 
                        ORDER BY created_at DESC, id DESC
                    """), {"user_id": user_id})
                    
                    sub_ids = [row[0] for row in subs_result.fetchall()]
                    
                    if len(sub_ids) > 1:
                        ids_to_delete = sub_ids[1:]
                        for sub_id in ids_to_delete:
                            await conn.execute(text("""
                                DELETE FROM subscriptions WHERE id = :id
                            """), {"id": sub_id})
                        delete_result = type('Result', (), {'rowcount': len(ids_to_delete)})()
                    else:
                        delete_result = type('Result', (), {'rowcount': 0})()
                
                deleted_count = delete_result.rowcount
                total_deleted += deleted_count
                logger.info(f"Удалено {deleted_count} дублирующихся подписок для пользователя {user_id}")

            logger.info(f"Всего удалено дублирующихся подписок: {total_deleted}")
            return total_deleted

        except Exception as e:
            logger.error(f"Ошибка при очистке дублирующихся подписок: {e}")
            raise


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
                    create_index_sql = """
                    CREATE INDEX IF NOT EXISTS idx_server_squad_promo_groups_promo ON server_squad_promo_groups(promo_group_id);
                    """
                elif db_type == "postgresql":
                    create_table_sql = """
                    CREATE TABLE server_squad_promo_groups (
                        server_squad_id INTEGER NOT NULL REFERENCES server_squads(id) ON DELETE CASCADE,
                        promo_group_id INTEGER NOT NULL REFERENCES promo_groups(id) ON DELETE CASCADE,
                        PRIMARY KEY (server_squad_id, promo_group_id)
                    );
                    """
                    create_index_sql = """
                    CREATE INDEX IF NOT EXISTS idx_server_squad_promo_groups_promo ON server_squad_promo_groups(promo_group_id);
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
                    create_index_sql = """
                    CREATE INDEX IF NOT EXISTS idx_server_squad_promo_groups_promo ON server_squad_promo_groups(promo_group_id);
                    """

                await conn.execute(text(create_table_sql))
                await conn.execute(text(create_index_sql))
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
                        "SELECT 1 FROM server_squad_promo_groups WHERE server_squad_id = :sid LIMIT 1"
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
                    f"✅ Базовая промогруппа назначена {assigned_count} серверам"
                )
            else:
                logger.info("ℹ️ Все серверы уже имеют назначенные промогруппы")

        return True

    except Exception as e:
        logger.error(
            f"Ошибка настройки таблицы server_squad_promo_groups: {e}"
        )
        return False


async def add_server_trial_flag_column() -> bool:
    column_exists = await check_column_exists('server_squads', 'is_trial_eligible')
    if column_exists:
        logger.info("Колонка is_trial_eligible уже существует в server_squads")
        return True

    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            if db_type == 'sqlite':
                column_def = 'BOOLEAN NOT NULL DEFAULT 0'
            elif db_type == 'postgresql':
                column_def = 'BOOLEAN NOT NULL DEFAULT FALSE'
            else:
                column_def = 'BOOLEAN NOT NULL DEFAULT FALSE'

            await conn.execute(
                text(f"ALTER TABLE server_squads ADD COLUMN is_trial_eligible {column_def}")
            )

            if db_type == 'postgresql':
                await conn.execute(
                    text("ALTER TABLE server_squads ALTER COLUMN is_trial_eligible SET DEFAULT FALSE")
                )

        logger.info("✅ Добавлена колонка is_trial_eligible в server_squads")
        return True

    except Exception as error:
        logger.error(f"Ошибка добавления колонки is_trial_eligible: {error}")
        return False


async def create_system_settings_table() -> bool:
    return await create_table_from_definition(
        'system_settings', SYSTEM_SETTINGS_DEFINITION
    )

async def create_web_api_tokens_table() -> bool:
    return await create_table_from_definition(
        'web_api_tokens', WEB_API_TOKENS_DEFINITION
    )


async def ensure_default_web_api_token() -> bool:
    """Создает или активирует дефолтный токен веб-API из настроек."""

    default_token = (settings.WEB_API_DEFAULT_TOKEN or "").strip()
    if not default_token:
        logger.debug("WEB_API_DEFAULT_TOKEN не задан, пропускаем создание токена")
        return True

    token_name = (settings.WEB_API_DEFAULT_TOKEN_NAME or "Bootstrap Token").strip()

    try:
        async with AsyncSessionLocal() as session:
            token_hash = hash_api_token(
                default_token,
                settings.WEB_API_TOKEN_HASH_ALGORITHM,
            )

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

                logger.debug("ℹ️ Дефолтный веб-API токен уже существует")
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
        logger.error(f"❌ Ошибка создания дефолтного веб-API токена: {error}")
        return False


async def create_privacy_policies_table() -> bool:
    return await create_table_from_definition(
        'privacy_policies', PRIVACY_POLICIES_DEFINITION
    )

async def create_public_offers_table() -> bool:
    return await create_table_from_definition(
        'public_offers', PUBLIC_OFFERS_DEFINITION
    )

async def create_faq_settings_table() -> bool:
    return await create_table_from_definition(
        'faq_settings', FAQ_SETTINGS_DEFINITION
    )

async def create_faq_pages_table() -> bool:
    return await create_table_from_definition(
        'faq_pages', FAQ_PAGES_DEFINITION
    )

async def create_user_promo_groups_table() -> bool:
    return await create_table_from_definition(
        'user_promo_groups', USER_PROMO_GROUPS_DEFINITION
    )

async def migrate_existing_user_promo_groups_data() -> bool:
    """Переносит существующие связи users.promo_group_id в таблицу user_promo_groups."""
    try:
        table_exists = await check_table_exists("user_promo_groups")
        if not table_exists:
            logger.warning("⚠️ Таблица user_promo_groups не существует, пропускаем миграцию данных")
            return False

        column_exists = await check_column_exists('users', 'promo_group_id')
        if not column_exists:
            logger.warning("⚠️ Колонка users.promo_group_id не существует, пропускаем миграцию данных")
            return True

        async with engine.begin() as conn:
            # Проверяем есть ли уже данные в user_promo_groups
            result = await conn.execute(text("SELECT COUNT(*) FROM user_promo_groups"))
            count = result.scalar()

            if count > 0:
                logger.info(f"ℹ️ В таблице user_promo_groups уже есть {count} записей, пропускаем миграцию")
                return True

            # Переносим данные из users.promo_group_id
            db_type = await get_database_type()

            if db_type == "sqlite":
                migrate_sql = """
                INSERT INTO user_promo_groups (user_id, promo_group_id, assigned_at, assigned_by)
                SELECT id, promo_group_id, CURRENT_TIMESTAMP, 'system'
                FROM users
                WHERE promo_group_id IS NOT NULL
                """
            else:  # PostgreSQL and MySQL
                migrate_sql = """
                INSERT INTO user_promo_groups (user_id, promo_group_id, assigned_at, assigned_by)
                SELECT id, promo_group_id, NOW(), 'system'
                FROM users
                WHERE promo_group_id IS NOT NULL
                """

            result = await conn.execute(text(migrate_sql))
            migrated_count = result.rowcount if hasattr(result, 'rowcount') else 0

            logger.info(f"✅ Перенесено {migrated_count} связей пользователей с промогруппами")
            return True

    except Exception as error:
        logger.error(f"❌ Ошибка миграции данных user_promo_groups: {error}")
        return False


async def add_promocode_promo_group_column() -> bool:
    """Добавляет колонку promo_group_id в таблицу promocodes."""
    column_exists = await check_column_exists('promocodes', 'promo_group_id')
    if column_exists:
        logger.info("Колонка promo_group_id уже существует в promocodes")
        return True

    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()

            # Add column
            if db_type == 'sqlite':
                await conn.execute(
                    text("ALTER TABLE promocodes ADD COLUMN promo_group_id INTEGER")
                )
            elif db_type == 'postgresql':
                await conn.execute(
                    text("ALTER TABLE promocodes ADD COLUMN promo_group_id INTEGER")
                )
                # Add foreign key
                await conn.execute(
                    text("""
                        ALTER TABLE promocodes
                        ADD CONSTRAINT fk_promocodes_promo_group
                        FOREIGN KEY (promo_group_id)
                        REFERENCES promo_groups(id)
                        ON DELETE SET NULL
                    """)
                )
                # Add index
                await conn.execute(
                    text("CREATE INDEX IF NOT EXISTS idx_promocodes_promo_group_id ON promocodes(promo_group_id)")
                )
            elif db_type == 'mysql':
                await conn.execute(
                    text("""
                        ALTER TABLE promocodes
                        ADD COLUMN promo_group_id INT,
                        ADD CONSTRAINT fk_promocodes_promo_group
                        FOREIGN KEY (promo_group_id)
                        REFERENCES promo_groups(id)
                        ON DELETE SET NULL
                    """)
                )
                await conn.execute(
                    text("CREATE INDEX idx_promocodes_promo_group_id ON promocodes(promo_group_id)")
                )

        logger.info("✅ Добавлена колонка promo_group_id в promocodes")
        return True

    except Exception as error:
        logger.error(f"❌ Ошибка добавления promo_group_id в promocodes: {error}")
        return False


async def run_universal_migration():
    logger.info("=== НАЧАЛО УНИВЕРСАЛЬНОЙ МИГРАЦИИ ===")
    
    try:
        db_type = await get_database_type()
        logger.info(f"Тип базы данных: {db_type}")

        if db_type == 'postgresql':
            logger.info("=== СИНХРОНИЗАЦИЯ ПОСЛЕДОВАТЕЛЬНОСТЕЙ PostgreSQL ===")
            sequences_synced = await sync_postgres_sequences()
            if sequences_synced:
                logger.info("✅ Последовательности PostgreSQL синхронизированы")
            else:
                logger.warning("⚠️ Не удалось синхронизировать последовательности PostgreSQL")

        referral_migration_success = await add_referral_system_columns()
        if not referral_migration_success:
            logger.warning("⚠️ Проблемы с миграцией реферальной системы")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ SYSTEM_SETTINGS ===")
        system_settings_ready = await create_system_settings_table()
        if system_settings_ready:
            logger.info("✅ Таблица system_settings готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей system_settings")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ WEB_API_TOKENS ===")
        web_api_tokens_ready = await create_web_api_tokens_table()
        if web_api_tokens_ready:
            logger.info("✅ Таблица web_api_tokens готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей web_api_tokens")

        logger.info("=== ДОБАВЛЕНИЕ КОЛОНКИ ДЛЯ ТРИАЛЬНЫХ СКВАДОВ ===")
        trial_column_ready = await add_server_trial_flag_column()
        if trial_column_ready:
            logger.info("✅ Колонка is_trial_eligible готова")
        else:
            logger.warning("⚠️ Проблемы с колонкой is_trial_eligible")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ PRIVACY_POLICIES ===")
        privacy_policies_ready = await create_privacy_policies_table()
        if privacy_policies_ready:
            logger.info("✅ Таблица privacy_policies готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей privacy_policies")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ PUBLIC_OFFERS ===")
        public_offers_ready = await create_public_offers_table()
        if public_offers_ready:
            logger.info("✅ Таблица public_offers готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей public_offers")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ FAQ_SETTINGS ===")
        faq_settings_ready = await create_faq_settings_table()
        if faq_settings_ready:
            logger.info("✅ Таблица faq_settings готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей faq_settings")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ FAQ_PAGES ===")
        faq_pages_ready = await create_faq_pages_table()
        if faq_pages_ready:
            logger.info("✅ Таблица faq_pages готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей faq_pages")

        logger.info("=== ПРОВЕРКА БАЗОВЫХ ТОКЕНОВ ВЕБ-API ===")
        default_token_ready = await ensure_default_web_api_token()
        if default_token_ready:
            logger.info("✅ Бутстрап токен веб-API готов")
        else:
            logger.warning("⚠️ Не удалось создать бутстрап токен веб-API")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ CRYPTOBOT ===")
        cryptobot_created = await create_cryptobot_payments_table()
        if cryptobot_created:
            logger.info("✅ Таблица CryptoBot payments готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей CryptoBot payments")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ HELEKET ===")
        heleket_created = await create_heleket_payments_table()
        if heleket_created:
            logger.info("✅ Таблица Heleket payments готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей Heleket payments")

        mulenpay_name = settings.get_mulenpay_display_name()
        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ %s ===", mulenpay_name)
        mulenpay_created = await create_mulenpay_payments_table()
        if mulenpay_created:
            logger.info("✅ Таблица %s payments готова", mulenpay_name)
        else:
            logger.warning("⚠️ Проблемы с таблицей %s payments", mulenpay_name)

        mulenpay_schema_ok = await ensure_mulenpay_payment_schema()
        if mulenpay_schema_ok:
            logger.info("✅ Схема %s payments актуальна", mulenpay_name)
        else:
            logger.warning("⚠️ Не удалось обновить схему %s payments", mulenpay_name)

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ PAL24 ===")
        pal24_created = await create_pal24_payments_table()
        if pal24_created:
            logger.info("✅ Таблица Pal24 payments готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей Pal24 payments")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ WATA ===")
        wata_created = await create_wata_payments_table()
        if wata_created:
            logger.info("✅ Таблица Wata payments готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей Wata payments")

        wata_schema_ok = await ensure_wata_payment_schema()
        if wata_schema_ok:
            logger.info("✅ Схема Wata payments актуальна")
        else:
            logger.warning("⚠️ Не удалось обновить схему Wata payments")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ DISCOUNT_OFFERS ===")
        discount_created = await create_discount_offers_table()
        if discount_created:
            logger.info("✅ Таблица discount_offers готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей discount_offers")

        discount_columns_ready = await ensure_discount_offer_columns()
        if discount_columns_ready:
            logger.info("✅ Колонки discount_offers в актуальном состоянии")
        else:
            logger.warning("⚠️ Не удалось обновить колонки discount_offers")

        user_discount_columns_ready = await ensure_user_promo_offer_discount_columns()
        if user_discount_columns_ready:
            logger.info("✅ Колонки пользовательских промо-скидок готовы")
        else:
            logger.warning("⚠️ Не удалось обновить пользовательские промо-скидки")

        effect_types_updated = await migrate_discount_offer_effect_types()
        if effect_types_updated:
            logger.info("✅ Типы эффектов промо-предложений обновлены")
        else:
            logger.warning("⚠️ Не удалось обновить типы эффектов промо-предложений")

        bonuses_reset = await reset_discount_offer_bonuses()
        if bonuses_reset:
            logger.info("✅ Бонусные начисления промо-предложений отключены")
        else:
            logger.warning("⚠️ Не удалось обнулить бонусы промо-предложений")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ PROMO_OFFER_TEMPLATES ===")
        promo_templates_created = await create_promo_offer_templates_table()
        if promo_templates_created:
            logger.info("✅ Таблица promo_offer_templates готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей promo_offer_templates")

        logger.info("=== ДОБАВЛЕНИЕ ПРИОРИТЕТА В ПРОМОГРУППЫ ===")
        priority_column_ready = await add_promo_group_priority_column()
        if priority_column_ready:
            logger.info("✅ Колонка priority в promo_groups готова")
        else:
            logger.warning("⚠️ Проблемы с добавлением priority в promo_groups")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ USER_PROMO_GROUPS ===")
        user_promo_groups_ready = await create_user_promo_groups_table()
        if user_promo_groups_ready:
            logger.info("✅ Таблица user_promo_groups готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей user_promo_groups")

        logger.info("=== МИГРАЦИЯ ДАННЫХ В USER_PROMO_GROUPS ===")
        data_migrated = await migrate_existing_user_promo_groups_data()
        if data_migrated:
            logger.info("✅ Данные перенесены в user_promo_groups")
        else:
            logger.warning("⚠️ Проблемы с миграцией данных в user_promo_groups")

        logger.info("=== ДОБАВЛЕНИЕ PROMO_GROUP_ID В PROMOCODES ===")
        promocode_column_ready = await add_promocode_promo_group_column()
        if promocode_column_ready:
            logger.info("✅ Колонка promo_group_id в promocodes готова")
        else:
            logger.warning("⚠️ Проблемы с добавлением promo_group_id в promocodes")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ MAIN_MENU_BUTTONS ===")
        main_menu_buttons_created = await create_main_menu_buttons_table()
        if main_menu_buttons_created:
            logger.info("✅ Таблица main_menu_buttons готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей main_menu_buttons")

        template_columns_ready = await ensure_promo_offer_template_active_duration_column()
        if template_columns_ready:
            logger.info("✅ Колонка active_discount_hours промо-предложений готова")
        else:
            logger.warning("⚠️ Не удалось обновить колонку active_discount_hours промо-предложений")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ PROMO_OFFER_LOGS ===")
        promo_logs_created = await create_promo_offer_logs_table()
        if promo_logs_created:
            logger.info("✅ Таблица promo_offer_logs готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей promo_offer_logs")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ SUBSCRIPTION_TEMPORARY_ACCESS ===")
        temp_access_created = await create_subscription_temporary_access_table()
        if temp_access_created:
            logger.info("✅ Таблица subscription_temporary_access готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей subscription_temporary_access")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ USER_MESSAGES ===")
        user_messages_created = await create_user_messages_table()
        if user_messages_created:
            logger.info("✅ Таблица user_messages готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей user_messages")

        logger.info("=== СОЗДАНИЕ/ОБНОВЛЕНИЕ ТАБЛИЦЫ WELCOME_TEXTS ===")
        welcome_texts_created = await create_welcome_texts_table()
        if welcome_texts_created:
            logger.info("✅ Таблица welcome_texts готова с полем is_enabled")
        else:
            logger.warning("⚠️ Проблемы с таблицей welcome_texts")
        
        logger.info("=== ДОБАВЛЕНИЕ МЕДИА ПОЛЕЙ В BROADCAST_HISTORY ===")
        media_fields_added = await add_media_fields_to_broadcast_history()
        if media_fields_added:
            logger.info("✅ Медиа поля в broadcast_history готовы")
        else:
            logger.warning("⚠️ Проблемы с добавлением медиа полей")

        logger.info("=== ДОБАВЛЕНИЕ ПОЛЕЙ БЛОКИРОВКИ В TICKETS ===")
        tickets_block_cols_added = await add_ticket_reply_block_columns()
        if tickets_block_cols_added:
            logger.info("✅ Поля блокировок в tickets готовы")
        else:
            logger.warning("⚠️ Проблемы с добавлением полей блокировок в tickets")

        logger.info("=== ДОБАВЛЕНИЕ ПОЛЕЙ SLA В TICKETS ===")
        sla_cols_added = await add_ticket_sla_columns()
        if sla_cols_added:
            logger.info("✅ Поля SLA в tickets готовы")
        else:
            logger.warning("⚠️ Проблемы с добавлением полей SLA в tickets")

        logger.info("=== ДОБАВЛЕНИЕ КОЛОНКИ CRYPTO LINK ДЛЯ ПОДПИСОК ===")
        crypto_link_added = await add_subscription_crypto_link_column()
        if crypto_link_added:
            logger.info("✅ Колонка subscription_crypto_link готова")
        else:
            logger.warning("⚠️ Проблемы с добавлением колонки subscription_crypto_link")

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ АУДИТА ПОДДЕРЖКИ ===")
        try:
            if not await check_table_exists('support_audit_logs'):
                created = await create_table_from_definition(
                    'support_audit_logs', SUPPORT_AUDIT_LOGS_DEFINITION
                )
                if created:
                    logger.info("✅ Таблица support_audit_logs создана")
                else:
                    logger.warning("⚠️ Проблемы с созданием таблицы support_audit_logs")
            else:
                logger.info("ℹ️ Таблица support_audit_logs уже существует")
        except Exception as e:
            logger.warning(f"⚠️ Проблемы с созданием таблицы support_audit_logs: {e}")

        logger.info("=== НАСТРОЙКА ПРОМО ГРУПП ===")
        promo_groups_ready = await ensure_promo_groups_setup()
        if promo_groups_ready:
            logger.info("✅ Промо группы готовы")
        else:
            logger.warning("⚠️ Проблемы с настройкой промо групп")

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
        
        async with engine.begin() as conn:
            total_subs = await conn.execute(text("SELECT COUNT(*) FROM subscriptions"))
            unique_users = await conn.execute(text("SELECT COUNT(DISTINCT user_id) FROM subscriptions"))
            
            total_count = total_subs.fetchone()[0]
            unique_count = unique_users.fetchone()[0]
            
            logger.info(f"Всего подписок: {total_count}")
            logger.info(f"Уникальных пользователей: {unique_count}")
            
            if total_count == unique_count:
                logger.info("База данных уже в корректном состоянии")
                logger.info("=== МИГРАЦИЯ ЗАВЕРШЕНА УСПЕШНО ===")
                return True
        
        deleted_count = await fix_subscription_duplicates_universal()
        
        async with engine.begin() as conn:
            final_check = await conn.execute(text("""
                SELECT user_id, COUNT(*) as count 
                FROM subscriptions 
                GROUP BY user_id 
                HAVING COUNT(*) > 1
            """))
            
            remaining_duplicates = final_check.fetchall()
            
            if remaining_duplicates:
                logger.warning(f"Остались дубликаты у {len(remaining_duplicates)} пользователей")
                return False
            else:
                logger.info("=== МИГРАЦИЯ ЗАВЕРШЕНА УСПЕШНО ===")
                logger.info("✅ Реферальная система обновлена")
                logger.info("✅ CryptoBot таблица готова")
                logger.info("✅ Heleket таблица готова")
                logger.info("✅ Таблица конверсий подписок создана")
                logger.info("✅ Таблица welcome_texts с полем is_enabled готова")
                logger.info("✅ Медиа поля в broadcast_history добавлены")
                logger.info("✅ Дубликаты подписок исправлены")
                return True
                
    except Exception as e:
        logger.error(f"=== ОШИБКА ВЫПОЛНЕНИЯ МИГРАЦИИ: {e} ===")
        return False

async def check_migration_status():
    logger.info("=== ПРОВЕРКА СТАТУСА МИГРАЦИЙ ===")
    
    try:
        status = {
            "has_made_first_topup_column": False,
            "cryptobot_table": False,
            "heleket_table": False,
            "user_messages_table": False,
            "welcome_texts_table": False,
            "welcome_texts_is_enabled_column": False,
            "broadcast_history_media_fields": False,
            "subscription_duplicates": False,
            "subscription_conversions_table": False,
            "promo_groups_table": False,
            "server_promo_groups_table": False,
            "server_squads_trial_column": False,
            "privacy_policies_table": False,
            "public_offers_table": False,
            "users_promo_group_column": False,
            "promo_groups_period_discounts_column": False,
            "promo_groups_auto_assign_column": False,
            "promo_groups_addon_discount_column": False,
            "users_auto_promo_group_assigned_column": False,
            "users_auto_promo_group_threshold_column": False,
            "users_promo_offer_discount_percent_column": False,
            "users_promo_offer_discount_source_column": False,
            "users_promo_offer_discount_expires_column": False,
            "subscription_crypto_link_column": False,
            "discount_offers_table": False,
            "discount_offers_effect_column": False,
            "discount_offers_extra_column": False,
            "promo_offer_templates_table": False,
            "promo_offer_templates_active_discount_column": False,
            "promo_offer_logs_table": False,
            "subscription_temporary_access_table": False,
        }
        
        status["has_made_first_topup_column"] = await check_column_exists('users', 'has_made_first_topup')
        
        status["cryptobot_table"] = await check_table_exists('cryptobot_payments')
        status["heleket_table"] = await check_table_exists('heleket_payments')
        status["user_messages_table"] = await check_table_exists('user_messages')
        status["welcome_texts_table"] = await check_table_exists('welcome_texts')
        status["privacy_policies_table"] = await check_table_exists('privacy_policies')
        status["public_offers_table"] = await check_table_exists('public_offers')
        status["subscription_conversions_table"] = await check_table_exists('subscription_conversions')
        status["promo_groups_table"] = await check_table_exists('promo_groups')
        status["server_promo_groups_table"] = await check_table_exists('server_squad_promo_groups')
        status["server_squads_trial_column"] = await check_column_exists('server_squads', 'is_trial_eligible')

        status["discount_offers_table"] = await check_table_exists('discount_offers')
        status["discount_offers_effect_column"] = await check_column_exists('discount_offers', 'effect_type')
        status["discount_offers_extra_column"] = await check_column_exists('discount_offers', 'extra_data')
        status["promo_offer_templates_table"] = await check_table_exists('promo_offer_templates')
        status["promo_offer_templates_active_discount_column"] = await check_column_exists('promo_offer_templates', 'active_discount_hours')
        status["promo_offer_logs_table"] = await check_table_exists('promo_offer_logs')
        status["subscription_temporary_access_table"] = await check_table_exists('subscription_temporary_access')

        status["welcome_texts_is_enabled_column"] = await check_column_exists('welcome_texts', 'is_enabled')
        status["users_promo_group_column"] = await check_column_exists('users', 'promo_group_id')
        status["promo_groups_period_discounts_column"] = await check_column_exists('promo_groups', 'period_discounts')
        status["promo_groups_auto_assign_column"] = await check_column_exists('promo_groups', 'auto_assign_total_spent_kopeks')
        status["promo_groups_addon_discount_column"] = await check_column_exists('promo_groups', 'apply_discounts_to_addons')
        status["users_auto_promo_group_assigned_column"] = await check_column_exists('users', 'auto_promo_group_assigned')
        status["users_auto_promo_group_threshold_column"] = await check_column_exists('users', 'auto_promo_group_threshold_kopeks')
        status["users_promo_offer_discount_percent_column"] = await check_column_exists('users', 'promo_offer_discount_percent')
        status["users_promo_offer_discount_source_column"] = await check_column_exists('users', 'promo_offer_discount_source')
        status["users_promo_offer_discount_expires_column"] = await check_column_exists('users', 'promo_offer_discount_expires_at')
        status["subscription_crypto_link_column"] = await check_column_exists('subscriptions', 'subscription_crypto_link')
        
        media_fields_exist = (
            await check_column_exists('broadcast_history', 'has_media') and
            await check_column_exists('broadcast_history', 'media_type') and
            await check_column_exists('broadcast_history', 'media_file_id') and
            await check_column_exists('broadcast_history', 'media_caption')
        )
        status["broadcast_history_media_fields"] = media_fields_exist
        
        async with engine.begin() as conn:
            duplicates_check = await conn.execute(text("""
                SELECT COUNT(*) FROM (
                    SELECT user_id, COUNT(*) as count 
                    FROM subscriptions 
                    GROUP BY user_id 
                    HAVING COUNT(*) > 1
                ) as dups
            """))
            duplicates_count = duplicates_check.fetchone()[0]
            status["subscription_duplicates"] = (duplicates_count == 0)
        
        check_names = {
            "has_made_first_topup_column": "Колонка реферальной системы",
            "cryptobot_table": "Таблица CryptoBot payments",
            "heleket_table": "Таблица Heleket payments",
            "user_messages_table": "Таблица пользовательских сообщений",
            "welcome_texts_table": "Таблица приветственных текстов",
            "privacy_policies_table": "Таблица политик конфиденциальности",
            "public_offers_table": "Таблица публичных оферт",
            "welcome_texts_is_enabled_column": "Поле is_enabled в welcome_texts",
            "broadcast_history_media_fields": "Медиа поля в broadcast_history",
            "subscription_conversions_table": "Таблица конверсий подписок",
            "subscription_duplicates": "Отсутствие дубликатов подписок",
            "promo_groups_table": "Таблица промо-групп",
            "server_promo_groups_table": "Связи серверов и промогрупп",
            "server_squads_trial_column": "Колонка триального назначения у серверов",
            "users_promo_group_column": "Колонка promo_group_id у пользователей",
            "promo_groups_period_discounts_column": "Колонка period_discounts у промо-групп",
            "promo_groups_auto_assign_column": "Колонка auto_assign_total_spent_kopeks у промо-групп",
            "promo_groups_addon_discount_column": "Колонка apply_discounts_to_addons у промо-групп",
            "users_auto_promo_group_assigned_column": "Флаг автоназначения промогруппы у пользователей",
            "users_auto_promo_group_threshold_column": "Порог последней авто-промогруппы у пользователей",
            "users_promo_offer_discount_percent_column": "Колонка процента промо-скидки у пользователей",
            "users_promo_offer_discount_source_column": "Колонка источника промо-скидки у пользователей",
            "users_promo_offer_discount_expires_column": "Колонка срока действия промо-скидки у пользователей",
            "subscription_crypto_link_column": "Колонка subscription_crypto_link в subscriptions",
            "discount_offers_table": "Таблица discount_offers",
            "discount_offers_effect_column": "Колонка effect_type в discount_offers",
            "discount_offers_extra_column": "Колонка extra_data в discount_offers",
            "promo_offer_templates_table": "Таблица promo_offer_templates",
            "promo_offer_templates_active_discount_column": "Колонка active_discount_hours в promo_offer_templates",
            "promo_offer_logs_table": "Таблица promo_offer_logs",
            "subscription_temporary_access_table": "Таблица subscription_temporary_access",
        }
        
        for check_key, check_status in status.items():
            check_name = check_names.get(check_key, check_key)
            icon = "✅" if check_status else "❌"
            logger.info(f"{icon} {check_name}: {'OK' if check_status else 'ТРЕБУЕТ ВНИМАНИЯ'}")
        
        all_good = all(status.values())
        if all_good:
            logger.info("🎉 Все миграции выполнены успешно!")
            
            try:
                async with engine.begin() as conn:
                    conversions_count = await conn.execute(text("SELECT COUNT(*) FROM subscription_conversions"))
                    users_count = await conn.execute(text("SELECT COUNT(*) FROM users"))
                    welcome_texts_count = await conn.execute(text("SELECT COUNT(*) FROM welcome_texts"))
                    broadcasts_count = await conn.execute(text("SELECT COUNT(*) FROM broadcast_history"))
                    
                    conv_count = conversions_count.fetchone()[0]
                    usr_count = users_count.fetchone()[0]
                    welcome_count = welcome_texts_count.fetchone()[0]
                    broadcast_count = broadcasts_count.fetchone()[0]
                    
                    logger.info(f"📊 Статистика: {usr_count} пользователей, {conv_count} конверсий, {welcome_count} приветственных текстов, {broadcast_count} рассылок")
            except Exception as stats_error:
                logger.debug(f"Не удалось получить дополнительную статистику: {stats_error}")
                
        else:
            logger.warning("⚠️ Некоторые миграции требуют внимания")
            missing_migrations = [check_names[k] for k, v in status.items() if not v]
            logger.warning(f"Требуют выполнения: {', '.join(missing_migrations)}")
        
        return status
        
    except Exception as e:
        logger.error(f"Ошибка проверки статуса миграций: {e}")
        return None
