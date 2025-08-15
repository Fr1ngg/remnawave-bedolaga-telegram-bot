#!/bin/bash

# Цвета для вывода
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

BOT_DIR="/opt/bedolaga-bot"
COMPOSE_FILE="$BOT_DIR/docker-compose.yml"
ENV_FILE="$BOT_DIR/.env"
SERVICE_FILE="/etc/systemd/system/bedolaga-bot.service"

# Функция для логирования
log() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

# Проверка является ли пользователь root
check_root() {
    if [ "$EUID" -ne 0 ]; then
        error "Этот скрипт должен быть запущен от имени root (используйте sudo)"
        exit 1
    fi
}

# Проверка установки бота
check_installation() {
    if [ -d "$BOT_DIR" ] && [ -f "$COMPOSE_FILE" ] && [ -f "$ENV_FILE" ]; then
        return 0
    else
        return 1
    fi
}

# Обновление системы
update_system() {
    log "Обновление системы Ubuntu..."
    apt update && apt upgrade -y
    log "Система обновлена успешно"
}

# Установка Docker
install_docker() {
    log "Установка Docker..."
    
    # Удаление старых версий
    apt remove -y docker docker-engine docker.io containerd runc
    
    # Установка зависимостей
    apt install -y apt-transport-https ca-certificates curl gnupg lsb-release nano
    
    # Добавление GPG ключа Docker
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
    
    # Добавление репозитория
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
    
    # Установка Docker
    apt update
    apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
    
    # Запуск Docker
    systemctl enable docker
    systemctl start docker
    
    log "Docker установлен успешно"
}

# Проверка и установка nano
ensure_nano() {
    if ! command -v nano &> /dev/null; then
        log "Установка nano..."
        apt update
        apt install -y nano
        log "Nano установлен успешно"
    fi
}

# Создание папки и структуры проекта
create_project_structure() {
    log "Создание структуры проекта..."
    
    mkdir -p "$BOT_DIR"
    mkdir -p "$BOT_DIR/logs"
    mkdir -p "$BOT_DIR/data"
    
    log "Структура проекта создана в $BOT_DIR"
}

create_docker_compose() {
    log "Создание docker-compose.yml..."
    
    echo "Выберите конфигурацию установки:"
    echo "1) Только бот (панель RemnaWave на другом сервере)"
    echo "2) Панель + бот на одном сервере (рекомендуется)"
    echo "3) Расширенная - с Redis и Nginx"
    
    while true; do
        read -rp "Ваш выбор (1-3): " choice
        case "$choice" in
            1)
                create_standalone_compose
                break
                ;;
            2)
                create_panel_bot_compose
                break
                ;;
            3)
                create_full_compose
                break
                ;;
            *)
                error "Неверный выбор. Попробуйте снова."
                ;;
        esac
    done
}

# Создание конфигурации только для бота (внешняя панель)
create_standalone_compose() {
    cat > "$COMPOSE_FILE" << 'EOF'
services:
  # PostgreSQL Database
  postgres:
    image: postgres:15-alpine
    container_name: remnawave_bot_db
    restart: unless-stopped
    environment:
      POSTGRES_DB: remnawave_bot
      POSTGRES_USER: remnawave_user
      POSTGRES_PASSWORD: secure_password_123
      POSTGRES_INITDB_ARGS: "--encoding=UTF-8 --lc-collate=C --lc-ctype=C"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U remnawave_user -d remnawave_bot"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s

  # RemnaWave Bot
  bot:
    image: fr1ngg/remnawave-bedolaga-telegram-bot:latest
    container_name: remnawave_bot
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://remnawave_user:secure_password_123@postgres:5432/remnawave_bot
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "python -c 'print(\"Bot is running\")'"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

volumes:
  postgres_data:
    driver: local

networks:
  bot_network:
    driver: bridge
EOF

    log "Конфигурация только для бота создана"
    export COMPOSE_TYPE="standalone"
}

