import logging
from sqlalchemy import text, inspect
from sqlalchemy.ext.asyncio import AsyncSession
from app.database.database import engine

logger = logging.getLogger(__name__)

async def get_database_type():
    return engine.dialect.name

async def check_table_exists(table_name: str) -> bool:
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()
            
            if db_type == 'sqlite':
                result = await conn.execute(text(f"""
                    SELECT name FROM sqlite_master 
                    WHERE type='table' AND name='{table_name}'
                """))
                return result.fetchone() is not None
                
            elif db_type == 'postgresql':
                result = await conn.execute(text("""
                    SELECT table_name FROM information_schema.tables 
                    WHERE table_schema = 'public' AND table_name = :table_name
                """), {"table_name": table_name})
                return result.fetchone() is not None
                
            elif db_type == 'mysql':
                result = await conn.execute(text("""
                    SELECT table_name FROM information_schema.tables 
                    WHERE table_schema = DATABASE() AND table_name = :table_name
                """), {"table_name": table_name})
                return result.fetchone() is not None
                
            return False
            
    except Exception as e:
        logger.error(f"Ошибка проверки существования таблицы {table_name}: {e}")
        return False

async def check_column_exists(table_name: str, column_name: str) -> bool:
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()
            
            if db_type == 'sqlite':
                result = await conn.execute(text(f"PRAGMA table_info({table_name})"))
                columns = result.fetchall()
                return any(col[1] == column_name for col in columns)
                
            elif db_type == 'postgresql':
                result = await conn.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = :table_name 
                    AND column_name = :column_name
                """), {"table_name": table_name, "column_name": column_name})
                return result.fetchone() is not None
                
            elif db_type == 'mysql':
                result = await conn.execute(text("""
                    SELECT COLUMN_NAME 
                    FROM information_schema.COLUMNS 
                    WHERE TABLE_NAME = :table_name 
                    AND COLUMN_NAME = :column_name
                """), {"table_name": table_name, "column_name": column_name})
                return result.fetchone() is not None
                
            return False
            
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

async def create_cryptobot_payments_table():
    table_exists = await check_table_exists('cryptobot_payments')
    if table_exists:
        logger.info("Таблица cryptobot_payments уже существует")
        return True
    
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()
            
            if db_type == 'sqlite':
                create_sql = """
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
                """
                
            elif db_type == 'postgresql':
                create_sql = """
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
                """
                
            elif db_type == 'mysql':
                create_sql = """
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
                """
            else:
                logger.error(f"Неподдерживаемый тип БД для создания таблицы: {db_type}")
                return False
            
            await conn.execute(text(create_sql))
            logger.info("Таблица cryptobot_payments успешно создана")
            return True
            
    except Exception as e:
        logger.error(f"Ошибка создания таблицы cryptobot_payments: {e}")
        return False

async def create_user_messages_table():
    table_exists = await check_table_exists('user_messages')
    if table_exists:
        logger.info("Таблица user_messages уже существует")
        return True
    
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()
            
            if db_type == 'sqlite':
                create_sql = """
                CREATE TABLE user_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    message_text TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT 1,
                    sort_order INTEGER DEFAULT 0,
                    created_by INTEGER NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
                );
                
                CREATE INDEX idx_user_messages_active ON user_messages(is_active);
                CREATE INDEX idx_user_messages_sort ON user_messages(sort_order, created_at);
                """
                
            elif db_type == 'postgresql':
                create_sql = """
                CREATE TABLE user_messages (
                    id SERIAL PRIMARY KEY,
                    message_text TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    sort_order INTEGER DEFAULT 0,
                    created_by INTEGER NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
                );
                
                CREATE INDEX idx_user_messages_active ON user_messages(is_active);
                CREATE INDEX idx_user_messages_sort ON user_messages(sort_order, created_at);
                """
                
            elif db_type == 'mysql':
                create_sql = """
                CREATE TABLE user_messages (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    message_text TEXT NOT NULL,
                    is_active BOOLEAN DEFAULT TRUE,
                    sort_order INT DEFAULT 0,
                    created_by INT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL
                );
                
                CREATE INDEX idx_user_messages_active ON user_messages(is_active);
                CREATE INDEX idx_user_messages_sort ON user_messages(sort_order, created_at);
                """
            else:
                logger.error(f"Неподдерживаемый тип БД для создания таблицы: {db_type}")
                return False
            
            await conn.execute(text(create_sql))
            logger.info("Таблица user_messages успешно создана")
            return True
            
    except Exception as e:
        logger.error(f"Ошибка создания таблицы user_messages: {e}")
        return False


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
                    await conn.execute(
                        text(
                            """
                            INSERT INTO promo_groups (
                                name,
                                server_discount_percent,
                                traffic_discount_percent,
                                device_discount_percent,
                                is_default
                            ) VALUES (:name, 0, 0, 0, :is_default)
                        """
                        ),
                        {"name": default_group_name, "is_default": True},
                    )

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
    table_exists = await check_table_exists('welcome_texts')
    if table_exists:
        logger.info("Таблица welcome_texts уже существует")
        return await add_welcome_text_is_enabled_column()
    
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()
            
            if db_type == 'sqlite':
                create_sql = """
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
                """
                
            elif db_type == 'postgresql':
                create_sql = """
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
                """
                
            elif db_type == 'mysql':
                create_sql = """
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
                """
            else:
                logger.error(f"Неподдерживаемый тип БД для создания таблицы: {db_type}")
                return False
            
            await conn.execute(text(create_sql))
            logger.info("✅ Таблица welcome_texts успешно создана с полем is_enabled")
            return True
            
    except Exception as e:
        logger.error(f"Ошибка создания таблицы welcome_texts: {e}")
        return False

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
    table_exists = await check_table_exists('subscription_conversions')
    if table_exists:
        logger.info("Таблица subscription_conversions уже существует")
        return True
    
    try:
        async with engine.begin() as conn:
            db_type = await get_database_type()
            
            if db_type == 'sqlite':
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
                
            elif db_type == 'postgresql':
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
                
            elif db_type == 'mysql':
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
            else:
                logger.error(f"Неподдерживаемый тип БД для создания таблицы: {db_type}")
                return False
            
            await conn.execute(text(create_sql))
            logger.info("✅ Таблица subscription_conversions успешно создана")
            return True
            
    except Exception as e:
        logger.error(f"Ошибка создания таблицы subscription_conversions: {e}")
        return False

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

async def run_universal_migration():
    logger.info("=== НАЧАЛО УНИВЕРСАЛЬНОЙ МИГРАЦИИ ===")
    
    try:
        db_type = await get_database_type()
        logger.info(f"Тип базы данных: {db_type}")
        
        referral_migration_success = await add_referral_system_columns()
        if not referral_migration_success:
            logger.warning("⚠️ Проблемы с миграцией реферальной системы")
        
        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ CRYPTOBOT ===")
        cryptobot_created = await create_cryptobot_payments_table()
        if cryptobot_created:
            logger.info("✅ Таблица CryptoBot payments готова")
        else:
            logger.warning("⚠️ Проблемы с таблицей CryptoBot payments")

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

        logger.info("=== СОЗДАНИЕ ТАБЛИЦЫ АУДИТА ПОДДЕРЖКИ ===")
        try:
            async with engine.begin() as conn:
                db_type = await get_database_type()
                if not await check_table_exists('support_audit_logs'):
                    if db_type == 'sqlite':
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
                    elif db_type == 'postgresql':
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
            "user_messages_table": False,
            "welcome_texts_table": False,
            "welcome_texts_is_enabled_column": False,
            "broadcast_history_media_fields": False,
            "subscription_duplicates": False,
            "subscription_conversions_table": False,
            "promo_groups_table": False,
            "users_promo_group_column": False
        }
        
        status["has_made_first_topup_column"] = await check_column_exists('users', 'has_made_first_topup')
        
        status["cryptobot_table"] = await check_table_exists('cryptobot_payments')
        status["user_messages_table"] = await check_table_exists('user_messages')
        status["welcome_texts_table"] = await check_table_exists('welcome_texts')
        status["subscription_conversions_table"] = await check_table_exists('subscription_conversions')
        status["promo_groups_table"] = await check_table_exists('promo_groups')

        status["welcome_texts_is_enabled_column"] = await check_column_exists('welcome_texts', 'is_enabled')
        status["users_promo_group_column"] = await check_column_exists('users', 'promo_group_id')
        
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
            "user_messages_table": "Таблица пользовательских сообщений",
            "welcome_texts_table": "Таблица приветственных текстов",
            "welcome_texts_is_enabled_column": "Поле is_enabled в welcome_texts",
            "broadcast_history_media_fields": "Медиа поля в broadcast_history",
            "subscription_conversions_table": "Таблица конверсий подписок",
            "subscription_duplicates": "Отсутствие дубликатов подписок",
            "promo_groups_table": "Таблица промо-групп",
            "users_promo_group_column": "Колонка promo_group_id у пользователей"
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
