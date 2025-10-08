### Веб‑админка: авторизация, доступ и безопасный сброс

- Веб‑API поднимается через FastAPI/uvicorn (см. `app/webapi/server.py`). Настройки берутся из `.env` и `app/config.py`.
- Фронтенд (`web-admin`) общается с бэкендом через `/auth/*` и использует JWT.

### Авторизация и регистрация
- Первый вход:
  - Если в БД нет ни одного администратора, доступна только регистрация.
  - Как только админ создан, регистрация блокируется на бэкенде и скрывается на фронтенде.
- Эндпоинты:
  - `POST /auth/register` — регистрация единственного администратора. После первого админа вернёт 403.
  - `POST /auth/login` — вход, возвращает `{ access_token }`.
  - `GET /auth/me` — профиль админа (требуется `Authorization: Bearer <token>`).
  - `GET /auth/can-register` — `{ can_register: boolean }` для UI.

Фронтенд‑поведение:
- При загрузке страницы авторизации фронт запрашивает `/auth/can-register`.
  - `true`: показывается форма регистрации (по умолчанию), можно переключиться на вход.
  - `false`: доступен только вход, ссылки на регистрацию скрыты.

### Переменные окружения (ключевые)
- `WEB_API_ENABLED=true`
- `WEB_API_HOST`, `WEB_API_PORT` — где слушает API
- CORS: `WEB_API_ALLOWED_ORIGINS` (по умолчанию “*”)
- `ADMIN_RESET_TOKEN` — секрет для сброса админа (обязательно для CLI/HTTP сброса)
- `ADMIN_RESET_IP_WHITELIST` — список IP через запятую для HTTP‑сброса (опционально, но рекомендуется)

Пример `.env` (фрагмент):
```env
WEB_API_ENABLED=true
WEB_API_HOST=0.0.0.0
WEB_API_PORT=8080
WEB_API_ALLOWED_ORIGINS=http://localhost:5173

ADMIN_RESET_TOKEN=super-secret-long-random
ADMIN_RESET_IP_WHITELIST=127.0.0.1,10.0.0.5
```

### Сброс администратора (безопасно)

1) CLI (предпочтительно, без публичного HTTP)
- Подготовка: на сервере задать `ADMIN_RESET_TOKEN` в окружении/compose/systemd.
- Команды:
```bash
# Обновить (или создать, если отсутствует) единственного админа
python -m app.management.reset_admin --username admin --password "StrongPass123" --token "super-secret-long-random"

# Полный пересоздание (удаляет всех админов и создаёт нового)
python -m app.management.reset_admin --mode recreate --force --username admin --password "StrongPass123" --token "super-secret-long-random"
```

2) HTTP (только если нужен удалённый сброс, ограничьте IP и храните токен в секрете)
- Эндпоинт: `POST /auth/reset-admin`
- Заголовок: `X-Admin-Reset-Token: <ADMIN_RESET_TOKEN>`
- Тело (JSON):
```json
{ "username": "admin", "password": "StrongPass123" }
```
- Проверяется токен и при наличии `ADMIN_RESET_IP_WHITELIST` — IP клиента. Выполняется безопасное пересоздание единственного админа.

### Запуск
- Бэкенд: убедитесь, что переменные окружения заданы; поднимите веб‑API (через ваш процесс‑менеджер/uvicorn).
- Фронтенд:
```bash
cd web-admin
npm install
npm run dev
```
- Откройте UI; при первом запуске будет доступна регистрация, далее — только вход.

Примечание: при использовании HTTP‑сброса рекомендуется ограничить доступ по IP и/или вообще отключить эндпоинт, оставив только CLI‑вариант.