# Создание конфигурации панель + бот
create_panel_bot_compose() {
    cat > "$COMPOSE_FILE" << 'EOF'
services:
  # PostgreSQL Database
  postgres:
    image: postgres:15-alpine
    container_name: remnawave_bot_db
    restart: unless-stopped
    environment:
      POSTGRES_DB: remnawave_bot
      POSTGRES_USER: remnawave_user
      POSTGRES_PASSWORD: secure_password_123
      POSTGRES_INITDB_ARGS: "--encoding=UTF-8 --lc-collate=C --lc-ctype=C"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - remnawave-network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U remnawave_user -d remnawave_bot"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s

  # RemnaWave Bot
  bot:
    image: fr1ngg/remnawave-bedolaga-telegram-bot:latest
    container_name: remnawave_bot
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://remnawave_user:secure_password_123@postgres:5432/remnawave_bot
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
    networks:
      - remnawave-network
    healthcheck:
      test: ["CMD-SHELL", "python -c 'print(\"Bot is running\")'"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

volumes:
  postgres_data:
    driver: local

networks:
  remnawave-network:
    name: remnawave-network
    external: true
EOF

    log "Конфигурация панель + бот на одном сервере создана"
    log "ВАЖНО: Убедитесь что панель RemnaWave уже установлена и создала сеть remnawave-network"
    export COMPOSE_TYPE="panel_bot"
}

create_full_compose() {
    cat > "$COMPOSE_FILE" << 'EOF'
services:
  # PostgreSQL Database
  postgres:
    image: postgres:15-alpine
    container_name: remnawave_bot_db
    restart: unless-stopped
    environment:
      POSTGRES_DB: remnawave_bot
      POSTGRES_USER: remnawave_user
      POSTGRES_PASSWORD: secure_password_123
      POSTGRES_INITDB_ARGS: "--encoding=UTF-8 --lc-collate=C --lc-ctype=C"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    ports:
      - "5432:5432"
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U remnawave_user -d remnawave_bot"]
      interval: 15s
      timeout: 10s
      retries: 5
      start_period: 30s

  # RemnaWave Bot
  bot:
    image: fr1ngg/remnawave-bedolaga-telegram-bot:latest
    container_name: remnawave_bot
    restart: unless-stopped
    depends_on:
      postgres:
        condition: service_healthy
    env_file:
      - .env
    environment:
      DATABASE_URL: postgresql+asyncpg://remnawave_user:secure_password_123@postgres:5432/remnawave_bot
    volumes:
      - ./logs:/app/logs
      - ./data:/app/data
    networks:
      - bot_network
    healthcheck:
      test: ["CMD-SHELL", "python -c 'print(\"Bot is running\")'"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s

  # Redis (для кэширования и улучшения производительности)
  redis:
    image: redis:7-alpine
    container_name: remnawave_bot_redis
    restart: unless-stopped
    command: redis-server --appendonly yes --requirepass redis_password_123
    volumes:
      - redis_data:/data
    ports:
      - "6379:6379"
    networks:
      - bot_network
    healthcheck:
      test: ["CMD", "redis-cli", "--raw", "incr", "ping"]
      interval: 10s
      timeout: 3s
      retries: 5
    profiles:
      - with-redis

  # Nginx (для статических файлов или веб-интерфейса)
  nginx:
    image: nginx:alpine
    container_name: remnawave_bot_nginx
    restart: unless-stopped
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
      - ./static:/usr/share/nginx/html:ro
    networks:
      - bot_network
    depends_on:
      - bot
    profiles:
      - with-nginx

volumes:
  postgres_data:
    driver: local
  redis_data:
    driver: local

networks:
  bot_network:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.0.0/16
EOF

    log "Расширенная конфигурация Docker Compose создана"
    export COMPOSE_TYPE="full"
}

# Создание .env файла
create_env_file() {
    log "Настройка .env файла..."
    
    # Основные настройки бота
    read -p "Введите BOT_TOKEN: " BOT_TOKEN
    read -p "Введите BOT_USERNAME (без @): " BOT_USERNAME
    read -p "Введите ADMIN_IDS (через запятую): " ADMIN_IDS
    
    # Настройки RemnaWave в зависимости от типа установки
    if [ "$COMPOSE_TYPE" = "panel_bot" ]; then
        log "Настройка для панель + бот на одном сервере"
        REMNAWAVE_URL="http://remnawave:3000"
        echo "URL панели будет: $REMNAWAVE_URL (внутренний адрес контейнера)"
    else
        read -p "Введите REMNAWAVE_URL (например: https://your-panel.com): " REMNAWAVE_URL
    fi
    
    read -p "Введите REMNAWAVE_TOKEN: " REMNAWAVE_TOKEN
    read -p "Введите SUBSCRIPTION_BASE_URL (например: https://sub.your-domain.com): " SUBSCRIPTION_BASE_URL
    
    # Настройки триала
    while true; do
        read -p "Включить триал? (y/n): " trial_enabled
        case $trial_enabled in
            [Yy]*)
                TRIAL_ENABLED="true"
                read -p "Введите TRIAL_DURATION_DAYS: " TRIAL_DURATION_DAYS
                read -p "Введите TRIAL_TRAFFIC_GB: " TRIAL_TRAFFIC_GB
                read -p "Введите TRIAL_SQUAD_UUID: " TRIAL_SQUAD_UUID
                break
                ;;
            [Nn]*)
                TRIAL_ENABLED="false"
                TRIAL_DURATION_DAYS=""
                TRIAL_TRAFFIC_GB=""
                TRIAL_SQUAD_UUID=""
                break
                ;;
            *)
                error "Пожалуйста, ответьте y или n."
                ;;
        esac
    done
    
    # Настройки реферальной системы
    read -p "Введите REFERRAL_FIRST_REWARD (сумму с .0 на конце): " REFERRAL_FIRST_REWARD
    read -p "Введите REFERRAL_REFERRED_BONUS (сумму с .0 на конце): " REFERRAL_REFERRED_BONUS
    read -p "Введите REFERRAL_THRESHOLD (сумму с .0 на конце): " REFERRAL_THRESHOLD
    read -p "Введите REFERRAL_PERCENTAGE (с 0. в начале): " REFERRAL_PERCENTAGE
    
    # Настройки оплаты звездами Telegram
    echo ""
    echo -e "${YELLOW}=== Настройка оплаты звездами Telegram ===${NC}"
    while true; do
        read -p "Включить оплату звездами Telegram? (y/n): " stars_enabled
        case $stars_enabled in
            [Yy]*)
                STARS_ENABLED="true"
                echo ""
                echo -e "${BLUE}Настройка курсов обмена звезд на рубли:${NC}"
                echo -e "${YELLOW}Введите курс обмена для каждого пакета звезд${NC}"
                echo -e "${YELLOW}(например, если 100 звезд = 150 рублей, введите 150)${NC}"
                echo ""
                
                read -p "Курс для 100 звезд (в рублях): " STARS_100_RATE
                read -p "Курс для 150 звезд (в рублях): " STARS_150_RATE
                read -p "Курс для 250 звезд (в рублях): " STARS_250_RATE
                read -p "Курс для 350 звезд (в рублях): " STARS_350_RATE
                read -p "Курс для 500 звезд (в рублях): " STARS_500_RATE
                break
                ;;
            [Nn]*)
                STARS_ENABLED="false"
                STARS_100_RATE=""
                STARS_150_RATE=""
                STARS_250_RATE=""
                STARS_350_RATE=""
                STARS_500_RATE=""
                break
                ;;
            *)
                error "Пожалуйста, ответьте y или n."
                ;;
        esac
    done
    
    # Настройки мониторинга
    read -p "Введите DELETE_EXPIRED_TRIAL_DAYS: " DELETE_EXPIRED_TRIAL_DAYS
    read -p "Введите DELETE_EXPIRED_REGULAR_DAYS: " DELETE_EXPIRED_REGULAR_DAYS
    
    # Создание .env файла
    cat > "$ENV_FILE" << EOF
