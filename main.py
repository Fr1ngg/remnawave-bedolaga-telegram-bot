import asyncio
import logging
import sys
import os
import signal
from pathlib import Path

from app.external.api_bot_server import start_api_bot_server

sys.path.append(str(Path(__file__).parent))

from app.bot import setup_bot
from app.config import settings
from app.database.database import init_db
from app.services.monitoring_service import monitoring_service
from app.services.maintenance_service import maintenance_service
from app.services.payment_service import PaymentService
from app.services.version_service import version_service
from app.external.webhook_server import WebhookServer
from app.external.yookassa_webhook import start_yookassa_webhook_server
from app.database.universal_migration import run_universal_migration
from app.services.backup_service import backup_service
from app.localization.loader import ensure_locale_templates


class GracefulExit:
    
    def __init__(self):
        self.exit = False
        
    def exit_gracefully(self, signum, frame):
        logging.getLogger(__name__).info(f"Получен сигнал {signum}. Корректное завершение работы...")
        self.exit = True


async def main():
    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL),
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(settings.LOG_FILE, encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    
    logger = logging.getLogger(__name__)
    logger.info("🚀 Запуск Bedolaga Remnawave Bot...")
    
    try:
        ensure_locale_templates()
    except Exception as error:
        logger.warning("Failed to prepare locale templates: %s", error)

    killer = GracefulExit()
    signal.signal(signal.SIGINT, killer.exit_gracefully)
    signal.signal(signal.SIGTERM, killer.exit_gracefully)
    
    webhook_server = None
    yookassa_server_task = None
    bot_api = None
    monitoring_task = None
    maintenance_task = None
    version_check_task = None
    polling_task = None
    
    try:
        logger.info("📊 Инициализация базы данных...")
        await init_db()
        
        skip_migration = os.getenv('SKIP_MIGRATION', 'false').lower() == 'true'
        
        if not skip_migration:
            logger.info("🔧 Выполняем проверку и миграцию базы данных...")
            try:
                migration_success = await run_universal_migration()
                
                if migration_success:
                    logger.info("✅ Миграция базы данных завершена успешно")
                else:
                    logger.warning("⚠️ Миграция завершилась с предупреждениями, но продолжаем запуск")
                    
            except Exception as migration_error:
                logger.error(f"❌ Ошибка выполнения миграции: {migration_error}")
                logger.warning("⚠️ Продолжаем запуск без миграции")
        else:
            logger.info("ℹ️ Миграция пропущена (SKIP_MIGRATION=true)")
        
        logger.info("🤖 Настройка бота...")
        bot, dp = await setup_bot()
        
        monitoring_service.bot = bot
        maintenance_service.set_bot(bot)
        
        from app.services.admin_notification_service import AdminNotificationService
        admin_notification_service = AdminNotificationService(bot)
        version_service.bot = bot
        version_service.set_notification_service(admin_notification_service)
        logger.info(f"📄 Сервис версий настроен для репозитория: {version_service.repo}")
        logger.info(f"📦 Текущая версия: {version_service.current_version}")
        
        logger.info("🔗 Бот подключен к сервисам мониторинга и техработ")

        logger.info("🗄️ Инициализация сервиса бекапов...")
        try:
            backup_service.bot = bot
            
            settings_obj = await backup_service.get_backup_settings()
            if settings_obj.auto_backup_enabled:
                await backup_service.start_auto_backup()
                logger.info("✅ Автобекапы запущены")
            
            logger.info("✅ Сервис бекапов инициализирован")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации сервиса бекапов: {e}")
        
        payment_service = PaymentService(bot)
        
        webhook_needed = settings.TRIBUTE_ENABLED or settings.is_cryptobot_enabled()
        
        if webhook_needed:
            enabled_services = []
            if settings.TRIBUTE_ENABLED:
                enabled_services.append("Tribute")
            if settings.is_cryptobot_enabled():
                enabled_services.append("CryptoBot")
            
            logger.info(f"🌐 Запуск webhook сервера для: {', '.join(enabled_services)}...")
            webhook_server = WebhookServer(bot)
            await webhook_server.start()
        else:
            logger.info("ℹ️ Tribute и CryptoBot отключены, webhook сервер не запускается")
        
        if settings.is_yookassa_enabled():
            logger.info("💳 Запуск YooKassa webhook сервера...")
            yookassa_server_task = asyncio.create_task(
                start_yookassa_webhook_server(payment_service)
            )
        else:
            logger.info("ℹ️ YooKassa отключена, webhook сервер не запускается")
        
        logger.info("📊 Запуск службы мониторинга...")
        monitoring_task = asyncio.create_task(monitoring_service.start_monitoring())

        if settings.is_bot_api_enabled:
            if settings.TOKEN_BOT_API is not None:
                logger.info("ℹ️ Запуск API бота")
                bot_api = asyncio.create_task(start_api_bot_server())
            else:
                logger.error("❌ Bot API не был запущен, укажите TOKEN_BOT_API в env")
        else:
            logger.info("ℹ️ API бота отключена, webhook сервер не запускается")

        logger.info("🔧 Проверка службы техработ...")
        if not maintenance_service._check_task or maintenance_service._check_task.done():
            logger.info("🔧 Запуск службы техработ...")
            maintenance_task = asyncio.create_task(maintenance_service.start_monitoring())
        else:
            logger.info("🔧 Служба техработ уже запущена")
            maintenance_task = None
        
        if settings.is_version_check_enabled():
            logger.info("📄 Запуск сервиса проверки версий...")
            version_check_task = asyncio.create_task(version_service.start_periodic_check())
        else:
            logger.info("ℹ️ Проверка версий отключена")
        
        logger.info("📄 Запуск polling...")
        polling_task = asyncio.create_task(dp.start_polling(bot, skip_updates=True))
        
        logger.info("=" * 50)
        logger.info("🎯 Активные webhook endpoints:")
        if webhook_needed:
            if settings.TRIBUTE_ENABLED:
                logger.info(f"   Tribute: {settings.WEBHOOK_URL}:{settings.TRIBUTE_WEBHOOK_PORT}{settings.TRIBUTE_WEBHOOK_PATH}")
            if settings.is_cryptobot_enabled():
                logger.info(f"   CryptoBot: {settings.WEBHOOK_URL}:{settings.TRIBUTE_WEBHOOK_PORT}{settings.CRYPTOBOT_WEBHOOK_PATH}")
        if settings.is_yookassa_enabled():
            logger.info(f"   YooKassa: {settings.WEBHOOK_URL}:{settings.YOOKASSA_WEBHOOK_PORT}{settings.YOOKASSA_WEBHOOK_PATH}")
        logger.info("📄 Активные фоновые сервисы:")
        logger.info(f"   Мониторинг: {'Включен' if monitoring_task else 'Отключен'}")
        logger.info(f"   Техработы: {'Включен' if maintenance_task else 'Отключен'}")
        logger.info(f"   Проверка версий: {'Включен' if version_check_task else 'Отключен'}")
        logger.info("=" * 50)
        
        try:
            while not killer.exit:
                await asyncio.sleep(1)

                if bot_api and bot_api.done():
                    exception = bot_api.exception()
                    if exception:
                        logger.error(f"Bot API сервер завершился с ошибкой: {exception}")
                        logger.info("🔄 Перезапуск BotAPI сервера...")
                        bot_api = asyncio.create_task(start_api_bot_server())
                
                if yookassa_server_task and yookassa_server_task.done():
                    exception = yookassa_server_task.exception()
                    if exception:
                        logger.error(f"YooKassa webhook сервер завершился с ошибкой: {exception}")
                        logger.info("🔄 Перезапуск YooKassa webhook сервера...")
                        yookassa_server_task = asyncio.create_task(
                            start_yookassa_webhook_server(payment_service)
                        )
                
                if monitoring_task.done():
                    exception = monitoring_task.exception()
                    if exception:
                        logger.error(f"Служба мониторинга завершилась с ошибкой: {exception}")
                        monitoring_task = asyncio.create_task(monitoring_service.start_monitoring())
                        
                if maintenance_task and maintenance_task.done():
                    exception = maintenance_task.exception()
                    if exception:
                        logger.error(f"Служба техработ завершилась с ошибкой: {exception}")
                        maintenance_task = asyncio.create_task(maintenance_service.start_monitoring())
                
                if version_check_task and version_check_task.done():
                    exception = version_check_task.exception()
                    if exception:
                        logger.error(f"Сервис проверки версий завершился с ошибкой: {exception}")
                        if settings.is_version_check_enabled():
                            logger.info("🔄 Перезапуск сервиса проверки версий...")
                            version_check_task = asyncio.create_task(version_service.start_periodic_check())
                        
                if polling_task.done():
                    exception = polling_task.exception()
                    if exception:
                        logger.error(f"Polling завершился с ошибкой: {exception}")
                        break
                        
        except Exception as e:
            logger.error(f"Ошибка в основном цикле: {e}")
            
    except Exception as e:
        logger.error(f"❌ Критическая ошибка при запуске: {e}")
        raise
        
    finally:
        logger.info("🛑 Начинается корректное завершение работы...")
        
        if yookassa_server_task and not yookassa_server_task.done():
            logger.info("ℹ️ Остановка YooKassa webhook сервера...")
            yookassa_server_task.cancel()
            try:
                await yookassa_server_task
            except asyncio.CancelledError:
                pass

        if bot_api and not bot_api.done():
            logger.info("ℹ️ Остановка BotAPI сервера...")
            bot_api.cancel()
            try:
                await bot_api
            except asyncio.CancelledError:
                pass
        
        if monitoring_task and not monitoring_task.done():
            logger.info("ℹ️ Остановка службы мониторинга...")
            monitoring_service.stop_monitoring()
            monitoring_task.cancel()
            try:
                await monitoring_task
            except asyncio.CancelledError:
                pass
        
        if maintenance_task and not maintenance_task.done():
            logger.info("ℹ️ Остановка службы техработ...")
            await maintenance_service.stop_monitoring()
            maintenance_task.cancel()
            try:
                await maintenance_task
            except asyncio.CancelledError:
                pass
        
        if version_check_task and not version_check_task.done():
            logger.info("ℹ️ Остановка сервиса проверки версий...")
            version_check_task.cancel()
            try:
                await version_check_task
            except asyncio.CancelledError:
                pass

        logger.info("ℹ️ Остановка сервиса бекапов...")
        try:
            await backup_service.stop_auto_backup()
        except Exception as e:
            logger.error(f"Ошибка остановки сервиса бекапов: {e}")
        
        if polling_task and not polling_task.done():
            logger.info("ℹ️ Остановка polling...")
            polling_task.cancel()
            try:
                await polling_task
            except asyncio.CancelledError:
                pass
        
        if webhook_server:
            logger.info("ℹ️ Остановка webhook сервера...")
            await webhook_server.stop()
        
        if 'bot' in locals():
            try:
                await bot.session.close()
                logger.info("✅ Сессия бота закрыта")
            except Exception as e:
                logger.error(f"Ошибка закрытия сессии бота: {e}")
        
        logger.info("✅ Завершение работы бота завершено")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен пользователем")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        sys.exit(1)
