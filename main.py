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
from app.services.payment_verification_service import (
    PENDING_MAX_AGE,
    SUPPORTED_MANUAL_CHECK_METHODS,
    auto_payment_verification_service,
    get_enabled_auto_methods,
    method_display_name,
)
from app.database.models import PaymentMethod
from app.services.version_service import version_service
from app.webapi.server import WebAPIServer
from app.webserver.unified_app import create_unified_app
from app.database.universal_migration import run_universal_migration
from app.services.backup_service import backup_service
from app.services.reporting_service import reporting_service
from app.services.remnawave_sync_service import remnawave_sync_service
from app.localization.loader import ensure_locale_templates
from app.services.system_settings_service import bot_configuration_service
from app.services.external_admin_service import ensure_external_admin_token
from app.services.broadcast_service import broadcast_service
from app.utils.startup_timeline import StartupTimeline
from app.utils.timezone import TimezoneAwareFormatter


class GracefulExit:
    
    def __init__(self):
        self.exit = False
        
    def exit_gracefully(self, signum, frame):
        logging.getLogger(__name__).info(f"Получен сигнал {signum}. Корректное завершение работы...")
        self.exit = True


async def main():
    formatter = TimezoneAwareFormatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        timezone_name=settings.TIMEZONE,
    )

    file_handler = logging.FileHandler(settings.LOG_FILE, encoding='utf-8')
    file_handler.setFormatter(formatter)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)

    logging.basicConfig(
        level=getattr(logging, settings.LOG_LEVEL),
        handlers=[file_handler, stream_handler],
    )
    
    # Установим более высокий уровень логирования для "мусорных" логов
    logging.getLogger("aiohttp.access").setLevel(logging.ERROR)
    logging.getLogger("aiohttp.client").setLevel(logging.WARNING)
    logging.getLogger("aiohttp.internal").setLevel(logging.WARNING)
    logging.getLogger("app.external.remnawave_api").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.ERROR)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    
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
    
    web_app = None
    monitoring_task = None
    maintenance_task = None
    version_check_task = None
    polling_task = None
    web_api_server = None
    telegram_webhook_enabled = False
    polling_enabled = True
    payment_webhooks_enabled = False

    summary_logged = False

    try:
        async with timeline.stage(
            "Инициализация базы данных", "🗄️", success_message="База данных готова"
        ):
            await init_db()

        auto_migration_enabled = (
            os.getenv("ENABLE_AUTO_MIGRATION", "false").lower() == "true"
        )

        if auto_migration_enabled:
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
                "⏹️",
                "Отключено",
                "ENABLE_AUTO_MIGRATION=false (по умолчанию)",
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

        async with timeline.stage(
            "Автосинхронизация RemnaWave",
            "🔄",
            success_message="Сервис автосинхронизации готов",
        ) as stage:
            try:
                await remnawave_sync_service.initialize()
                status = remnawave_sync_service.get_status()
                if status.enabled:
                    times_text = ", ".join(t.strftime("%H:%M") for t in status.times) or "—"
                    if status.next_run:
                        next_run_text = status.next_run.strftime("%d.%m.%Y %H:%M")
                        stage.log(
                            f"Активирована: расписание {times_text}, ближайший запуск {next_run_text}"
                        )
                    else:
                        stage.log(f"Активирована: расписание {times_text}")
                else:
                    stage.log("Автосинхронизация отключена настройками")
            except Exception as e:
                stage.warning(f"Ошибка запуска автосинхронизации: {e}")
                logger.error(f"❌ Ошибка запуска автосинхронизации RemnaWave: {e}")

        payment_service = PaymentService(bot)
        auto_payment_verification_service.set_payment_service(payment_service)

        verification_providers: list[str] = []
        auto_verification_active = False
        async with timeline.stage(
            "Сервис проверки пополнений",
            "💳",
            success_message="Ручная проверка активна",
        ) as stage:
            for method in SUPPORTED_MANUAL_CHECK_METHODS:
                if method == PaymentMethod.YOOKASSA and settings.is_yookassa_enabled():
                    verification_providers.append("YooKassa")
                elif method == PaymentMethod.MULENPAY and settings.is_mulenpay_enabled():
                    verification_providers.append(settings.get_mulenpay_display_name())
                elif method == PaymentMethod.PAL24 and settings.is_pal24_enabled():
                    verification_providers.append("PayPalych")
                elif method == PaymentMethod.WATA and settings.is_wata_enabled():
                    verification_providers.append("WATA")
                elif method == PaymentMethod.HELEKET and settings.is_heleket_enabled():
                    verification_providers.append("Heleket")
                elif method == PaymentMethod.CRYPTOBOT and settings.is_cryptobot_enabled():
                    verification_providers.append("CryptoBot")

            if verification_providers:
                hours = int(PENDING_MAX_AGE.total_seconds() // 3600)
                stage.log(
                    "Ожидающие пополнения автоматически отбираются не старше "
                    f"{hours}ч"
                )
                stage.log(
                    "Доступна ручная проверка для: "
                    + ", ".join(sorted(verification_providers))
                )
                stage.success(
                    f"Активно провайдеров: {len(verification_providers)}"
                )
            else:
                stage.skip("Нет активных провайдеров для ручной проверки")

            if settings.is_payment_verification_auto_check_enabled():
                auto_methods = get_enabled_auto_methods()
                if auto_methods:
                    interval_minutes = settings.get_payment_verification_auto_check_interval()
                    auto_labels = ", ".join(
                        sorted(method_display_name(method) for method in auto_methods)
                    )
                    stage.log(
                        "Автопроверка каждые "
                        f"{interval_minutes} мин: {auto_labels}"
                    )
                else:
                    stage.log(
                        "Автопроверка включена, но нет активных провайдеров"
                    )
            else:
                stage.log("Автопроверка отключена настройками")

            await auto_payment_verification_service.start()
            auto_verification_active = auto_payment_verification_service.is_running()
            if auto_verification_active:
                stage.log("Фоновая автопроверка запущена")

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

        bot_run_mode = settings.get_bot_run_mode()
        polling_enabled = bot_run_mode in {"polling", "both"}
        telegram_webhook_enabled = bot_run_mode in {"webhook", "both"}

        payment_webhooks_enabled = any(
            [
                settings.TRIBUTE_ENABLED,
                settings.is_cryptobot_enabled(),
                settings.is_mulenpay_enabled(),
                settings.is_yookassa_enabled(),
                settings.is_pal24_enabled(),
                settings.is_wata_enabled(),
                settings.is_heleket_enabled(),
            ]
        )

        async with timeline.stage(
            "Единый веб-сервер",
            "🌐",
            success_message="Веб-сервер запущен",
        ) as stage:
            should_start_web_app = (
                settings.is_web_api_enabled()
                or telegram_webhook_enabled
                or payment_webhooks_enabled
                or settings.get_miniapp_static_path().exists()
            )

            if should_start_web_app:
                web_app = create_unified_app(
                    bot,
                    dp,
                    payment_service,
                    enable_telegram_webhook=telegram_webhook_enabled,
                )

                web_api_server = WebAPIServer(app=web_app)
                await web_api_server.start()

                base_url = settings.WEBHOOK_URL or f"http://{settings.WEB_API_HOST}:{settings.WEB_API_PORT}"
                stage.log(f"Базовый URL: {base_url}")

                features: list[str] = []
                if settings.is_web_api_enabled():
                    features.append("админка")
                if payment_webhooks_enabled:
                    features.append("платежные webhook-и")
                if telegram_webhook_enabled:
                    features.append("Telegram webhook")
                if settings.get_miniapp_static_path().exists():
                    features.append("статические файлы миниаппа")

                if features:
                    stage.log("Активные сервисы: " + ", ".join(features))
                stage.success("HTTP-сервисы активны")
            else:
                stage.skip("HTTP-сервисы отключены настройками")

        async with timeline.stage(
            "Telegram webhook",
            "🤖",
            success_message="Telegram webhook настроен",
        ) as stage:
            if telegram_webhook_enabled:
                webhook_url = settings.get_telegram_webhook_url()
                if not webhook_url:
                    stage.warning("WEBHOOK_URL не задан, пропускаем настройку webhook")
                else:
                    allowed_updates = dp.resolve_used_update_types()
                    await bot.set_webhook(
                        url=webhook_url,
                        secret_token=settings.WEBHOOK_SECRET_TOKEN,
                        drop_pending_updates=settings.WEBHOOK_DROP_PENDING_UPDATES,
                        allowed_updates=allowed_updates,
                    )
                    stage.log(f"Webhook установлен: {webhook_url}")
                    stage.log(f"Allowed updates: {', '.join(sorted(allowed_updates)) if allowed_updates else 'all'}")
                    stage.success("Telegram webhook активен")
            else:
                stage.skip("Режим webhook отключен")

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
            if not settings.is_maintenance_monitoring_enabled():
                maintenance_task = None
                stage.skip("Мониторинг техработ отключен настройками")
            elif not maintenance_service._check_task or maintenance_service._check_task.done():
                maintenance_task = asyncio.create_task(maintenance_service.start_monitoring())
                stage.log(f"Интервал проверки: {settings.MAINTENANCE_CHECK_INTERVAL}с")
                stage.log(
                    f"Повторных попыток проверки: {settings.get_maintenance_retry_attempts()}"
                )
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
            "Запуск polling",
            "🤖",
            success_message="Aiogram polling запущен",
        ) as stage:
            if polling_enabled:
                polling_task = asyncio.create_task(dp.start_polling(bot, skip_updates=True))
                stage.log("skip_updates=True")
            else:
                polling_task = None
                stage.skip("Polling отключен режимом работы")

        webhook_lines: list[str] = []
        base_url = settings.WEBHOOK_URL or f"http://{settings.WEB_API_HOST}:{settings.WEB_API_PORT}"

        def _fmt(path: str) -> str:
            return f"{base_url}{path if path.startswith('/') else '/' + path}"

        telegram_webhook_url = settings.get_telegram_webhook_url()
        if telegram_webhook_enabled and telegram_webhook_url:
            webhook_lines.append(f"Telegram: {telegram_webhook_url}")
        if settings.TRIBUTE_ENABLED:
            webhook_lines.append(f"Tribute: {_fmt(settings.TRIBUTE_WEBHOOK_PATH)}")
        if settings.is_mulenpay_enabled():
            webhook_lines.append(
                f"{settings.get_mulenpay_display_name()}: {_fmt(settings.MULENPAY_WEBHOOK_PATH)}"
            )
        if settings.is_cryptobot_enabled():
            webhook_lines.append(f"CryptoBot: {_fmt(settings.CRYPTOBOT_WEBHOOK_PATH)}")
        if settings.is_yookassa_enabled():
            webhook_lines.append(f"YooKassa: {_fmt(settings.YOOKASSA_WEBHOOK_PATH)}")
        if settings.is_pal24_enabled():
            webhook_lines.append(f"PayPalych: {_fmt(settings.PAL24_WEBHOOK_PATH)}")
        if settings.is_wata_enabled():
            webhook_lines.append(f"WATA: {_fmt(settings.WATA_WEBHOOK_PATH)}")
        if settings.is_heleket_enabled():
            webhook_lines.append(f"Heleket: {_fmt(settings.HELEKET_WEBHOOK_PATH)}")

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
        services_lines.append(
            "Проверка пополнений: "
            + ("Включена" if verification_providers else "Отключена")
        )
        services_lines.append(
            "Автопроверка пополнений: "
            + (
                "Включена"
                if auto_payment_verification_service.is_running()
                else "Отключена"
            )
        )
        timeline.log_section("Активные фоновые сервисы", services_lines, icon="📄")

        timeline.log_summary()
        summary_logged = True
        
        try:
            while not killer.exit:
                await asyncio.sleep(1)
                
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

                if auto_verification_active and not auto_payment_verification_service.is_running():
                    logger.warning(
                        "Сервис автопроверки пополнений остановился, пробуем перезапустить..."
                    )
                    await auto_payment_verification_service.start()
                    auto_verification_active = auto_payment_verification_service.is_running()

                if polling_task and polling_task.done():
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

        logger.info("ℹ️ Остановка сервиса автопроверки пополнений...")
        try:
            await auto_payment_verification_service.stop()
        except Exception as error:
            logger.error(
                f"Ошибка остановки сервиса автопроверки пополнений: {error}"
            )

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

        logger.info("ℹ️ Остановка сервиса отчетов...")
        try:
            await reporting_service.stop()
        except Exception as e:
            logger.error(f"Ошибка остановки сервиса отчетов: {e}")

        logger.info("ℹ️ Остановка сервиса автосинхронизации RemnaWave...")
        try:
            await remnawave_sync_service.stop()
        except Exception as e:
            logger.error(f"Ошибка остановки автосинхронизации RemnaWave: {e}")

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
        
        if telegram_webhook_enabled and 'bot' in locals():
            logger.info("ℹ️ Снятие Telegram webhook...")
            try:
                await bot.delete_webhook(drop_pending_updates=False)
                logger.info("✅ Telegram webhook удалён")
            except Exception as error:
                logger.error(f"Ошибка удаления Telegram webhook: {error}")

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