# Bot Configuration
BOT_TOKEN=$BOT_TOKEN
BOT_USERNAME=$BOT_USERNAME

# RemnaWave API Configuration
REMNAWAVE_URL=$REMNAWAVE_URL
REMNAWAVE_TOKEN=$REMNAWAVE_TOKEN
SUBSCRIPTION_BASE_URL=$SUBSCRIPTION_BASE_URL

# Admin Configuration
ADMIN_IDS=$ADMIN_IDS
SUPPORT_USERNAME=support

# Trial Configuration
TRIAL_ENABLED=$TRIAL_ENABLED
EOF

    if [ "$TRIAL_ENABLED" = "true" ]; then
        cat >> "$ENV_FILE" << EOF
TRIAL_DURATION_DAYS=$TRIAL_DURATION_DAYS
TRIAL_TRAFFIC_GB=$TRIAL_TRAFFIC_GB
TRIAL_SQUAD_UUID=$TRIAL_SQUAD_UUID
EOF
    fi

    cat >> "$ENV_FILE" << EOF

# Referral Configuration
REFERRAL_FIRST_REWARD=$REFERRAL_FIRST_REWARD
REFERRAL_REFERRED_BONUS=$REFERRAL_REFERRED_BONUS
REFERRAL_THRESHOLD=$REFERRAL_THRESHOLD
REFERRAL_PERCENTAGE=$REFERRAL_PERCENTAGE

# Telegram Stars Configuration
STARS_ENABLED=$STARS_ENABLED
EOF

    # Добавляем курсы звезд только если включена оплата звездами
    if [ "$STARS_ENABLED" = "true" ]; then
        cat >> "$ENV_FILE" << EOF
STARS_100_RATE=$STARS_100_RATE
STARS_150_RATE=$STARS_150_RATE
STARS_250_RATE=$STARS_250_RATE
STARS_350_RATE=$STARS_350_RATE
STARS_500_RATE=$STARS_500_RATE
EOF
    fi

    cat >> "$ENV_FILE" << EOF

# Monitor Configuration
MONITOR_ENABLED=true
MONITOR_CHECK_INTERVAL=21600
MONITOR_DAILY_CHECK_HOUR=12
MONITOR_WARNING_DAYS=2
DELETE_EXPIRED_TRIAL_DAYS=$DELETE_EXPIRED_TRIAL_DAYS
DELETE_EXPIRED_REGULAR_DAYS=$DELETE_EXPIRED_REGULAR_DAYS
AUTO_DELETE_ENABLED=true

# Lucky Game Configuration
LUCKY_GAME_ENABLED=true
LUCKY_GAME_REWARD=50.0
LUCKY_GAME_NUMBERS=30
LUCKY_GAME_WINNING_COUNT=5
EOF

    log ".env файл создан успешно"
    
    # Показываем специальные инструкции для панель + бот
    if [ "$COMPOSE_TYPE" = "panel_bot" ]; then
        echo ""
        echo -e "${YELLOW}=== ВАЖНЫЕ ИНСТРУКЦИИ ДЛЯ ПАНЕЛЬ + БОТ ===${NC}"
        echo -e "${GREEN}1. Убедитесь что панель RemnaWave уже запущена${NC}"
        echo -e "${GREEN}2. URL панели установлен как: $REMNAWAVE_URL${NC}"
        echo -e "${GREEN}3. Бот будет подключаться к панели через внутреннюю Docker сеть${NC}"
        echo -e "${YELLOW}4. Если панель не запущена, сначала запустите её!${NC}"
        echo ""
    fi
}

# Создание службы systemd
create_service() {
    while true; do
        read -p "Создать службу для запуска бота? (y/n): " create_service_choice
        case $create_service_choice in
            [Yy]*)
                log "Создание службы systemd..."
                
                cat > "$SERVICE_FILE" << EOF
[Unit]
Description=RemnaWave Bedolaga Bot
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$BOT_DIR
ExecStart=/usr/bin/docker compose up -d
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
                
                systemctl daemon-reload
                systemctl enable bedolaga-bot.service
                log "Служба bedolaga-bot создана и включена"
                break
                ;;
            [Nn]*)
                log "Служба не будет создана"
                break
                ;;
            *)
                error "Пожалуйста, ответьте y или n."
                ;;
        esac
    done
}

# Проверка статуса бота
check_bot_status() {
    if docker compose -f "$COMPOSE_FILE" ps --services --filter "status=running" | grep -q "bot"; then
        return 0  # Запущен
    else
        return 1  # Не запущен
    fi
}

# Проверка подключения к RemnaWave API
check_remnawave_connection() {
    if [ -f "$ENV_FILE" ]; then
        source "$ENV_FILE"
        if [ ! -z "$REMNAWAVE_URL" ]; then
            # Для внутренних URL (панель+бот) проверка API отключена
            # так как панель может блокировать HTTP запросы через ProxyCheckMiddleware
            if [[ "$REMNAWAVE_URL" == *"remnawave:3000"* ]]; then
                # Для локальной установки просто проверяем что бот запущен
                # Если бот работает - значит скорее всего API тоже доступен
                if docker compose -f "$COMPOSE_FILE" ps bot | grep -q "Up"; then
                    return 0  # Считаем что подключен если бот запущен
                else
                    return 1
                fi
            else
                # Внешний URL - проверяем напрямую
                if curl -s --connect-timeout 5 "$REMNAWAVE_URL/api/system/stats" > /dev/null 2>&1; then
                    return 0
                else
                    return 1
                fi
            fi
        else
            return 1
        fi
    else
        return 1
    fi
}

