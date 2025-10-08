# 🚀 Remnawave Bedolaga Bot — Deploy Guide

<p align="center">
  <img src="./assets/logo2.svg" alt="Logo" width="220" />
</p>

## Требования
- Docker Engine + Docker Compose plugin
- Домен для админки: `ADMIN_DOMAIN`
- Открыты порты 80/443 (для Caddy)

## Структура
- Один `docker-compose.yml` — бот, PostgreSQL, Redis, Caddy и сборщик web‑admin
- `caddy/Caddyfile` — хост для админки
- `env.example` — шаблон переменных окружения

## Шаг 1. Подготовка
```bash
# Клонируйте репозиторий
git clone https://github.com/PEDZEO/remnawave-bedolaga-telegram-bot-web-admin
cd remnawave-bedolaga-telegram-bot-web-admin

# Подготовьте каталоги
mkdir -p ./logs ./data ./data/backups ./data/referral_qr
sudo chown -R 1000:1000 ./logs ./data

# Создайте .env из примера
cp env.example .env
```

## Шаг 2. Заполните .env
Минимально необходимо указать:
- BOT_TOKEN — токен бота от @BotFather
- REMNAWAVE_API_URL, REMNAWAVE_API_KEY (и при необходимости REMNAWAVE_SECRET_KEY)
- WEB_API_ENABLED=true
- WEB_API_ALLOWED_ORIGINS=https://ADMIN_DOMAIN
- WEB_API_DEFAULT_TOKEN — первичный ключ для входа в админку
- ADMIN_DOMAIN, CADDY_EMAIL
- WEBHOOK_URL=http(s)://SERVER_IP (опционально)

При необходимости скорректируйте БД/Redis (по умолчанию всё работает в Docker).

## Шаг 3. Сборка web‑admin
```bash
docker compose up -d web-admin-builder
```
Ожидаемый результат — артефакты сборки появятся в volume `web_admin_dist`.

## Шаг 4. Запуск всей инфраструктуры
```bash
docker compose up -d
```
Что поднимется:
- postgres, redis — БД и кеш
- bot — сам бот + встроенный Web API (8080)
- caddy — HTTPS reverse‑proxy и раздача админки

## Шаг 5. Настройка DNS
- `ADMIN_DOMAIN` укажите в DNS (A/AAAA на IP VPS)
- Убедитесь, что входящие 80/443 открыты
- Caddy автоматически выпустит TLS‑сертификаты

## Доступ
- Админка: https://ADMIN_DOMAIN
  - При первом входе введите `WEB_API_DEFAULT_TOKEN` как API‑ключ в UI
  - UI обращается к бэкенду по `/api` → `remnawave_bot:8080`

## Полезные команды
   ```bash
# Статус
docker compose ps

# Логи
docker compose logs -f bot

# Проверка health внутри контейнера
docker compose exec -T bot wget -qO- http://localhost:8081/health || true

# Перезапуск
docker compose restart

# Остановка
docker compose down
```

## Обновление
```bash
git pull
# Пересобрать web-admin при изменениях фронтенда
docker compose up -d web-admin-builder
# Обновить остальное
docker compose up -d
```

## Примечания
- CORS: `WEB_API_ALLOWED_ORIGINS` должен включать ваш `https://ADMIN_DOMAIN`.
