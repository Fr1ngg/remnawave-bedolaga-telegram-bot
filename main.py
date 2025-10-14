import asyncio
import logging
import sys
import os
import signal
from pathlib import Path

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
from app.external.pal24_webhook import start_pal24_webhook_server, Pal24WebhookServer
from app.database.universal_migration import run_universal_migration
from app.services.backup_service import backup_service
from app.services.reporting_service import reporting_service
from app.localization.loader import ensure_locale_templates
from app.services.system_settings_service import bot_configuration_service
from app.services.external_admin_service import ensure_external_admin_token
from app.services.broadcast_service import broadcast_service
from app.utils.startup_timeline import StartupTimeline


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
    timeline = StartupTimeline(logger, "Bedolaga Remnawave Bot")
    timeline.log_banner(
        [
            ("Уровень логирования", settings.LOG_LEVEL),
            ("Режим БД", settings.DATABASE_MODE),
        ]
    )

    async with timeline.stage(
        "Подготовка локализаций", "🗂️", success_message="Шаблоны локализаций готовы"
    ) as stage:
        try:
            ensure_locale_templates()
        except Exception as error:
            stage.warning(f"Не удалось подготовить шаблоны локализаций: {error}")
            logger.warning("Failed to prepare locale templates: %s", error)

    killer = GracefulExit()
    signal.signal(signal.SIGINT, killer.exit_gracefully)
    signal.signal(signal.SIGTERM, killer.exit_gracefully)
    
    webhook_server = None
    yookassa_server_task = None
    pal24_server: Pal24WebhookServer | None = None
    monitoring_task = None
    maintenance_task = None
    version_check_task = None
    polling_task = None
    web_api_server = None
    
    summary_logged = False

    try:
        async with timeline.stage(
            "Инициализация базы данных", "🗄️", success_message="База данных готова"
        ):
            await init_db()

        skip_migration = os.getenv('SKIP_MIGRATION', 'false').lower() == 'true'

        if not skip_migration:
            async with timeline.stage(
                "Проверка и миграция базы данных",
                "🧬",
                success_message="Миграция завершена успешно",
            ) as stage:
                try:
                    migration_success = await run_universal_migration()
                    if migration_success:
                        stage.success("Миграция завершена успешно")
                    else:
                        stage.warning(
                            "Миграция завершилась с предупреждениями, запуск продолжится"
                        )
                        logger.warning(
                            "⚠️ Миграция завершилась с предупреждениями, но продолжаем запуск"
                        )
                except Exception as migration_error:
                    stage.warning(f"Ошибка выполнения миграции: {migration_error}")
                    logger.error(f"❌ Ошибка выполнения миграции: {migration_error}")
                    logger.warning("⚠️ Продолжаем запуск без миграции")
        else:
            timeline.add_manual_step(
                "Проверка и миграция базы данных",
                "⏭️",
                "Пропущено",
                "SKIP_MIGRATION=true",
            )

        async with timeline.stage(
            "Загрузка конфигурации из БД",
            "⚙️",
            success_message="Конфигурация загружена",
        ) as stage:
            try:
                await bot_configuration_service.initialize()
            except Exception as error:
                stage.warning(f"Не удалось загрузить конфигурацию: {error}")
                logger.error(f"❌ Не удалось загрузить конфигурацию: {error}")

        bot = None
        dp = None
        async with timeline.stage("Настройка бота", "🤖", success_message="Бот настроен") as stage:
            bot, dp = await setup_bot()
            stage.log("Кеш и FSM подготовлены")

        monitoring_service.bot = bot
        maintenance_service.set_bot(bot)
        broadcast_service.set_bot(bot)

        from app.services.admin_notification_service import AdminNotificationService

        async with timeline.stage(
            "Интеграция сервисов",
            "🔗",
            success_message="Сервисы подключены",
        ) as stage:
            admin_notification_service = AdminNotificationService(bot)
            version_service.bot = bot
            version_service.set_notification_service(admin_notification_service)
            stage.log(f"Репозиторий версий: {version_service.repo}")
            stage.log(f"Текущая версия: {version_service.current_version}")
            stage.success("Мониторинг, уведомления и рассылки подключены")

        async with timeline.stage(
            "Сервис бекапов",
            "🗄️",
            success_message="Сервис бекапов инициализирован",
        ) as stage:
            try:
                backup_service.bot = bot
                settings_obj = await backup_service.get_backup_settings()
                if settings_obj.auto_backup_enabled:
                    await backup_service.start_auto_backup()
                    stage.log(
                        "Автобекапы включены: интервал "
                        f"{settings_obj.backup_interval_hours}ч, запуск {settings_obj.backup_time}"
                    )
                else:
                    stage.log("Автобекапы отключены настройками")
                stage.success("Сервис бекапов инициализирован")
            except Exception as e:
                stage.warning(f"Ошибка инициализации сервиса бекапов: {e}")
                logger.error(f"❌ Ошибка инициализации сервиса бекапов: {e}")

        async with timeline.stage(
            "Сервис отчетов",
            "📊",
            success_message="Сервис отчетов готов",
        ) as stage:
            try:
                reporting_service.set_bot(bot)
                await reporting_service.start()
            except Exception as e:
                stage.warning(f"Ошибка запуска сервиса отчетов: {e}")
                logger.error(f"❌ Ошибка запуска сервиса отчетов: {e}")

        payment_service = PaymentService(bot)

        async with timeline.stage(
            "Внешняя админка",
            "🛡️",
            success_message="Токен внешней админки готов",
        ) as stage:
            try:
                bot_user = await bot.get_me()
                token = await ensure_external_admin_token(
                    bot_user.username,
                    bot_user.id,
                )
                if token:
                    stage.log("Токен синхронизирован")
                else:
                    stage.warning("Не удалось получить токен внешней админки")
            except Exception as error:  # pragma: no cover - защитный блок
                stage.warning(f"Ошибка подготовки внешней админки: {error}")
                logger.error("❌ Ошибка подготовки внешней админки: %s", error)

        webhook_needed = (
            settings.TRIBUTE_ENABLED
            or settings.is_cryptobot_enabled()
            or settings.is_mulenpay_enabled()
            or settings.is_wata_enabled()
        )

        async with timeline.stage(
            "Webhook сервисы",
            "🌐",
            success_message="Webhook сервера настроены",
        ) as stage:
            if webhook_needed:
                enabled_services = []
                if settings.TRIBUTE_ENABLED:
                    enabled_services.append("Tribute")
                if settings.is_mulenpay_enabled():
                    enabled_services.append("Mulen Pay")
                if settings.is_wata_enabled():
                    enabled_services.append("Wata")
                if settings.is_cryptobot_enabled():
                    enabled_services.append("CryptoBot")

                webhook_server = WebhookServer(bot)
                await webhook_server.start()
                stage.log(f"Активированы: {', '.join(enabled_services)}")
                stage.success("Webhook сервера запущены")
            else:
                stage.skip("Tribute, Wata, Mulen Pay и CryptoBot отключены")

        async with timeline.stage(
            "YooKassa webhook",
            "💳",
            success_message="YooKassa webhook запущен",
        ) as stage:
            if settings.is_yookassa_enabled():
                yookassa_server_task = asyncio.create_task(
                    start_yookassa_webhook_server(payment_service)
                )
                stage.log(
                    f"Endpoint: {settings.WEBHOOK_URL}:{settings.YOOKASSA_WEBHOOK_PORT}{settings.YOOKASSA_WEBHOOK_PATH}"
                )
            else:
                stage.skip("YooKassa отключена настройками")

        async with timeline.stage(
            "PayPalych webhook",
            "💳",
            success_message="PayPalych webhook запущен",
        ) as stage:
            if settings.is_pal24_enabled():
                pal24_server = await start_pal24_webhook_server(payment_service)
                stage.log(
                    f"Endpoint: {settings.WEBHOOK_URL}:{settings.PAL24_WEBHOOK_PORT}{settings.PAL24_WEBHOOK_PATH}"
                )
            else:
                stage.skip("PayPalych отключен настройками")

        async with timeline.stage(
            "Служба мониторинга",
            "📈",
            success_message="Служба мониторинга запущена",
        ) as stage:
            monitoring_task = asyncio.create_task(monitoring_service.start_monitoring())
            stage.log(f"Интервал опроса: {settings.MONITORING_INTERVAL}с")

        async with timeline.stage(
            "Служба техработ",
            "🛡️",
            success_message="Служба техработ запущена",
        ) as stage:
            if not maintenance_service._check_task or maintenance_service._check_task.done():
                maintenance_task = asyncio.create_task(maintenance_service.start_monitoring())
                stage.log(f"Интервал проверки: {settings.MAINTENANCE_CHECK_INTERVAL}с")
            else:
                maintenance_task = None
                stage.skip("Служба техработ уже активна")

        async with timeline.stage(
            "Сервис проверки версий",
            "📄",
            success_message="Проверка версий запущена",
        ) as stage:
            if settings.is_version_check_enabled():
                version_check_task = asyncio.create_task(version_service.start_periodic_check())
                stage.log(
                    f"Интервал проверки: {settings.VERSION_CHECK_INTERVAL_HOURS}ч"
                )
            else:
                version_check_task = None
                stage.skip("Проверка версий отключена настройками")

        async with timeline.stage(
            "Административное веб-API",
            "🌐",
            success_message="Веб-API запущено",
        ) as stage:
            if settings.is_web_api_enabled():
                try:
                    from app.webapi import WebAPIServer

                    web_api_server = WebAPIServer()
                    await web_api_server.start()
                    stage.success(
                        f"Доступно на http://{settings.WEB_API_HOST}:{settings.WEB_API_PORT}"
                    )
                except Exception as error:
                    stage.warning(f"Не удалось запустить веб-API: {error}")
                    logger.error(f"❌ Не удалось запустить веб-API: {error}")
            else:
                stage.skip("Веб-API отключено")

        async with timeline.stage(
            "Запуск polling",
            "🤖",
            success_message="Aiogram polling запущен",
        ) as stage:
            polling_task = asyncio.create_task(dp.start_polling(bot, skip_updates=True))
            stage.log("skip_updates=True")

        webhook_lines = []
        if webhook_needed:
            if settings.TRIBUTE_ENABLED:
                webhook_lines.append(
                    f"Tribute: {settings.WEBHOOK_URL}:{settings.TRIBUTE_WEBHOOK_PORT}{settings.TRIBUTE_WEBHOOK_PATH}"
                )
            if settings.is_mulenpay_enabled():
                webhook_lines.append(
                    f"Mulen Pay: {settings.WEBHOOK_URL}:{settings.TRIBUTE_WEBHOOK_PORT}{settings.MULENPAY_WEBHOOK_PATH}"
                )
            if settings.is_cryptobot_enabled():
                webhook_lines.append(
                    f"CryptoBot: {settings.WEBHOOK_URL}:{settings.TRIBUTE_WEBHOOK_PORT}{settings.CRYPTOBOT_WEBHOOK_PATH}"
                )
        if settings.is_yookassa_enabled():
            webhook_lines.append(
                f"YooKassa: {settings.WEBHOOK_URL}:{settings.YOOKASSA_WEBHOOK_PORT}{settings.YOOKASSA_WEBHOOK_PATH}"
            )
        if settings.is_pal24_enabled():
            webhook_lines.append(
                f"PayPalych: {settings.WEBHOOK_URL}:{settings.PAL24_WEBHOOK_PORT}{settings.PAL24_WEBHOOK_PATH}"
            )

        timeline.log_section(
            "Активные webhook endpoints",
            webhook_lines if webhook_lines else ["Нет активных endpoints"],
            icon="🎯",
        )

        services_lines = [
            f"Мониторинг: {'Включен' if monitoring_task else 'Отключен'}",
            f"Техработы: {'Включен' if maintenance_task else 'Отключен'}",
            f"Проверка версий: {'Включен' if version_check_task else 'Отключен'}",
            f"Отчеты: {'Включен' if reporting_service.is_running() else 'Отключен'}",
        ]
        timeline.log_section("Активные фоновые сервисы", services_lines, icon="📄")

        timeline.log_summary()
        summary_logged = True
        
        try:
            while not killer.exit:
                await asyncio.sleep(1)
                
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
        if not summary_logged:
            timeline.log_summary()
            summary_logged = True
        logger.info("🛑 Начинается корректное завершение работы...")
        
        if yookassa_server_task and not yookassa_server_task.done():
            logger.info("ℹ️ Остановка YooKassa webhook сервера...")
            yookassa_server_task.cancel()
            try:
                await yookassa_server_task
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

        if pal24_server:
            logger.info("ℹ️ Остановка PayPalych webhook сервера...")
            await asyncio.get_running_loop().run_in_executor(None, pal24_server.stop)
        
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

        logger.info("ℹ️ Остановка сервиса отчетов...")
        try:
            await reporting_service.stop()
        except Exception as e:
            logger.error(f"Ошибка остановки сервиса отчетов: {e}")

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

        if web_api_server:
            try:
                await web_api_server.stop()
                logger.info("✅ Административное веб-API остановлено")
            except Exception as error:
                logger.error(f"Ошибка остановки веб-API: {error}")
        
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