# Запуск бота
start_bot() {
    log "Запуск бота..."
    cd "$BOT_DIR"
    
    # Проверяем тип установки
    if grep -q "remnawave-network" "$COMPOSE_FILE"; then
        log "Обнаружена конфигурация панель + бот"
        
        # Проверяем существование сети
        if ! docker network ls | grep -q "remnawave-network"; then
            error "Сеть remnawave-network не найдена!"
            error "Убедитесь что панель RemnaWave запущена и создала сеть"
            echo ""
            echo "Для проверки выполните:"
            echo "  docker network ls | grep remnawave"
            echo ""
            echo "Если сети нет, сначала запустите панель RemnaWave"
            return 1
        fi
        
        log "Сеть remnawave-network найдена ✓"
    fi
    
    docker compose up -d
    log "Бот запущен"
    
    # Ждем немного и проверяем статус
    sleep 5
    if check_bot_status; then
        log "✅ Бот успешно запущен и работает"
    else
        warn "⚠️ Бот запущен но возможны проблемы. Проверьте логи: docker compose logs bot"
    fi
}

# Остановка бота
stop_bot() {
    log "Остановка бота..."
    cd "$BOT_DIR"
    docker compose down
    log "Бот остановлен"
}

# Перезапуск бота
restart_bot() {
    log "Перезапуск бота..."
    cd "$BOT_DIR"
    docker compose restart
    log "Бот перезапущен"
}

# Обновление бота
update_bot() {
    log "Обновление бота..."
    cd "$BOT_DIR"
    docker compose down
    docker compose pull bot
    docker compose up -d
    log "Бот обновлен"
}

# Просмотр логов
view_logs() {
    cd "$BOT_DIR"
    docker compose logs bot
}

# Просмотр логов в реальном времени
view_live_logs() {
    cd "$BOT_DIR"
    docker compose logs -f bot
}

# Создание резервной копии базы данных
backup_database() {
    log "Создание резервной копии базы данных..."
    cd "$BOT_DIR"
    
    # Проверяем, запущен ли контейнер postgres
    if ! docker compose ps postgres | grep -q "Up"; then
        log "Контейнер PostgreSQL не запущен. Запускаем PostgreSQL..."
        docker compose up -d postgres
        
        # Ждем готовности базы данных
        log "Ожидание готовности базы данных..."
        for i in {1..30}; do
            if docker compose exec postgres pg_isready -U remnawave_user -d remnawave_bot &>/dev/null; then
                log "PostgreSQL готов к работе"
                break
            fi
            if [ $i -eq 30 ]; then
                error "PostgreSQL не запустился в течение 60 секунд"
                return 1
            fi
            sleep 2
            echo -n "."
        done
        echo ""
    fi
    
    # Проверяем доступность базы данных
    log "Проверка подключения к базе данных..."
    if ! docker compose exec postgres pg_isready -U remnawave_user -d remnawave_bot &>/dev/null; then
        error "База данных недоступна. Проверьте логи: docker compose logs postgres"
        return 1
    fi
    
    BACKUP_FILE="$BOT_DIR/backup_$(date +%Y%m%d_%H%M%S).sql"
    
    log "Создание дампа базы данных..."
    
    # Используем docker compose exec без -T и с правильным выводом
    if docker compose exec postgres pg_dump -U remnawave_user -d remnawave_bot --verbose --no-owner --no-privileges > "$BACKUP_FILE" 2>/dev/null; then
        # Проверяем размер созданного файла
        if [ -s "$BACKUP_FILE" ]; then
            FILE_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
            log "Резервная копия создана успешно: $(basename "$BACKUP_FILE")"
            echo -e "${GREEN}Размер файла: $FILE_SIZE${NC}"
            echo -e "${GREEN}Путь: $BACKUP_FILE${NC}"
            
            # Показываем краткую информацию о содержимом
            LINES_COUNT=$(wc -l < "$BACKUP_FILE")
            echo -e "${BLUE}Количество строк в дампе: $LINES_COUNT${NC}"
        else
            error "Резервная копия создана, но файл пустой. Возможные причины:"
            echo "  - База данных пуста (бот еще не создал таблицы)"
            echo "  - Нет прав доступа к базе данных"
            echo "  - Неправильные параметры подключения"
            rm -f "$BACKUP_FILE"
            return 1
        fi
    else
        error "Ошибка при создании резервной копии"
        # Показываем подробную информацию об ошибке
        echo "Попытка диагностики проблемы..."
        docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "\dt" 2>&1 || {
            echo "Не удается подключиться к базе данных."
            echo "Проверьте логи контейнера: docker compose logs postgres"
        }
        rm -f "$BACKUP_FILE" 2>/dev/null
        return 1
    fi
}

