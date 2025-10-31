# 🚀 Remnawave Bedolaga Bot

<div align="center">

<img width="1024" height="1024" alt="ChatGPT Image 23 окт  2025 г , 13_18_33" src="https://github.com/user-attachments/assets/17ad0128-231d-4553-9f4b-ce0644da796c" />


**🤖 Современный Telegram-бот для управления VPN подписками через Remnawave API**

*Полнофункциональное решение с управлением пользователями, платежами и администрированием*

[![Python](https://img.shields.io/badge/Python-3.13+-blue?logo=python&logoColor=white)](https://python.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15+-blue?logo=postgresql&logoColor=white)](https://postgresql.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![GitHub Stars](https://img.shields.io/github/stars/Fr1ngg/remnawave-bedolaga-telegram-bot?style=social)](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/stargazers)

[🚀 Быстрый старт](#-быстрый-старт) • [📖 Функционал](#-функционал) • [🐳 Docker](#-docker-развертывание) • [💻 Локальная разработка](#-локальная-разработка) • [💬 Поддержка](#-поддержка-и-сообщество)

</div>

---

## 🧪 [Тестирование бота](https://t.me/FringVPN_bot)

## 💬 **[Bedolaga Chat](https://t.me/+wTdMtSWq8YdmZmVi)** - Для общения, вопросов, предложений

---

## 🌟 Почему Bedolaga?

Бот Бедолага не добрый и не милый.
Он просто делает вашу работу вместо вас, принимает оплату, выдаёт подписки, интегрируется с Remnawave и тихо ненавидит всех, кто ещё не подключил его.

Вы хотите продавать VPN — Бедолага позволит это делать.
Вы хотите спать — он позволит и это.

### ⚡ **Полная автоматизация VPN бизнеса**
- 🎯 **Готовое решение** - разверни за 5 минут, начни продавать сегодня
- 💰 **Многоканальные платежи** - Telegram Stars + Tribute + CryptoBot + Heleket + YooKassa (СБП + карты) + MulenPay + PayPalych (СБП + карты) + WATA
- 🔄 **Автоматизация 99%** - от регистрации до продления подписок
- - 📱 **MiniApp лк** - личный кабинет с возможностью покупки/продления подписки
- 📊 **Детальная аналитика** - полная картина вашего бизнеса
- 💬 **Уведомления в топики** об: Активация триала 💎 Покупка подписки 🔄 Конверсия из триала в платную ⏰ Продление подписки 💰 Пополнение баланса 🚧 Включении тех работ ♻️ Появлении новой версии бота
  
### 🎛️ **Гибкость конфигурации**
- 🌍 **Умный выбор серверов** - автоматический пропуск при одном сервере, мультивыбор
- 📱 **Управление устройствами** - от 1 до неограниченного количества
- 📊 **Режимы продажи трафика** - фиксированный лимит или выбор пакетов
- 🎁 **Промо-система** - коды на деньги, дни подписки, триал-периоды
- 🔧 **Гибкие тарифы** - от 5GB до безлимита, от 14 дней до года
- 🛒 **Умная корзина** - сохранение параметров подписки при недостатке баланса

### 💪 **Enterprise готовность**
- 🏗️ **Современная архитектура** - AsyncIO, PostgreSQL, Redis, модульная структура
- 🔒 **Безопасность** - интеграция с системой защиты панели через куки-аутентификацию
- 📈 **Масштабируемость** - от стартапа до крупного бизнеса
- 🔧 **Мониторинг** - автоматическое управление режимом тех. работ
- 🛡️ **Защита панели** - поддержка [remnawave-reverse-proxy](https://github.com/eGamesAPI/remnawave-reverse-proxy)
- 🗄️ **Бекапы/Восстановление** - автобекапы и восстановление бд прямо в боте с уведомлениями в топики
- ✍️ **Проверка на подписку** - проверяет подписку на канал
- 🔄 **Автосинхронизация** - фоновая синхронизация подписок и серверов(сквадов) с Remnawave по расписанию

### 📚 Поддерживаемые методы авторизации

| Метод | Заголовок | Описание |
|-------|-----------|----------|
| API Key | X-Api-Key: your_api_key | Стандартный API ключ |
| Bearer Token | Authorization: Bearer token | Классический Bearer token |
| Basic Auth | X-Api-Key: Basic base64(user:pass) | Basic Authentication |
| eGames Cookies | Cookies в формате key:value | Для панелей eGames |

---

## 🚀 Быстрый старт

### 🧙‍♂️ Автоустановка через `install_bot.sh`

Скрипт-установщик берёт на себя подготовку окружения, настройку конфигурации и дальнейшее обслуживание бота. Он работает поверх Docker Compose и требует заранее установить:

- **Docker Engine** и **Docker Compose plugin** (2.20+);
- **Git** и **Bash** (по умолчанию есть в большинстве Linux дистрибутивов);
- `openssl` (используется для генерации токенов, но не обязателен — при отсутствии скрипт использует `urandom`).

```bash
# 1. Скачай репозиторий
git clone https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot.git
cd remnawave-bedolaga-telegram-bot

# 2. Создай необходимые директории
mkdir -p ./logs ./data ./data/backups ./data/referral_qr
chmod -R 755 ./logs ./data
sudo chown -R 1000:1000 ./logs ./data

# 3. Запусти мастер установки
chmod +x install_bot.sh
./install_bot.sh
```

На первом запуске мастер:

1. Спросит путь установки и сохранит его в `./.bot_install_state` — можно оставлять путь по умолчанию (текущая директория).
2. Поможет собрать `.env`: запросит обязательные токены (бот, Remnawave, админы), при необходимости сгенерирует Web API и PostgreSQL пароли, предложит авторизацию Basic Auth или eGames secret.
3. Подготовит структуру каталогов (`logs`, `data`, `backups` и т. д.) и проверит, что Docker готов к запуску.
4. Создаст (или обновит) `docker-compose.yml`, настроит внешнюю сеть `bot_network`, чтобы дальнейшие сервисы (например, Caddy) могли подключаться.
5. Запустит контейнеры бота, PostgreSQL и Redis и выведет их статус.

После установки повторный запуск `./install_bot.sh` открывает **интерактивное меню управления**:

- 📊 Мониторинг состояния контейнеров и ресурсов
- ⚙️ Управление сервисами (запуск/остановка/пересборка)
- 📋 Просмотр и поиск по логам
- 🔄 Обновление проекта из Git с автоматическим бэкапом
- 💾 Создание и 📦 восстановление резервных копий (включая базу данных)
- 🧹 Очистка логов, бэкапов и образов
- 🌐 Помощник настройки обратного прокси Caddy (webhook + miniapp, обновление `docker-compose`, перезагрузка)
- ⚙️ Конфигуратор `.env` (редактирование, пересоздание, маскировка секретов)

> 💡 Скрипт можно запускать сколько угодно раз — он хранит путь установки и понимает, когда конфигурация уже создана. Меню работает и по SSH (достаточно TTY), а для скриптов можно передать путь установки через stdin.

### 🐳 Ручной Docker запуск

Если не хочется пользоваться мастером, можно настроить всё вручную:

```bash
# 1. Скачай репозиторий
git clone https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot.git
cd remnawave-bedolaga-telegram-bot

# 2. Настрой конфиг
cp .env.example .env
nano .env  # Заполни токены и настройки

# 3. Создай необходимые директории
mkdir -p ./logs ./data ./data/backups ./data/referral_qr
chmod -R 755 ./logs ./data
sudo chown -R 1000:1000 ./logs ./data

# 4. Запусти всё разом
docker compose up -d

# 5. Проверь статус
docker compose logs
```

---

## 🌐 Настройка обратного прокси и доменов

> Этот раздел описывает полноценную ручную настройку обратного прокси для **двух разных доменов**: отдельный домен для вебхуков (`hooks.example.com`) и отдельный домен для мини-приложения (`miniapp.example.com`). Оба прокси-сервера (Caddy или nginx) должны работать в одной Docker-сети с ботом, чтобы обращаться к сервису по внутреннему имени `remnawave_bot` без проброса портов наружу.

### 1. Планирование доменов и переменных окружения

1. Добавьте в DNS по **A/AAAA-записи** для обоих доменов на IP сервера, где запущен бот.
2. Убедитесь, что входящий трафик на **80/tcp и 443/tcp** открыт (брандмауэр, облачный фаервол).
3. В `.env` пропишите корректные URL, чтобы бот формировал ссылки с HTTPS-доменами:
   ```env
   WEBHOOK_URL=https://hooks.example.com
   WEB_API_ENABLED=true
   WEB_API_ALLOWED_ORIGINS=https://miniapp.example.com
   MINIAPP_CUSTOM_URL=https://miniapp.example.com
   ```

### 2. Общая Docker-сеть для бота и прокси

`docker-compose.yml` бота создаёт сеть `bot_network`. Чтобы внешний прокси видел сервис `remnawave_bot`, нужно:

```bash
# Убедиться, что сеть существует
docker network ls | grep bot_network || docker network create bot_network

# Подключить прокси (если контейнер уже запущен отдельно)
docker network connect bot_network <proxy_container_name>
```

Если прокси запускается через **собственный docker-compose**, в файле нужно объявить ту же сеть как внешнюю:

```yaml
networks:
  bot_network:
    external: true
```

### 3. Ручная установка Caddy в Docker

1. Создайте каталог для конфигурации:
   ```bash
   mkdir -p ~/caddy
   cd ~/caddy
   ```

2. Сохраните docker-compose-файл `docker-compose.caddy.yml`:
   ```yaml
   services:
     caddy:
       image: caddy:2-alpine
       container_name: remnawave_caddy
       restart: unless-stopped
       ports:
         - "80:80"
         - "443:443"
       volumes:
         - ./Caddyfile:/etc/caddy/Caddyfile
         - caddy_data:/data
         - caddy_config:/config
         - /root/remnawave-bedolaga-telegram-bot/miniapp:/miniapp:ro
         - /root/remnawave-bedolaga-telegram-bot/miniapp/redirect:/miniapp/redirect:ro
       networks:
         - bot_network

   volumes:
     caddy_data:
     caddy_config:

   networks:
     bot_network:
       external: true
   ```

3. Создайте `Caddyfile` с двумя виртуальными хостами:
   ```caddy
   webhook.domain.com {
       handle /tribute-webhook* {
           reverse_proxy remnawave_bot:8081
       }
       
       handle /cryptobot-webhook* {
           reverse_proxy remnawave_bot:8081
       }
       
       handle /mulenpay-webhook* {
           reverse_proxy remnawave_bot:8081
       }
       
       handle /pal24-webhook* {
           reverse_proxy remnawave_bot:8084
       }
       
       handle /wata-webhook* {
           reverse_proxy remnawave_bot:8081
       }
       
       handle /yookassa-webhook* {
           reverse_proxy remnawave_bot:8082
       }
       
       handle /health {
           reverse_proxy remnawave_bot:8081/health
       }
   }
   
   miniapp.domain.com {
       encode gzip zstd
       root * /miniapp
       file_server
       
       @config path /app-config.json
       header @config Access-Control-Allow-Origin "*"
       
       reverse_proxy /miniapp/* remnawave_bot:8080 {
           header_up Host {host}
           header_up X-Real-IP {remote_host}
       }
   }
   ```

4. Запустите прокси:
   ```bash
   docker compose -f docker-compose.caddy.yml up -d
   ```

### 4. Ручная настройка nginx в Docker

1. Создайте каталог `/opt/nginx-remnawave` и поместите туда `docker-compose.nginx.yml`:
   ```yaml
   services:
     nginx:
       image: nginx:1.25-alpine
       container_name: remnawave_nginx
       restart: unless-stopped
       ports:
         - "80:80"
         - "443:443"
       volumes:
         - ./nginx.conf:/etc/nginx/nginx.conf:ro
         - ./certs:/etc/ssl/private:ro
         - ./miniapp:/var/www/remnawave-miniapp:ro
       networks:
         - bot_network

   networks:
     bot_network:
       external: true
   ```

2. Пример `nginx.conf`:
   ```nginx
   events {}

   http {
     include /etc/nginx/mime.types;
     sendfile on;
     tcp_nopush on;
     tcp_nodelay on;
     keepalive_timeout 65;

     upstream remnawave_bot_hooks {
       server remnawave_bot:8081;
     }

     upstream remnawave_bot_yookassa {
       server remnawave_bot:8082;
     }

     upstream remnawave_bot_api {
       server remnawave_bot:8080;
     }

     server {
       listen 80;
       listen 443 ssl http2;
       server_name hooks.example.com;

       ssl_certificate /etc/ssl/private/hooks.fullchain.pem;
       ssl_certificate_key /etc/ssl/private/hooks.privkey.pem;

       location = /webhook { proxy_pass http://remnawave_bot_hooks; }
       location /tribute-webhook { proxy_pass http://remnawave_bot_hooks; }
       location /cryptobot-webhook { proxy_pass http://remnawave_bot_hooks; }
       location /mulenpay-webhook { proxy_pass http://remnawave_bot_hooks; }
       location /wata-webhook { proxy_pass http://remnawave_bot_hooks; }
       location /pal24-webhook { proxy_pass http://remnawave_bot:8084; }
       location /yookassa-webhook { proxy_pass http://remnawave_bot_yookassa; }

       proxy_set_header Host $host;
       proxy_set_header X-Real-IP $remote_addr;
     }

     server {
       listen 80;
       listen 443 ssl http2;
       server_name miniapp.example.com;

       ssl_certificate /etc/ssl/private/miniapp.fullchain.pem;
       ssl_certificate_key /etc/ssl/private/miniapp.privkey.pem;

       root /var/www/remnawave-miniapp;
       index index.html;

       location /miniapp/ {
         proxy_pass http://remnawave_bot_api/miniapp/;
         proxy_set_header X-API-Key "КЛЮЧ-WEBAPI";
       }
     }
   }
   ```

---

## ⚙️ Конфигурация

### 🔧 Основные параметры

| Настройка | Где взять | Пример |
|-----------|-----------|---------|
| 🤖 **BOT_TOKEN** | [@BotFather](https://t.me/BotFather) | `1234567890:AABBCCdd...` |
| 🔑 **REMNAWAVE_API_KEY** | Твоя Remnawave панель | `eyJhbGciOiJIUzI1...` |
| 🌐 **REMNAWAVE_API_URL** | URL твоей панели | `https://panel.example.com` |
| 🛡️ **REMNAWAVE_SECRET_KEY** | Ключ защиты панели | `secret_name:secret_value` |
| 👑 **ADMIN_IDS** | Твой Telegram ID | `123456789,987654321` |

### 🌐 Интеграция веб-админки

Подробное пошаговое руководство по запуску административного веб-API и подключению внешней панели находится в [docs/web-admin-integration.md](docs/web-admin-integration.md).

### 📱 Telegram Mini App ЛК

Инструкция по развёртыванию мини-приложения, публикации статической страницы и настройке reverse-proxy доступна в [docs/miniapp-setup.md](docs/miniapp-setup.md).

### 📊 Статус серверов в главном меню

| Переменная | Описание | Пример |
|------------|----------|--------|
| `SERVER_STATUS_MODE` | Режим работы кнопки: `disabled`, `external_link`, `external_link_miniapp` или `xray` | `xray` |
| `SERVER_STATUS_EXTERNAL_URL` | Прямая ссылка на внешний мониторинг | `https://status.example.com` |
| `SERVER_STATUS_METRICS_URL` | URL страницы метрик XrayChecker | `https://sub.example.com/metrics` |

### 🛡️ Защита панели Remnawave

Для панелей, защищенных через [remnawave-reverse-proxy](https://github.com/eGamesAPI/remnawave-reverse-proxy):

```env
# Для панелей установленных скриптом eGames
REMNAWAVE_SECRET_KEY=XXXXXXX:DDDDDDDD

# Или если ключ и значение одинаковые
REMNAWAVE_SECRET_KEY=secret_key_name
```

### 📊 Режимы продажи трафика

#### **Выбираемые пакеты** (по умолчанию)
```env
TRAFFIC_SELECTION_MODE=selectable
TRAFFIC_PACKAGES_CONFIG="5:2000:false,10:3500:false,25:7000:false,50:11000:true,100:15000:true,250:17000:false,500:19000:false,1000:19500:true,0:20000:true"
```

#### **Фиксированный лимит**
```env
TRAFFIC_SELECTION_MODE=fixed
FIXED_TRAFFIC_LIMIT_GB=100  # 0 = безлимит
TRAFFIC_PACKAGES_CONFIG="100:15000:true"
```

### 💰 Система ценообразования

Цена подписки рассчитывается по формуле:
**Базовая цена + Стоимость трафика + Доп. устройства + Доп. серверы**

**Пример расчета для подписки на 180 дней:**
- Базовый период: 400₽
- Трафик безлимит: 200₽/мес × 6 мес = 1200₽
- 4 устройства: 50₽/мес × 6 мес = 300₽
- 2 сервера: 100₽/мес × 6 мес = 1200₽
- **Итого: 3100₽**

### 📱 Управление устройствами

```env
# Бесплатные устройства в триал подписке
TRIAL_DEVICE_LIMIT=1

# Бесплатные устройства в платной подписке
DEFAULT_DEVICE_LIMIT=3

# Максимум устройств для покупки (0 = без лимита)
MAX_DEVICES_LIMIT=15
```

### 👥 Реферальная система

```env
# Включение/выключение реферальной программы
REFERRAL_PROGRAM_ENABLED=true

# Минимальная сумма пополнения для активации бонусов
REFERRAL_MINIMUM_TOPUP_KOPEKS=10000

# Бонус новому пользователю при первом пополнении
REFERRAL_FIRST_TOPUP_BONUS_KOPEKS=10000

# Бонус пригласившему при первом пополнении реферала
REFERRAL_INVITER_BONUS_KOPEKS=10000

# Процент комиссии с последующих пополнений
REFERRAL_COMMISSION_PERCENT=25
```

### 🔄 Автосинхронизация Remnawave

```env
# Включение автоматической синхронизации серверов
REMNAWAVE_AUTO_SYNC_ENABLED=true

# Время синхронизации (через запятую, формат HH:MM)
REMNAWAVE_AUTO_SYNC_TIMES=03:00,15:00
```

### 🛡️ Мониторинг и техническое обслуживание

```env
# Автоматический режим тех. работ
MAINTENANCE_MODE=false
MAINTENANCE_AUTO_ENABLE=true
MAINTENANCE_MONITORING_ENABLED=true
MAINTENANCE_CHECK_INTERVAL=30

# Интервал проверки состояния панели (секунды)
MONITORING_INTERVAL=60
```

### 🛒 Умная корзина

```env
# Redis для сохранения корзины (требуется)
REDIS_URL=redis://redis:6379/0
```

<details>
<summary>🔧 Полная конфигурация .env</summary>

```env
# ===============================================
# 🤖 REMNAWAVE BEDOLAGA BOT CONFIGURATION
# ===============================================

# ===== TELEGRAM BOT =====
BOT_TOKEN=
ADMIN_IDS=
SUPPORT_USERNAME=@support

# Уведомления администраторов
ADMIN_NOTIFICATIONS_ENABLED=true
ADMIN_NOTIFICATIONS_CHAT_ID=-1001234567890
ADMIN_NOTIFICATIONS_TOPIC_ID=123
ADMIN_NOTIFICATIONS_TICKET_TOPIC_ID=126

# Автоматические отчеты
ADMIN_REPORTS_ENABLED=false
ADMIN_REPORTS_SEND_TIME=10:00

# Обязательная подписка на канал
CHANNEL_SUB_ID=
CHANNEL_IS_REQUIRED_SUB=false
CHANNEL_LINK=

# ===== DATABASE CONFIGURATION =====
DATABASE_MODE=auto
DATABASE_URL=

# PostgreSQL настройки
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=remnawave_bot
POSTGRES_USER=remnawave_user
POSTGRES_PASSWORD=secure_password_123

# Redis
REDIS_URL=redis://redis:6379/0

# ===== REMNAWAVE API =====
REMNAWAVE_API_URL=https://panel.example.com
REMNAWAVE_API_KEY=your_api_key_here
REMNAWAVE_AUTH_TYPE=api_key
REMNAWAVE_SECRET_KEY=

# Автосинхронизация
REMNAWAVE_AUTO_SYNC_ENABLED=true
REMNAWAVE_AUTO_SYNC_TIMES=03:00,15:00

# Шаблон описания пользователя
REMNAWAVE_USER_DESCRIPTION_TEMPLATE="Bot user: {full_name} {username}"
# Шаблон имени пользователя в панели
REMNAWAVE_USER_USERNAME_TEMPLATE="user_{telegram_id}"
REMNAWAVE_USER_DELETE_MODE=delete

# ===== ПОДПИСКИ =====
TRIAL_DURATION_DAYS=3
TRIAL_TRAFFIC_LIMIT_GB=10
TRIAL_DEVICE_LIMIT=1

DEFAULT_DEVICE_LIMIT=3
MAX_DEVICES_LIMIT=15

# ===== НАСТРОЙКИ ТРАФИКА =====
TRAFFIC_SELECTION_MODE=selectable
FIXED_TRAFFIC_LIMIT_GB=100
AVAILABLE_SUBSCRIPTION_PERIODS=30,90,180
AVAILABLE_RENEWAL_PERIODS=30,90,180

# ===== ЦЕНЫ (в копейках) =====
BASE_SUBSCRIPTION_PRICE=0
PRICE_14_DAYS=7000
PRICE_30_DAYS=9900
PRICE_60_DAYS=25900
PRICE_90_DAYS=36900
PRICE_180_DAYS=69900
PRICE_360_DAYS=109900

# Скидки для базовых пользователей
BASE_PROMO_GROUP_PERIOD_DISCOUNTS_ENABLED=false
BASE_PROMO_GROUP_PERIOD_DISCOUNTS=60:10,90:20,180:40,360:70

TRAFFIC_PACKAGES_CONFIG="5:2000:false,10:3500:false,25:7000:false,50:11000:true,100:15000:true,0:20000:true"
PRICE_PER_DEVICE=5000
DEVICES_SELECTION_ENABLED=true
# Единое количество устройств для режима без выбора (0 — не назначать устройства)
DEVICES_SELECTION_DISABLED_AMOUNT=0

# ===== РЕФЕРАЛЬНАЯ СИСТЕМА =====
REFERRAL_PROGRAM_ENABLED=true
REFERRAL_MINIMUM_TOPUP_KOPEKS=10000
REFERRAL_FIRST_TOPUP_BONUS_KOPEKS=10000
REFERRAL_INVITER_BONUS_KOPEKS=10000
REFERRAL_COMMISSION_PERCENT=25

# ===== АВТОПРОДЛЕНИЕ =====
AUTOPAY_WARNING_DAYS=3,1
DEFAULT_AUTOPAY_ENABLED=true
DEFAULT_AUTOPAY_DAYS_BEFORE=3
MIN_BALANCE_FOR_AUTOPAY_KOPEKS=10000

# ===== ПЛАТЕЖНЫЕ СИСТЕМЫ =====

# Telegram Stars
TELEGRAM_STARS_ENABLED=true
TELEGRAM_STARS_RATE_RUB=1.3

# Tribute
TRIBUTE_ENABLED=false
TRIBUTE_API_KEY=
TRIBUTE_WEBHOOK_PATH=/tribute-webhook

# YooKassa
YOOKASSA_ENABLED=false
YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
YOOKASSA_SBP_ENABLED=false
YOOKASSA_WEBHOOK_PATH=/yookassa-webhook

# CryptoBot
CRYPTOBOT_ENABLED=false
CRYPTOBOT_API_TOKEN=
CRYPTOBOT_WEBHOOK_PATH=/cryptobot-webhook

# Heleket
HELEKET_ENABLED=false
HELEKET_MERCHANT_ID=
HELEKET_API_KEY=
HELEKET_WEBHOOK_PATH=/heleket-webhook
HELEKET_WEBHOOK_PORT=8086

# MulenPay
MULENPAY_ENABLED=false
MULENPAY_API_KEY=
MULENPAY_SECRET_KEY=
MULENPAY_SHOP_ID=
MULENPAY_WEBHOOK_PATH=/mulenpay-webhook

# PayPalych / Pal24
PAL24_ENABLED=false
PAL24_API_TOKEN=
PAL24_SHOP_ID=
PAL24_WEBHOOK_PATH=/pal24-webhook
PAL24_SBP_BUTTON_VISIBLE=true
PAL24_CARD_BUTTON_VISIBLE=true

# WATA
WATA_ENABLED=false
WATA_TOKEN=
WATA_TERMINAL_ID=
WATA_WEBHOOK_PATH=/wata-webhook
WATA_WEBHOOK_HOST=0.0.0.0
WATA_WEBHOOK_PORT=8085

# ===== ИНТЕРФЕЙС И UX =====
ENABLE_LOGO_MODE=true
LOGO_FILE=vpn_logo.png
MAIN_MENU_MODE=default
HIDE_SUBSCRIPTION_LINK=false
CONNECT_BUTTON_MODE=guide

# ===== МОНИТОРИНГ И УВЕДОМЛЕНИЯ =====
MONITORING_INTERVAL=60
ENABLE_NOTIFICATIONS=true
NOTIFICATION_RETRY_ATTEMPTS=3

# ===== СТАТУС СЕРВЕРОВ =====
SERVER_STATUS_MODE=disabled
SERVER_STATUS_EXTERNAL_URL=
SERVER_STATUS_METRICS_URL=
SERVER_STATUS_ITEMS_PER_PAGE=10

# ===== РЕЖИМ ТЕХНИЧЕСКИХ РАБОТ =====
MAINTENANCE_MODE=false
MAINTENANCE_CHECK_INTERVAL=30
MAINTENANCE_AUTO_ENABLE=true
MAINTENANCE_MONITORING_ENABLED=true

# ===== ЛОКАЛИЗАЦИЯ =====
DEFAULT_LANGUAGE=ru
AVAILABLE_LANGUAGES=ru,en
LANGUAGE_SELECTION_ENABLED=true

# ===== СИСТЕМА БЕКАПОВ =====
BACKUP_AUTO_ENABLED=true
BACKUP_INTERVAL_HOURS=24
BACKUP_TIME=03:00
BACKUP_MAX_KEEP=7
BACKUP_SEND_ENABLED=true

# ===== ПРОВЕРКА ОБНОВЛЕНИЙ БОТА =====
VERSION_CHECK_ENABLED=true
VERSION_CHECK_INTERVAL_HOURS=1

# ===== ЛОГИРОВАНИЕ =====
LOG_LEVEL=INFO
LOG_FILE=logs/bot.log
```

</details>

---

#### ⭐ Функционал

<table>
<tr>
<td width="50%" valign="top">

### 👤 **Для пользователей**

🧭 **Онбординг и доступ**
- 🌐 Выбор языка интерфейса (RU/EN), динамическая локализация
- 📜 Принятие правил, оферты и политики конфиденциальности
- 📡 Проверка подписки на обязательный канал
- 🔗 Deeplink-инвайты, UTM-кампании и реферальные коды

🛒 **Умная покупка подписок**
- 📅 Гибкие периоды (14–360 дней) со скидками
- 📊 Выбор трафика: фиксированный лимит, пакеты или безлимит
- 🌍 Автоматический выбор сервера или мультивыбор
- 📱 Настройка количества устройств и серверов
- 🧾 Динамический калькулятор стоимости
- 💾 **Сквозная корзина** - сохранение параметров при недостатке баланса
- ↩️ Быстрый возврат к оформлению после пополнения

🧪 **Тестовая подписка**
- 🎁 Гибко настраиваемый триал и welcome-цепочка
- 🔔 Уведомления об истечении и автоконверсия
- 💎 Автовыдача бонусов за кампании и инвайты
- 🛡️ Контроль обязательной подписки на канал (отключает подписку при отписке)

💰 **Платежи и баланс**
- ⭐ Telegram Stars
- 💳 Tribute
- 💳 YooKassa (СБП + банковские карты)
- 💰 CryptoBot (USDT, TON, BTC, ETH и др.)
- 🪙 Heleket (криптовалюта с наценкой)
- 💳 MulenPay (СБП)
- 🏦 PayPalych/Pal24 (СБП + карты)
- 💳 **WATA**
- 📥 Автогенерация счетов и webhook-уведомления
- 💼 История операций 
- 🔄 Автоплатёж с настройкой дня списания
- 🎁 Реферальные и промо-бонусы
- ⚡ **Быстрое пополнение** с кнопками выбора суммы

📱 **Управление подписками**
- 📈 Реальный трафик, устройства и серверы 
- 🌍 Переключение серверов и стран 
- 📱 Сброс HWID
- 🧩 Смена языка, промогруппы и параметров
- 🧾 Просмотр активных услуг и статуса
- 🔗 Получение ссылок подключения в один клик

🛟 **Поддержка и самообслуживание**
- 🎫 **Система тикетов** с вложениями
- 📚 FAQ, правила, оферта и политика
- 💬 Быстрые ссылки на поддержку

🧩 **Бонусы и промо**
- 🎫 Промокоды на деньги, дни, триал подписку
- 🎁 **Персональные промо-предложения** от админов
- 💰 **Тестовый доступ к серверам** через промо-акции
- 💸 **Автоматические скидки** при оплате и автопродлении
- 👥 Реферальная программа с комиссиями и бонусами
- 📊 Аналитика доходов и конверсии рефералов
- 🔗 Генерация реферальных ссылок и QR кодов

💎 **Промо-группы и скидки**
- 🏷️ **Система промогрупп** с индивидуальными скидками
- 💰 Скидки на серверы, трафик и устройства
- 📊 **Скидочные уровни за траты** - прозрачная система лояльности
- 📈 Автоматическое повышение уровня при достижении порога
- 🎯 **Скидки за длительные периоды** подписки для базовых юзеров

📱 **Mini App и гайды**
- 🖥️ **Полноценный личный кабинет** в Telegram WebApp
- 📊 Управление подпиской и параметрами
- 💳 Интегрированные платежи
- 🎁 Активация промо-оферов и промокодов
- 📱 Управление устройствами
- 👥 Реферальная статистика
- 📋 FAQ и юридические документы
- 📥 Библиотека загрузочных ссылок для клиентов
- 🛰️ Web API для внешних интеграций

</td>
<td width="50%" valign="top">

### ⚙️ **Для администраторов**

📊 **Аналитика и отчётность**
- 📈 Дашборды по пользователям, подпискам и трафику
- 💰 Детализация платежей по всем источникам
- 🧮 Продажи по тарифам, устройствам и странам
- 📣 Эффективность кампаний, промокодов и UTM
- 🎯 **Статистика по промо-группам** и скидочным уровням
- 📊 **Расширенная фильтрация** пользователей (баланс, траты, активность)

👥 **Управление пользователями**
- 🔍 Поиск, фильтры и детальные карточки
- 💰 Ручное изменение баланса 
- 📱 Изменение лимитов устройств, трафика, серверов
- 🔄 Сброс HWID и перегенерация подписки
- 🎯 Назначение промогрупп и тарифов
- 💳 **Покупка подписки пользователю** прямо из админки
- ⏰ **Продление/сокращение срока** подписки (±365 дней)
- 🚫 Блокировки с таймером и аудит действий
- 🛡️ **Защита от запрещенных никнеймов** с настраиваемым список банвордов (автоблокировка подозрительных имен)

🎯 **Продажи, маркетинг и удержание**
- 🎫 Промокоды 
- 💳 Промо-группы со скидками
- 🎁 **Персональные промо-предложения** с поиском получателей
- 💸 **Тестовые серверы** - временная выдача доступа
- 💰 **Автоматические скидки** при оплате
- 📣 **Рекламные кампании** с deeplink и бонусами (Автовыдача подписки / баланса при переходе)
- 📨 Рассылки по сегментам с медиа и кнопка
- 🎨 **Кастомные кнопки** для рассылок (подключение, подписка, поддержка, партнертка и тд)
- 🔘 Настройка главного меню и приветственных экранов

🛟 **Поддержка и модерация**
- 🎫 **Центр тикетов** с приоритетами и статусами
- ⏱️ **SLA таймеры** и автоуведомления
- 🧑‍⚖️ Роли модераторов с ограниченным доступом (без выдачи админ прав)
- 📊 Детальный журнал всех операций
- 🚫 Блокировки нарушителей
- 🧾 История диалогов и быстрые ответы

🔔 **Уведомления и коммуникации**
- 📢 **Топики для событий** (покупки, триалы, техработы)
- 🔔 Настройка уведомлений и расписаний
- 📨 **Управление контентом** - политика, оферта, FAQ
- 💬 Автоматические сообщения о задолженностях

🧰 **Обслуживание и DevOps**
- 🛠️ `install_bot.sh` - **интерактивное меню управления**
- 🚧 Ручной и авто-режим техработ
- 🗒️ Просмотр системных логов и health-check
- 🔄 **Автосинхронизация Remnawave** по расписанию и при старте бота
- ♻️ Проверка обновлений репозитория
- 📊 **Мониторинг серверов** (интеграция с XrayChecker)

🗄️ **Бекапы и восстановление**
- 🗓️ **Умные автобекапы** с гибким расписанием
- 📦 Ручные бекапы с выбором содержимого
- 📤 Отправка архивов в выделенный чат/топик
- 🔁 Восстановление без остановки бота
- ✅ Автоматическая синхронизация sequences после восстановления

💳 **Биллинг и настройки**
- ⚙️ **Управление ценами** без перезапуска бота
- 🔘 **Управление пакетами трафика** (включение/отключение)
- 🧪 Тестовые платежи для каждого провайдера
- 🪝 Управление вебхуками всех платёжных систем

🏗️ **REST API для интеграций**
- 🔌 **FastAPI Web API** с полной документацией
- 🔑 Управление API-ключами и токенами
- 📊 Эндпоинты для подписок, пользователей, транзакций
- 🎁 API промо-системы и рассылок
- 📋 API управления контентом и настройками

</td>
</tr>
</table>

### 🤖 Автоматизация и экосистема

- 🔄 **Мониторинг Remnawave** - регулярная проверка API, автоматическое включение/выключение техработ
- 🔄 **Автосинхронизация серверов** - фоновая синхронизация по расписанию
- 🛒 **Умная корзина** - сохранение параметров подписки в Redis при недостатке баланса
- 🛡️ **Антифрод** - валидация подписки на канал
- 🚫 **Защита от блокировок** - автоблокировка подозрительных никнеймов и имитации фишинг аккаунтов
- 🧠 **Асинхронная архитектура** - aiogram 3, PostgreSQL/SQLite, Redis и очереди задач
- 🌐 **Мультиязычность** - локализации RU/EN, быстрый выбор языка
- 📦 **Интеграция с Remnawave API** - автоматическое создание пользователей и синхронизация
- 🔄 **Миграция сквадов** - массовый перенос пользователей между серверами
- 🧾 **История операций** - хранение всех транзакций и действий для аудита

### 🌐 Веб-API и мини-приложение

- ⚙️ **FastAPI Web API** с эндпоинтами для управления всеми аспектами бота
- 🔑 **Управление API-ключами** - выпуск, отзыв, реактивация токенов
- 🛰️ **Mini App** - полноценный личный кабинет внутри Telegram
- 💳 **Интегрированные платежи** в Mini App (Stars, Pal24, YooKassa, WATA)
- 🧭 **App Config** - централизованная раздача ссылок на клиенты
- 🪝 **Платёжные вебхуки** - встроенные серверы для всех платёжных систем
- 📡 **Мониторинг серверов** - REST-эндпоинты для просмотра нод и статистики

## 🚀 Производительность

| Пользователей | Память | CPU | Диск | Описание |
|---------------|--------|-----|------|----------|
| **1,000** | 512MB | 1 vCPU | 10GB | ✅ Стартап |
| **10,000** | 2GB | 2 vCPU | 50GB | ✅ Малый бизнес |
| **50,000** | 4GB | 4 vCPU | 100GB | ✅ Средний бизнес |
| **100,000+** | 8GB+ | 8+ vCPU | 200GB+ | 🚀 Enterprise |

---

## 🏗️ Технологический стек

### 💪 Современные технологии

- **🐍 Python 3.13+** с AsyncIO - максимальная производительность
- **🗄️ PostgreSQL 15+** - надежное хранение данных
- **⚡ Redis** - быстрое кеширование и сессии (для корзины)
- **🐳 Docker** - простое развертывание в любой среде
- **🔗 SQLAlchemy ORM** - безопасная работа с БД
- **🚀 aiogram 3** - современная Telegram Bot API
- **⚡ FastAPI** - высокопроизводительный REST API
- **📦 Pydantic v2** - валидация данных

---

## 🔧 Первичная настройка

После запуска необходимо:

1. **📡 Синхронизация серверов** (обязательно!)
   - Зайди в бот → **Админ панель** → **Подписки** → **Управление серверами**
   - Нажми **Синхронизация** и дождись завершения
   - Без этого пользователи не смогут выбирать страны!

2. **👥 Синхронизация пользователей** (если есть база)
   - **Админ панель** → **Remnawave** → **Синхронизация**
   - **Синхронизировать всех** → дождись импорта

3. **💳 Настройка платежных систем**
   - **Telegram Stars**: Работает автоматически
   - **Tribute**: Настрой webhook на `https://your-domain.com/tribute-webhook`
   - **YooKassa**: Настрой webhook на `https://your-domain.com/yookassa-webhook`
   - **CryptoBot**: Настрой webhook на `https://your-domain.com/cryptobot-webhook`
   - **Heleket**: Настрой webhook на `https://your-domain.com/heleket-webhook`
   - **MulenPay**: Настрой webhook на `https://your-domain.com/mulenpay-webhook`
   - **PayPalych**: Укажи Result URL `https://your-domain.com/pal24-webhook` в кабинете Pal24
   - **WATA**: Настрой webhook на `https://your-domain.com/wata-webhook`

4. **🔄 Настройка автосинхронизации** (опционально)
   - В `.env` установи `REMNAWAVE_AUTO_SYNC_ENABLED=true`
   - Укажи время синхронизации в `REMNAWAVE_AUTO_SYNC_TIMES=03:00,15:00`

### 🛠️ Настройка уведомлений в топик группы

#### 1. Переменные окружения

Добавьте в файл `.env`:

```env
# Уведомления администраторов
ADMIN_NOTIFICATIONS_ENABLED=true
ADMIN_NOTIFICATIONS_CHAT_ID=-1001234567890  # ID канала/группы
ADMIN_NOTIFICATIONS_TOPIC_ID=123             # ID топика (опционально)
ADMIN_NOTIFICATIONS_TICKET_TOPIC_ID=126      # ID топика для тикетов
```

#### 2. Создание канала

1. **Создайте приватный канал** или группу для уведомлений
2. **Добавьте бота** как администратора с правами отправки сообщений
3. **Получите ID канала**:
   - Отправьте любое сообщение в канал
   - Перешлите его боту @userinfobot
   - Скопируйте Chat ID (например: `-1001234567890`)

#### 3. Настройка топиков (опционально)

Если используете супергруппу с топиками:

1. **Включите топики** в настройках группы
2. **Создайте топики** для уведомлений (например, "Уведомления", "Тикеты")
3. **Получите ID топика** из URL веб-версии Telegram или используйте бота

---

## 🐛 Устранение неполадок

### 🏥 Health Checks
- **Основной**: `http://localhost:8081/health`
- **YooKassa**: `http://localhost:8082/health`
- **Pal24**: `http://localhost:8084/health`

### 🔧 Полезные команды
```bash
# Просмотр логов в реальном времени
docker compose logs -f bot

# Статус всех контейнеров
docker compose ps

# Перезапуск только бота
docker compose restart bot

# Проверка базы данных
docker compose exec postgres pg_isready -U remnawave_user

# Подключение к базе данных
docker compose exec postgres psql -U remnawave_user -d remnawave_bot

# Проверка Redis
docker compose exec redis redis-cli ping

# Проверка использования ресурсов
docker stats

# Очистка Docker
docker system prune
```

### 🚨 Частые проблемы и решения

| Проблема | Диагностика | Решение |
|----------|-------------|---------|
| **Бот не отвечает** | `docker logs remnawave_bot` | Проверь `BOT_TOKEN` и интернет |
| **Ошибки БД** | `docker compose ps postgres` | Проверь статус PostgreSQL |
| **Webhook не работает** | Проверь порты 8081/8082/8084 | Настрой прокси-сервер |
| **API недоступен** | Проверь логи бота | Проверь `REMNAWAVE_API_URL` |
| **Корзина не сохраняется** | `docker compose ps redis` | Проверь статус Redis |
| **Платежи не проходят** | Проверь webhook'и | Настрой URL в платежных системах |

---

## 💡 Использование

### 👤 **Для пользователей**

1. **🚀 Старт** → Найди бота и нажми `/start`
2. **🌐 Язык** → Выбери язык интерфейса (RU/EN)
3. **📋 Правила** → Прими правила сервиса
4. **💰 Баланс** → Пополни через любой удобный способ
5. **🛒 Подписка** → Выбери тариф и параметры
6. **📱 Подключение** → Получи ссылку или конфиг
7. **👥 Партнерка** → Поделись ссылкой и получай бонусы

### ⚙️ **Для администраторов**

Доступ через **"⚙️ Админ панель"**:

- **📦 Подписки** → настройка серверов, цен, синхронизация
- **👥 Пользователи** → поиск, редактирование, блокировка
- **💎 Промо-группы** → управление скидочными группами и уровнями
- **🎁 Промокоды** → создание и статистика
- **🎯 Промо-предложения** → персональные акции и скидки
- **📨 Рассылки** → уведомления по сегментам
- **📣 Кампании** → управление рекламными кампаниями
- **🎫 Тикеты** → система поддержки
- **📄 Контент** → политика, оферта, FAQ
- **🖥 Remnawave** → мониторинг, синхронизация
- **📊 Статистика** → детальная аналитика

---

## 🛡️ Безопасность

### 🔐 Защита панели Remnawave

Бот поддерживает интеграцию с системой защиты панели:

```env
# Для защищенных панелей
REMNAWAVE_SECRET_KEY=secret_name:secret_value

# Для панелей eGames скрипта  
REMNAWAVE_SECRET_KEY=XXXXXXX:DDDDDDDD
```

### 🔒 Дополнительные меры безопасности

- **Валидация всех входящих данных**
- **Rate limiting для защиты от спама**  
- **Шифрование чувствительных данных**
- **Автоматическое управление сессиями**
- **Мониторинг подозрительной активности**
- **Защита от запрещенных никнеймов** - автоблокировка имитации официальных аккаунтов
- **Защита от обфусцированных доменов** в отображаемом имени

---

## 🤝 Как помочь проекту

- 🔍 [**Сообщай о багах**](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/issues) с подробным описанием
- 💡 [**Предлагай идеи**](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/discussions) для улучшения
- ⭐ **Ставь звезды** проекту - это мотивирует разработку!
- 📢 **Рассказывай друзьям** о проекте
- 💝 **[Поддержи разработку](https://t.me/tribute/app?startapp=duUO)** - помоги проекту расти
- 🔧 **Отправляй Pull Requests** - внеси свой вклад в код

---

## 💬 Поддержка и сообщество

### 📞 **Контакты**

- **💬 Telegram:** [@fringg](https://t.me/fringg) - вопросы по разработке (только по делу!)
- **💬 Telegram Group:** [Bedolaga Chat](https://t.me/+wTdMtSWq8YdmZmVi) - общение, вопросы, предложения
- **🐛 Issues:** [GitHub Issues](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/issues) - баги и предложения

### 📚 **Полезные ресурсы**

- **📖 [Remnawave Docs](https://docs.remna.st)** - документация панели
- **🤖 [Telegram Bot API](https://core.telegram.org/bots/api)** - API ботов  
- **🐳 [Docker Guide](https://docs.docker.com/get-started/)** - обучение Docker
- **🛡️ [Reverse Proxy](https://github.com/eGamesAPI/remnawave-reverse-proxy)** - защита панели

---

## 💝 Благодарности

### 🌟 **Топ спонсоры проекта**

<table align="center">
<tr>
<th>🏆 Место</th>
<th>👤 Спонсор</th>
<th>💰 Сумма</th>
<th>💬 Благодарность</th>
</tr>

<tr>
<td>🥇</td>
<td><strong>@SmartSystemCompany</strong></td>
<td>₽8,500</td>
<td>За щедрую поддержку и вклад в развитие</td>
</tr>

<tr>
<td>🥈</td>
<td><strong>@pilot_737800</strong></td>
<td>₽7,750</td>
<td>За веру в проект с самого начала</td>
</tr>

<tr>
<td>🥈</td>
<td><strong>@psych0O</strong></td>
<td>$60</td>
<td>За щедрую поддержку и вклад в развитие</td>
</tr>

<tr>
<td>🥉</td>
<td><strong>@Vldmrmtn</strong></td>
<td>₽5,000</td>
<td>За значительную поддержку проекта</td>
</tr>

<tr>
<td>4</td>
<td><strong>@k0tbtc</strong></td>
<td>₽3,000</td>
<td>За поддержку и доверие</td>
</tr>

<tr>
<td>5</td>
<td><strong>@Legacyyy777</strong></td>
<td>₽2,900</td>
<td>За ценные предложения и UX улучшения</td>
</tr>

<tr>
<td>6</td>
<td><strong>@sklvg</strong></td>
<td>₽3,000</td>
<td>За международную поддержку</td>
</tr>

</table>

### 🌟 **Особая благодарность контрибьюторам**

- **@yazhog** - легенда проекта! За крутые PR'ы, рефакторинг, автобекапы и навигацию
- **@Gy9vin** - за модульную архитектуру, быстрое пополнение, админ-функции и тестирование
- **@Legacyyy777** - за улучшения рассылки, PayPalych, MulenPay и UX фиксы
- **@SantaSpeen** - за актуализацию app-config.json и кучу рекомендаций
- **@PEDZEO** - за SLA поддержки и управление модераторами

### 🎉 **Сообщество**

- **Remnawave Team** - за отличную панель и стабильный API
- **Сообщество Bedolaga** - за активное тестирование и обратную связь
- **Всем пользователям** - за доверие и использование бота

---

## 📋 Roadmap

### 🚧 **В разработке**

- 🌎 **Веб-панель** - полноценная административная панель
- 📊 **Расширенная аналитика** - больше метрик и графиков  
- 🔄 **API для интеграций** - подключение внешних сервисов
- 🎨 **Темы оформления** - кастомизация интерфейса Mini App

### ✅ **Недавно добавлено**

- 💳 **WATA** - оплата банковскими картами
- 🔄 **Автосинхронизация Remnawave** - фоновая синхронизация серверов
- 🛒 **Умная корзина** - сохранение параметров подписки
- 🏗️ **Модульная архитектура** - подписок и платежей
- 🖥️ **Полноценный личный кабинет** в Mini App
- 💎 **Промо-группы и скидочные уровни** - система лояльности
- 🎁 **Персональные промо-предложения** - таргетированные акции
- 📄 **Система управления контентом** - политика, оферта, FAQ
- 🎫 **Система тикетов** - поддержка пользователей
- 📊 **Мониторинг серверов** - интеграция с XrayChecker
- 🛡️ **Защита от блокировок** - антифрод система

---

<div align="center">

## 📄 Лицензия

Проект распространяется под лицензией **MIT**

[📜 Посмотреть лицензию](LICENSE)

---

## 🚀 Начни уже сегодня!

<table align="center">
<tr>
<td align="center">
<h3>🧪 Протестируй бота</h3>
<a href="https://t.me/FringVPN_bot">
<img src="https://img.shields.io/badge/Telegram-Тестовый_бот-blue?style=for-the-badge&logo=telegram" alt="Test Bot">
</a>
</td>
<td align="center">
<h3>💬 Присоединись к сообществу</h3>
<a href="https://t.me/+wTdMtSWq8YdmZmVi">
<img src="https://img.shields.io/badge/Telegram-Bedolaga_Chat-blue?style=for-the-badge&logo=telegram" alt="Community">
</a>
</td>
</tr>
<tr>
<td align="center">
<h3>⭐ Поставь звезду</h3>
<a href="https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot">
<img src="https://img.shields.io/badge/GitHub-Звезда-yellow?style=for-the-badge&logo=github" alt="Star">
</a>
</td>
<td align="center">
<h3>💝 Поддержи проект</h3>
<a href="https://t.me/tribute/app?startapp=duUO">
<img src="https://img.shields.io/badge/Tribute-Донат-green?style=for-the-badge&logo=heart" alt="Donate">
</a>
</td>
</tr>
</table>

---

## 🔄 Быстрые команды

### 📦 Установка и запуск
```bash
# Автоустановка (рекомендуется)
git clone https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot.git
cd remnawave-bedolaga-telegram-bot
chmod +x install_bot.sh
./install_bot.sh

# Ручной запуск
docker compose up -d
docker compose logs -f
```

### 🔄 Обновление
```bash
# Через install_bot.sh (с автобэкапом)
./install_bot.sh
# Выбрать: 4. 🔄 Обновить проект из Git

# Ручное обновление
git pull
docker compose down
docker compose pull
docker compose up -d
```

### 💾 Бэкап и восстановление
```bash
# Создать бэкап через install_bot.sh
./install_bot.sh
# Выбрать: 5. 💾 Создать резервную копию

# Восстановить бэкап
./install_bot.sh
# Выбрать: 6. 📦 Восстановить из бэкапа
```

### 📊 Мониторинг
```bash
# Статус сервисов
docker compose ps

# Логи бота
docker compose logs -f bot

# Проверка здоровья
curl http://localhost:8081/health

# Использование ресурсов
docker stats
```

---

## 📈 Статистика проекта

<div align="center">

![GitHub stars](https://img.shields.io/github/stars/Fr1ngg/remnawave-bedolaga-telegram-bot?style=social)
![GitHub forks](https://img.shields.io/github/forks/Fr1ngg/remnawave-bedolaga-telegram-bot?style=social)
![GitHub watchers](https://img.shields.io/github/watchers/Fr1ngg/remnawave-bedolaga-telegram-bot?style=social)

![GitHub last commit](https://img.shields.io/github/last-commit/Fr1ngg/remnawave-bedolaga-telegram-bot)
![GitHub commit activity](https://img.shields.io/github/commit-activity/m/Fr1ngg/remnawave-bedolaga-telegram-bot)
![GitHub contributors](https://img.shields.io/github/contributors/Fr1ngg/remnawave-bedolaga-telegram-bot)

![GitHub issues](https://img.shields.io/github/issues/Fr1ngg/remnawave-bedolaga-telegram-bot)
![GitHub pull requests](https://img.shields.io/github/issues-pr/Fr1ngg/remnawave-bedolaga-telegram-bot)
![GitHub closed issues](https://img.shields.io/github/issues-closed/Fr1ngg/remnawave-bedolaga-telegram-bot)

</div>

---

## 🎯 Ключевые особенности в цифрах

<div align="center">

| Метрика | Значение |
|---------|----------|
| 💳 **Платёжных систем** | 8 (Stars, YooKassa, Tribute, CryptoBot, Heleket, MulenPay, Pal24, WATA) |
| 🌍 **Языков интерфейса** | 2 (RU, EN) с возможностью расширения |
| 📊 **Периодов подписки** | 6 (от 14 дней до года) |
| 🎁 **Типов промо-акций** | 5 (коды, группы, предложения, скидки, кампании) |
| 🔌 **REST API эндпоинтов** | 50+ для полного управления |
| 📱 **Режимов работы** | 2 (классический бот + MiniApp focus) |
| 🛡️ **Методов авторизации** | 4 (API Key, Bearer, Basic Auth, eGames) |
| 🗄️ **Способов хранения** | 2 (PostgreSQL, SQLite) с автовыбором |

</div>

---

## 🔥 Почему выбирают Bedolaga?

### 💼 **Для бизнеса**

✅ **Быстрый запуск** - от установки до первых продаж за 10 минут  
✅ **Полная автоматизация** - бот работает 24/7 без вашего участия  
✅ **Прозрачная аналитика** - всегда знаете, сколько зарабатываете  
✅ **Гибкие тарифы** - настройте цены под свою аудиторию  
✅ **Система лояльности** - удерживайте клиентов промо-группами и скидками  
✅ **Масштабируемость** - от 10 до 100,000+ пользователей  

### 🛠️ **Для разработчиков**

✅ **Современный стек** - Python 3.13, AsyncIO, PostgreSQL, Redis  
✅ **Модульная архитектура** - легко расширять и модифицировать  
✅ **Полное API** - интегрируйте с любыми сервисами  
✅ **Docker-ready** - разворачивается за минуты  
✅ **Подробная документация** - все описано и понятно  
✅ **Активное сообщество** - помощь в Telegram чате  

### 👥 **Для пользователей**

✅ **Простой интерфейс** - интуитивно понятное меню на родном языке  
✅ **Много способов оплаты** - выбирайте удобный вариант  
✅ **Быстрая поддержка** - система тикетов с приоритетами  
✅ **Прозрачность** - всегда видите, за что платите  
✅ **Бонусы и скидки** - реферальная программа и промо-акции  
✅ **Удобное управление** - все в одном месте, в Telegram  

---

## 💡 Советы по оптимизации

### ⚡ Повышение производительности

1. **Используйте Redis** для корзины и кэширования
2. **Настройте автосинхронизацию** в ночное время
3. **Включите автобэкапы** с отправкой в Telegram
4. **Оптимизируйте логирование** - LOG_LEVEL=INFO для продакшена
5. **Используйте PostgreSQL** вместо SQLite для больших баз

### 💰 Увеличение продаж

1. **Включите реферальную программу** - пользователи приведут друзей
2. **Настройте промо-группы** - дайте скидки постоянным клиентам
3. **Используйте персональные акции** - реактивируйте неактивных
4. **Запускайте кампании** - привлекайте новых через deeplink
5. **Добавьте быстрое пополнение** - упростите процесс оплаты

### 🎯 Улучшение UX

1. **Включите Mini App режим** - современный интерфейс
2. **Настройте корзину** - пользователи не потеряют выбор
3. **Добавьте FAQ** - ответьте на частые вопросы заранее
4. **Настройте быстрые ответы** - ускорьте поддержку
5. **Используйте уведомления** - держите пользователей в курсе

---

## 🔐 Безопасность и соответствие

### 🛡️ Защита данных

- ✅ Шифрование чувствительных данных в БД
- ✅ Безопасное хранение токенов и ключей
- ✅ Валидация всех входящих данных
- ✅ Защита от SQL-инъекций через ORM
- ✅ Rate limiting для предотвращения злоупотреблений
- ✅ Аудит всех административных действий

### 📋 Юридическое соответствие

- ✅ Политика конфиденциальности (настраивается)
- ✅ Публичная оферта (настраивается)
- ✅ Правила использования (настраивается)
- ✅ История транзакций для аудита
- ✅ Соответствие требованиям платёжных систем
- ✅ GDPR-ready (возможность удаления данных)

---

## 📞 Нужна помощь?

### 🆘 Частые вопросы

<details>
<summary><b>Как начать работу?</b></summary>

1. Скачайте репозиторий
2. Запустите `install_bot.sh`
3. Следуйте инструкциям установщика
4. Синхронизируйте серверы в админке
5. Готово! 🎉

</details>

<details>
<summary><b>Какие требования к серверу?</b></summary>

Минимальные:
- 1 vCPU
- 512 MB RAM
- 10 GB диск
- Ubuntu 20.04+ или Debian 11+
- Docker и Docker Compose

Рекомендуемые:
- 2+ vCPU
- 2+ GB RAM
- 50+ GB SSD
- Стабильное интернет-соединение

</details>

<details>
<summary><b>Как настроить платёжную систему?</b></summary>

1. Получите ключи API в личном кабинете платёжной системы
2. Добавьте их в `.env` файл
3. Настройте webhook URL в кабинете провайдера
4. Протестируйте через админ-панель бота
5. Включите метод для пользователей

Подробнее: [docs/payment-setup.md](docs/payment-setup.md)

</details>

<details>
<summary><b>Как обновить бота?</b></summary>

**Через install_bot.sh (рекомендуется):**
```bash
./install_bot.sh
# Выбрать: 4. 🔄 Обновить проект из Git
```

**Вручную:**
```bash
git pull
docker compose down
docker compose pull
docker compose up -d
```

Скрипт автоматически создаст бэкап перед обновлением!

</details>

<details>
<summary><b>Как сделать бэкап?</b></summary>

**Автоматически:**
- Настройте в `.env`: `BACKUP_AUTO_ENABLED=true`
- Бэкапы создаются по расписанию

**Вручную через install_bot.sh:**
```bash
./install_bot.sh
# Выбрать: 5. 💾 Создать резервную копию
```

**Через админ-панель:**
- Админ панель → Настройки → Бэкапы → Создать

</details>

<details>
<summary><b>Бот не отвечает, что делать?</b></summary>

1. Проверьте статус: `docker compose ps`
2. Посмотрите логи: `docker compose logs -f bot`
3. Проверьте BOT_TOKEN в `.env`
4. Убедитесь, что все контейнеры запущены
5. Попробуйте перезапустить: `docker compose restart`

Если не помогло - пишите в [чат поддержки](https://t.me/+wTdMtSWq8YdmZmVi)

</details>

### 💬 Куда обратиться?

- 🐛 **Баг?** → [GitHub Issues](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/issues)
- 💡 **Идея?** → [GitHub Discussions](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/discussions)
- 🆘 **Вопрос?** → [Bedolaga Chat](https://t.me/+wTdMtSWq8YdmZmVi)
- 📧 **Личное?** → [@fringg](https://t.me/fringg)

---

**Made with ❤️ by [@fringg](https://t.me/fringg) and amazing [contributors](https://github.com/Fr1ngg/remnawave-bedolaga-telegram-bot/graphs/contributors)**

**Версия:** v2.5.2 | **Последнее обновление:** 2024

<div align="center">

### ⭐ Не забудь поставить звезду проекту!

[![Star History Chart](https://api.star-history.com/svg?repos=Fr1ngg/remnawave-bedolaga-telegram-bot&type=Date)](https://star-history.com/#Fr1ngg/remnawave-bedolaga-telegram-bot&Date)

</div>

</div>