# Восстановление базы данных
restore_database() {
    log "Восстановление базы данных из резервной копии"
    
    # Поиск файлов резервных копий
    BACKUP_FILES=($(find "$BOT_DIR" -name "backup_*.sql" -type f 2>/dev/null))
    
    if [ ${#BACKUP_FILES[@]} -eq 0 ]; then
        error "Файлы резервных копий не найдены в $BOT_DIR"
        echo "Поместите файл резервной копии (.sql) в папку $BOT_DIR"
        return 1
    fi
    
    echo -e "${YELLOW}Найденные резервные копии:${NC}"
    for i in "${!BACKUP_FILES[@]}"; do
        BACKUP_FILE="${BACKUP_FILES[$i]}"
        FILE_SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
        FILE_DATE=$(basename "$BACKUP_FILE" | sed 's/backup_\([0-9]\{8\}_[0-9]\{6\}\)\.sql/\1/' | sed 's/_/ /')
        echo "$((i+1))) $(basename "$BACKUP_FILE") - Размер: $FILE_SIZE - Дата: $FILE_DATE"
    done
    echo "$((${#BACKUP_FILES[@]}+1))) Указать путь к другому файлу"
    echo "0) Отмена"
    
    while true; do
        read -p "Выберите файл для восстановления: " choice
        
        if [ "$choice" = "0" ]; then
            log "Операция отменена"
            return 0
        elif [ "$choice" = "$((${#BACKUP_FILES[@]}+1))" ]; then
            read -p "Введите полный путь к файлу резервной копии: " SELECTED_BACKUP
            if [ ! -f "$SELECTED_BACKUP" ]; then
                error "Файл не найден: $SELECTED_BACKUP"
                continue
            fi
            break
        elif [ "$choice" -ge 1 ] && [ "$choice" -le "${#BACKUP_FILES[@]}" ]; then
            SELECTED_BACKUP="${BACKUP_FILES[$((choice-1))]}"
            break
        else
            error "Неверный выбор. Попробуйте снова."
        fi
    done
    
    warn "ВНИМАНИЕ! Это действие перезапишет текущую базу данных!"
    warn "Убедитесь, что у вас есть резервная копия текущих данных!"
    read -p "Продолжить восстановление? Введите 'YES' для подтверждения: " confirm
    
    if [ "$confirm" != "YES" ]; then
        log "Операция отменена"
        return 0
    fi
    
    cd "$BOT_DIR"
    
    # Проверяем, запущен ли контейнер postgres
    if ! docker compose ps postgres | grep -q "Up"; then
        log "Запуск контейнера PostgreSQL..."
        docker compose up -d postgres
        
        # Ждем готовности базы данных
        log "Ожидание готовности базы данных..."
        for i in {1..30}; do
            if docker compose exec postgres pg_isready -U remnawave_user -d remnawave_bot &>/dev/null; then
                break
            fi
            sleep 2
            echo -n "."
        done
        echo ""
    fi
    
    log "Очистка текущей базы данных..."
    if docker compose exec -T postgres psql -U remnawave_user -d remnawave_bot -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" &>/dev/null; then
        log "База данных очищена"
    else
        error "Ошибка при очистке базы данных"
        return 1
    fi
    
    log "Восстановление данных из файла: $(basename "$SELECTED_BACKUP")"
    if docker compose exec -T postgres psql -U remnawave_user -d remnawave_bot < "$SELECTED_BACKUP"; then
        log "База данных успешно восстановлена!"
        log "Перезапуск бота для применения изменений..."
        docker compose restart bot
        log "Восстановление завершено успешно"
    else
        error "Ошибка при восстановлении базы данных"
        return 1
    fi
}

# Диагностика базы данных
diagnose_database() {
    log "Диагностика состояния базы данных..."
    cd "$BOT_DIR"
    
    # Проверяем состояние контейнера
    echo -e "${YELLOW}Состояние контейнеров:${NC}"
    docker compose ps
    echo ""
    
    # Проверяем, запущен ли PostgreSQL, если нет - запускаем
    if ! docker compose ps postgres | grep -q "Up"; then
        log "PostgreSQL не запущен. Запускаем..."
        docker compose up -d postgres
        
        # Ждем готовности базы данных
        log "Ожидание готовности базы данных..."
        for i in {1..30}; do
            if docker compose exec postgres pg_isready -U remnawave_user -d remnawave_bot &>/dev/null; then
                log "PostgreSQL готов к работе"
                break
            fi
            if [ $i -eq 30 ]; then
                error "PostgreSQL не запустился в течение 60 секунд"
                return 1
            fi
            sleep 2
            echo -n "."
        done
        echo ""
    fi
    
    # Проверяем доступность PostgreSQL
    echo -e "${YELLOW}Проверка доступности PostgreSQL:${NC}"
    if docker compose exec postgres pg_isready -U remnawave_user -d remnawave_bot; then
        echo -e "${GREEN}✓ PostgreSQL доступен${NC}"
    else
        echo -e "${RED}✗ PostgreSQL недоступен${NC}"
        echo "Проверьте логи: docker compose logs postgres"
        return 1
    fi
    echo ""
    
    # Проверяем подключение к базе данных
    echo -e "${YELLOW}Информация о базе данных:${NC}"
    docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "
        SELECT 
            current_database() as database_name,
            current_user as current_user,
            version() as postgresql_version;
    " 2>/dev/null || {
        echo -e "${RED}Ошибка подключения к базе данных${NC}"
        return 1
    }
    echo ""
    
    # Показываем список таблиц
    echo -e "${YELLOW}Таблицы в базе данных:${NC}"
    TABLES_OUTPUT=$(docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "\dt" 2>/dev/null)
    if echo "$TABLES_OUTPUT" | grep -q "No relations found"; then
        echo -e "${YELLOW}База данных пуста - таблицы еще не созданы${NC}"
        echo -e "${BLUE}Это нормально если бот еще ни разу не запускался${NC}"
    elif [ -z "$TABLES_OUTPUT" ]; then
        echo -e "${YELLOW}Не удается получить список таблиц${NC}"
    else
        echo "$TABLES_OUTPUT"
    fi
    echo ""
    
    # Показываем размер базы данных
    echo -e "${YELLOW}Размер базы данных:${NC}"
    docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "
        SELECT 
            pg_database.datname,
            pg_size_pretty(pg_database_size(pg_database.datname)) AS size
        FROM pg_database 
        WHERE datname = 'remnawave_bot';
    " 2>/dev/null || {
        echo -e "${RED}Не удается получить размер базы данных${NC}"
    }
}

# Функция экстренного исправления базы данных
emergency_fix_database() {
    log "Экстренное исправление базы данных..."
    
    cd "$BOT_DIR"
    
    # Проверяем, запущен ли контейнер бота
    if ! docker compose ps bot | grep -q "Up"; then
        warn "Контейнер бота не запущен. Запускаем бота..."
        docker compose up -d bot
        
        # Ждем готовности бота
        log "Ожидание готовности бота..."
        for i in {1..60}; do
            if docker compose logs bot 2>/dev/null | grep -q "Bot started successfully\|Application startup complete\|Bot is running"; then
                log "Бот готов к работе"
                break
            fi
            if [ $i -eq 60 ]; then
                warn "Бот не запустился полностью, но попробуем выполнить исправление"
                break
            fi
            sleep 2
            echo -n "."
        done
        echo ""
    fi
    
    # Создаем скрипт исправления во временном файле
    EMERGENCY_SCRIPT="$BOT_DIR/emergency_fix.py"
    
    log "Создание скрипта экстренного исправления..."
    cat > "$EMERGENCY_SCRIPT" << 'EOF'
"""
Экстренное исправление проблемы с отображением подписок
Этот патч добавляет недостающие поля в таблицу user_subscriptions
"""

import asyncio
import sys
import os
from pathlib import Path

# Добавляем корневую директорию в путь
sys.path.insert(0, str(Path(__file__).parent))

try:
    from config import load_config
    from database import Database
except ImportError:
    # Если не можем импортировать, попробуем из app
    sys.path.insert(0, '/app')
    try:
        from config import load_config
        from database import Database
    except ImportError:
        print("❌ Не удается импортировать модули. Проверьте структуру проекта.")
        sys.exit(1)

from sqlalchemy import text
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def check_and_add_column(db, column_name, column_definition):
    """Проверяет и добавляет колонку в таблицу"""
    try:
        # Отдельная транзакция для проверки
        async with db.engine.begin() as conn:
            await conn.execute(text(f"SELECT {column_name} FROM user_subscriptions LIMIT 1"))
            logger.info(f"✅ Поле {column_name} уже существует")
            return True
    except Exception:
        # Отдельная транзакция для добавления колонки
        try:
            async with db.engine.begin() as conn:
                logger.info(f"➕ Добавляю поле {column_name}...")
                await conn.execute(text(f"""
                    ALTER TABLE user_subscriptions 
                    ADD COLUMN {column_name} {column_definition}
                """))
                logger.info(f"✅ Поле {column_name} добавлено")
                return True
        except Exception as e:
            logger.error(f"❌ Ошибка при добавлении {column_name}: {e}")
            return False

async def emergency_fix():
    """Экстренное исправление базы данных"""
    
    try:
        # Загружаем конфигурацию
        config = load_config()
        
        # Подключаемся к базе данных  
        db = Database(config.DATABASE_URL)
        
        logger.info("🔧 Выполняю экстренное исправление базы данных...")
        
        # Проверяем существование таблицы user_subscriptions
        try:
            async with db.engine.begin() as conn:
                result = await conn.execute(text("SELECT COUNT(*) FROM user_subscriptions"))
                count = result.scalar()
                logger.info(f"📊 Найдено {count} подписок в таблице user_subscriptions")
        except Exception as e:
            logger.error(f"❌ Таблица user_subscriptions не найдена: {e}")
            await db.close()
            return

        # Добавляем поля по одному в отдельных транзакциях
        success1 = await check_and_add_column(db, "auto_pay_enabled", "BOOLEAN DEFAULT FALSE")
        success2 = await check_and_add_column(db, "auto_pay_days_before", "INTEGER DEFAULT 3")
        
        # Финальная проверка в отдельной транзакции
        if success1 and success2:
            try:
                async with db.engine.begin() as conn:
                    result = await conn.execute(text("""
                        SELECT id, auto_pay_enabled, auto_pay_days_before 
                        FROM user_subscriptions LIMIT 1
                    """))
                    row = result.fetchone()
                    if row:
                        logger.info("✅ Все поля доступны для чтения")
                        logger.info(f"🔍 Пример записи: id={row[0]}, auto_pay_enabled={row[1]}, auto_pay_days_before={row[2]}")
                    else:
                        logger.info("✅ Все поля доступны, но таблица пуста")
                        
            except Exception as e:
                logger.error(f"❌ Поля все еще недоступны: {e}")
        else:
            logger.error("❌ Не удалось добавить все необходимые поля")
                
        await db.close()
        logger.info("🎉 Экстренное исправление завершено!")
        
    except Exception as e:
        logger.error(f"💥 Критическая ошибка: {e}")
        raise

if __name__ == "__main__":
    asyncio.run(emergency_fix())
EOF

    # Копируем скрипт в контейнер и запускаем
    log "Копирование скрипта в контейнер бота..."
    if docker compose exec bot test -d /app; then
        # Копируем скрипт в контейнер
        docker compose cp "$EMERGENCY_SCRIPT" bot:/app/emergency_fix.py
        
        log "Запуск экстренного исправления в контейнере бота..."
        if docker compose exec bot python emergency_fix.py; then
            log "✅ Экстренное исправление выполнено успешно!"
            
            # Перезапускаем бота для применения изменений
            log "Перезапуск бота для применения изменений..."
            docker compose restart bot
            log "✅ Бот перезапущен"
        else
            error "❌ Ошибка при выполнении экстренного исправления"
            echo "Проверьте логи бота: docker compose logs bot"
        fi
        
        # Удаляем временный скрипт из контейнера
        docker compose exec bot rm -f /app/emergency_fix.py 2>/dev/null || true
    else
        error "❌ Не удается найти директорию /app в контейнере бота"
        echo "Проверьте, что контейнер бота запущен правильно"
    fi
    
    # Удаляем временный скрипт с хоста
    rm -f "$EMERGENCY_SCRIPT"
}

# Альтернативный метод экстренного исправления через SQL
emergency_fix_database_sql() {
    log "Экстренное исправление базы данных через SQL..."
    
    cd "$BOT_DIR"
    
    # Проверяем, запущен ли контейнер postgres
    if ! docker compose ps postgres | grep -q "Up"; then
        log "Контейнер PostgreSQL не запущен. Запускаем PostgreSQL..."
        docker compose up -d postgres
        
        # Ждем готовности базы данных
        log "Ожидание готовности базы данных..."
        for i in {1..30}; do
            if docker compose exec postgres pg_isready -U remnawave_user -d remnawave_bot &>/dev/null; then
                log "PostgreSQL готов к работе"
                break
            fi
            if [ $i -eq 30 ]; then
                error "PostgreSQL не запустился в течение 60 секунд"
                return 1
            fi
            sleep 2
            echo -n "."
        done
        echo ""
    fi
    
    log "Проверка существования полей в таблице user_subscriptions..."
    
    # Проверяем auto_pay_enabled
    if docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "SELECT auto_pay_enabled FROM user_subscriptions LIMIT 1" &>/dev/null; then
        log "✅ Поле auto_pay_enabled уже существует"
    else
        log "➕ Добавление поля auto_pay_enabled..."
        if docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "ALTER TABLE user_subscriptions ADD COLUMN auto_pay_enabled BOOLEAN DEFAULT FALSE" &>/dev/null; then
            log "✅ Поле auto_pay_enabled добавлено"
        else
            error "❌ Ошибка при добавлении поля auto_pay_enabled"
            return 1
        fi
    fi
    
    # Проверяем auto_pay_days_before
    if docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "SELECT auto_pay_days_before FROM user_subscriptions LIMIT 1" &>/dev/null; then
        log "✅ Поле auto_pay_days_before уже существует"
    else
        log "➕ Добавление поля auto_pay_days_before..."
        if docker compose exec postgres psql -U remnawave_user -d remnawave_bot -c "ALTER TABLE user_subscriptions ADD COLUMN auto_pay_days_before INTEGER DEFAULT 3" &>/dev/null; then
            log "✅ Поле auto_pay_days_before добавлено"
        else
            error "❌ Ошибка при добавлении поля auto_pay_days_before"
            return 1
        fi
    fi
    
    log "✅ Экстренное исправление через SQL завершено!"
    
    # Перезапускаем бота если он запущен
    if docker compose ps bot | grep -q "Up"; then
        log "Перезапуск бота для применения изменений..."
        docker compose restart bot
        log "✅ Бот перезапущен"
    fi
}

# Редактирование .env файла
edit_env_file() {
    ensure_nano
    
    if [ ! -f "$ENV_FILE" ]; then
        error "Файл .env не найден: $ENV_FILE"
        return 1
    fi
    
    log "Открытие .env файла для редактирования..."
    log "После изменений перезапустите бота для применения настроек"
    
    # Создаем резервную копию .env файла
    cp "$ENV_FILE" "$ENV_FILE.backup.$(date +%Y%m%d_%H%M%S)"
    
    nano "$ENV_FILE"
    
    log "Редактирование завершено"
    echo -e "${YELLOW}Не забудьте перезапустить бота для применения изменений!${NC}"
}

# Удаление базы данных
remove_database() {
    warn "ВНИМАНИЕ! Это действие удалит всю базу данных!"
    warn "Все данные бота (пользователи, подписки, настройки) будут потеряны!"
    read -p "Вы уверены? Введите 'YES' для подтверждения: " confirm
    if [ "$confirm" = "YES" ]; then
        log "Удаление базы данных..."
        cd "$BOT_DIR"
        
        # Останавливаем все сервисы если они запущены
        if docker compose ps --services --filter "status=running" | grep -q "."; then
            log "Остановка всех контейнеров..."
            docker compose down
        fi
        
        # Удаляем volume с данными PostgreSQL
        log "Удаление volume с данными PostgreSQL..."
        VOLUME_NAME=$(docker compose config --volumes 2>/dev/null | grep postgres || echo "bedolaga-bot_postgres_data")
        
        # Пробуем удалить volume разными способами
        if docker volume ls | grep -q "$VOLUME_NAME"; then
            if docker volume rm "$VOLUME_NAME" 2>/dev/null; then
                log "Volume $VOLUME_NAME успешно удален"
            else
                log "Принудительное удаление volume..."
                docker volume rm "$VOLUME_NAME" --force 2>/dev/null || true
            fi
        fi
        
        # Также пробуем удалить стандартное имя volume
        docker volume rm "$(basename $BOT_DIR)_postgres_data" 2>/dev/null || true
        
        # Удаляем все неиспользуемые volumes связанные с проектом
        log "Очистка неиспользуемых volumes..."
        docker volume prune -f 2>/dev/null || true
        
        log "База данных удалена"
        log "При следующем запуске бота будет создана новая пустая база данных"
    else
        log "Операция отменена"
    fi
}

# Полное удаление бота
remove_bot() {
    warn "ВНИМАНИЕ! Это действие полностью удалит бота и все данные!"
    read -p "Вы уверены? Введите 'YES' для подтверждения: " confirm
    if [ "$confirm" = "YES" ]; then
        log "Удаление бота..."
        cd "$BOT_DIR"
        docker compose down -v
        systemctl disable bedolaga-bot.service 2>/dev/null || true
        rm -f "$SERVICE_FILE"
        rm -rf "$BOT_DIR"
        log "Бот полностью удален"
        exit 0
    else
        log "Операция отменена"
    fi
}

# Проверка статуса сети RemnaWave
check_remnawave_network() {
    if docker network ls | grep -q "remnawave-network"; then
        return 0  # Сеть существует
    else
        return 1  # Сеть не существует
    fi
}

# Главное меню
show_menu() {
    clear
    echo -e "${BLUE}=== RemnaWave Bedolaga Bot Management ===${NC}"
    echo ""
    
    # Определяем тип установки
    INSTALLATION_TYPE="Неизвестно"
    if [ -f "$COMPOSE_FILE" ]; then
        if grep -q "remnawave-network" "$COMPOSE_FILE" && grep -q "external: true" "$COMPOSE_FILE"; then
            INSTALLATION_TYPE="Панель + Бот на одном сервере"
        elif grep -q "bot_network" "$COMPOSE_FILE"; then
            INSTALLATION_TYPE="Только бот (внешняя панель)"
        fi
    fi
    
    echo -e "${YELLOW}Тип установки: ${NC}$INSTALLATION_TYPE"
    
    # Показать статус бота
    echo -e "${YELLOW}Статус бота:${NC}"
    if check_bot_status; then
        echo -e "🟢 Бот: ${GREEN}ЗАПУЩЕН${NC}"
    else
        echo -e "🔴 Бот: ${RED}ОСТАНОВЛЕН${NC}"
    fi
    
    # Показать статус сети (для панель + бот)
    if [[ "$INSTALLATION_TYPE" == *"Панель + Бот"* ]]; then
        echo -e "${YELLOW}Статус сети RemnaWave:${NC}"
        if check_remnawave_network; then
            echo -e "🟢 Сеть: ${GREEN}СОЗДАНА${NC}"
        else
            echo -e "🔴 Сеть: ${RED}НЕ НАЙДЕНА${NC} (запустите панель RemnaWave)"
        fi
    fi
    
    # Показать статус подключения к API
    echo -e "${YELLOW}Подключение к RemnaWave API:${NC}"
    if check_remnawave_connection; then
        echo -e "🟢 API: ${GREEN}ПОДКЛЮЧЕН${NC}"
    else
        echo -e "🔴 API: ${RED}НЕ ПОДКЛЮЧЕН${NC}"
    fi
    
    echo ""
    echo -e "${YELLOW}Доступные действия:${NC}"
    
    if check_bot_status; then
        # Бот запущен
        echo "1) Выключить бот"
        echo "2) Перезапустить бот"
        echo "3) Посмотреть логи в реальном времени"
        echo "4) Обновить бот"
        echo "5) Посмотреть логи"
        echo "6) Создать резервную копию БД"
        echo "7) Восстановить базу данных"
        echo "8) Редактировать .env файл"
        echo "9) Диагностика базы данных"
        echo "10) Экстренное исправление БД (Python)"
        echo "11) Экстренное исправление БД (SQL)"
        echo "12) Удалить базу данных"
        echo "13) Полностью удалить бота"
        echo "0) Выход"
        
        read -p "Выберите действие: " choice
        case $choice in
            1) stop_bot; read -p "Нажмите Enter для продолжения..."; ;;
            2) restart_bot; read -p "Нажмите Enter для продолжения..."; ;;
            3) view_live_logs; ;;
            4) update_bot; read -p "Нажмите Enter для продолжения..."; ;;
            5) view_logs; read -p "Нажмите Enter для продолжения..."; ;;
            6) backup_database; read -p "Нажмите Enter для продолжения..."; ;;
            7) restore_database; read -p "Нажмите Enter для продолжения..."; ;;
            8) edit_env_file; read -p "Нажмите Enter для продолжения..."; ;;
            9) diagnose_database; read -p "Нажмите Enter для продолжения..."; ;;
            10) emergency_fix_database; read -p "Нажмите Enter для продолжения..."; ;;
            11) emergency_fix_database_sql; read -p "Нажмите Enter для продолжения..."; ;;
            12) remove_database; read -p "Нажмите Enter для продолжения..."; ;;
            13) remove_bot; ;;
            0) exit 0; ;;
            *) error "Неверный выбор"; read -p "Нажмите Enter для продолжения..."; ;;
        esac
    else
        # Бот остановлен
        echo "1) Запустить бот"
        echo "2) Обновить бот"
        echo "3) Посмотреть логи"
        echo "4) Создать резервную копию БД"
        echo "5) Восстановить базу данных"
        echo "6) Редактировать .env файл"
        echo "7) Диагностика базы данных"
        echo "8) Экстренное исправление БД (Python)"
        echo "9) Экстренное исправление БД (SQL)"
        echo "10) Удалить базу данных"
        echo "11) Полностью удалить бота"
        echo "0) Выход"
        
        read -p "Выберите действие: " choice
        case $choice in
            1) start_bot; read -p "Нажмите Enter для продолжения..."; ;;
            2) update_bot; read -p "Нажмите Enter для продолжения..."; ;;
            3) view_logs; read -p "Нажмите Enter для продолжения..."; ;;
            4) backup_database; read -p "Нажмите Enter для продолжения..."; ;;
            5) restore_database; read -p "Нажмите Enter для продолжения..."; ;;
            6) edit_env_file; read -p "Нажмите Enter для продолжения..."; ;;
            7) diagnose_database; read -p "Нажмите Enter для продолжения..."; ;;
            8) emergency_fix_database; read -p "Нажмите Enter для продолжения..."; ;;
            9) emergency_fix_database_sql; read -p "Нажмите Enter для продолжения..."; ;;
            10) remove_database; read -p "Нажмите Enter для продолжения..."; ;;
            11) remove_bot; ;;
            0) exit 0; ;;
            *) error "Неверный выбор"; read -p "Нажмите Enter для продолжения..."; ;;
        esac
    fi
}

# Основная функция установки
install_bot() {
    log "Начало установки RemnaWave Bedolaga Bot"
    
    update_system
    install_docker
    create_project_structure
    create_docker_compose
    create_env_file
    create_service
    
    log "Установка завершена!"
    log "Бот установлен в: $BOT_DIR"
    
    if [ "$COMPOSE_TYPE" = "panel_bot" ]; then
        echo ""
        echo -e "${YELLOW}=== ВАЖНО ДЛЯ ПАНЕЛЬ + БОТ УСТАНОВКИ ===${NC}"
        echo -e "${GREEN}1. Перед запуском бота убедитесь что панель RemnaWave запущена${NC}"
        echo -e "${GREEN}2. Панель должна создать сеть 'remnawave-network'${NC}"
        echo -e "${GREEN}3. Проверить сеть: docker network ls | grep remnawave${NC}"
        echo -e "${YELLOW}4. Если сети нет - сначала запустите панель RemnaWave!${NC}"
        echo ""
    fi
    
    log "Для управления ботом используйте это меню или systemctl"
    
    read -p "Нажмите Enter для перехода в меню управления..."
}

# Главная функция
main() {
    check_root
    
    if check_installation; then
        log "Бот уже установлен. Переход в меню управления..."
        ensure_nano  # Убеждаемся что nano установлен
    else
        install_bot
    fi
    
    # Основной цикл меню
    while true; do
        show_menu
    done
}

# Запуск скрипта
main "$@"
