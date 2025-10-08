#!/usr/bin/env bash
set -euo pipefail

canonicalize_path() {
  local input_path=${1:-}
  if [[ -z "$input_path" ]]; then
    return 1
  fi
  if command -v realpath >/dev/null 2>&1; then
    realpath -m "$input_path"
    return 0
  fi
  if command -v python3 >/dev/null 2>&1; then
    python3 - "$input_path" <<'PY'
import os
import sys
path = os.path.expanduser(sys.argv[1])
print(os.path.realpath(path))
PY
    return 0
  fi
  local dir_part
  local base_part
  dir_part=$(dirname -- "$input_path") || dir_part="."
  base_part=$(basename -- "$input_path") || base_part="$input_path"
  local dir_resolved
  if dir_resolved=$(cd "$dir_part" 2>/dev/null && pwd); then
    printf '%s/%s\n' "$dir_resolved" "$base_part"
    return 0
  fi
  printf '%s\n' "$input_path"
}

SCRIPT_PATH=$(canonicalize_path "${BASH_SOURCE[0]}")
SCRIPT_DIR=$(cd "$(dirname "$SCRIPT_PATH")" && pwd)
STATE_FILE="$SCRIPT_DIR/.bot_install_state"
BACKUP_DIR="$SCRIPT_DIR/backups"

save_state() {
  local state_dir
  state_dir=$(dirname -- "$STATE_FILE")
  mkdir -p "$state_dir"
  local tmp_file
  if ! tmp_file=$(mktemp "$state_dir/.bot_install_state.XXXXXX" 2>/dev/null); then
    tmp_file="$STATE_FILE.tmp.$$"
  fi
  {
    printf 'INSTALL_PATH=%q\n' "$INSTALL_PATH"
  } >"$tmp_file"
  chmod 600 "$tmp_file"
  mv "$tmp_file" "$STATE_FILE"
}

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m'
BOLD='\033[1m'

CHECK="✓"
CROSS="✗"
ARROW="➜"
STAR="★"
GEAR="⚙"

print_header() {
  echo -e "\n${CYAN}${BOLD}╔════════════════════════════════════════════════════════════╗${NC}"
  echo -e "${CYAN}${BOLD}║${NC}  ${WHITE}${BOLD}$1${NC}${CYAN}${BOLD}║${NC}"
  echo -e "${CYAN}${BOLD}╚════════════════════════════════════════════════════════════╝${NC}\n"
}

print_section() {
  echo -e "\n${BLUE}${BOLD}${ARROW} $1${NC}"
  echo -e "${BLUE}─────────────────────────────────────────────────────${NC}"
}

print_success() {
  echo -e "${GREEN}${CHECK} $1${NC}"
}

print_error() {
  echo -e "${RED}${CROSS} $1${NC}" >&2
}

print_warning() {
  echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
  echo -e "${CYAN}ℹ $1${NC}"
}

print_status() {
  local status=$1
  local text=$2
  if [[ "$status" == "running" ]]; then
    echo -e "${GREEN}● ${text}${NC}"
  elif [[ "$status" == "stopped" ]]; then
    echo -e "${RED}● ${text}${NC}"
  else
    echo -e "${YELLOW}● ${text}${NC}"
  fi
}

initialize_state() {
  local reason=${1:-missing}
  case "$reason" in
    missing)
      print_warning "Файл состояния установки не найден. Выполняем начальную настройку."
      ;;
    unreadable)
      print_warning "Не удалось прочитать файл состояния $STATE_FILE. Требуется повторная настройка."
      ;;
    invalid)
      print_warning "Файл состояния $STATE_FILE повреждён или не содержит путь установки."
      ;;
    *)
      print_warning "$reason"
      ;;
  esac
  local default_path
  default_path=${INSTALL_PATH:-$SCRIPT_DIR}
  local install_path_input=""
  if [[ -t 0 ]]; then
    read -rp "Укажите путь установки [${default_path}]: " install_path_input
  elif read -r -t 1 install_path_input; then
    print_info "Путь установки получен из стандартного ввода"
  else
    print_info "Используем путь по умолчанию: ${default_path}"
  fi
  install_path_input=${install_path_input:-$default_path}
  local resolved_path
  resolved_path=$(canonicalize_path "$install_path_input") || resolved_path="$install_path_input"
  INSTALL_PATH="$resolved_path"
  save_state
  print_success "Путь установки сохранён: $INSTALL_PATH"
}

load_state() {
  if [[ -f "$STATE_FILE" ]]; then
    if source "$STATE_FILE" 2>/dev/null; then
      if [[ -n "${INSTALL_PATH:-}" ]]; then
        local resolved_path
        resolved_path=$(canonicalize_path "$INSTALL_PATH") || resolved_path="$INSTALL_PATH"
        INSTALL_PATH="$resolved_path"
        print_info "Используем сохранённый путь установки: $INSTALL_PATH"
      else
        initialize_state invalid
      fi
    else
      initialize_state unreadable
    fi
  else
    initialize_state missing
  fi
  if [[ ! -f "$INSTALL_PATH/.env" ]]; then
    print_warning ".env файл не найден!"
    read -rp "Выполнить первичную настройку .env? [Y/n]: " setup_env_confirm
    if [[ "${setup_env_confirm,,}" != "n" ]]; then
      setup_env
    else
      print_error "Бот не может работать без .env файла!"
      print_info "Вы можете настроить его позже через пункт меню [10]"
    fi
  fi
  BACKUP_DIR="$INSTALL_PATH/backups"
  mkdir -p "$BACKUP_DIR"
}

check_env_exists() {
  [[ -f "$INSTALL_PATH/.env" ]]
}

setup_env() {
  print_header "ПЕРВИЧНАЯ НАСТРОЙКА КОНФИГУРАЦИИ (.env)"
  local env_file="$INSTALL_PATH/.env"
  if [[ -f "$env_file" ]]; then
    cp "$env_file" "$env_file.backup.$(date +%Y%m%d_%H%M%S)"
    print_info "Создана резервная копия существующего .env"
  fi
  print_section "Обязательные параметры"
  local bot_token=""
  while [[ -z "$bot_token" ]]; do
    read -rp "Введите токен бота (BOT_TOKEN): " bot_token
    if [[ -z "$bot_token" ]]; then
      print_error "Токен бота обязателен!"
    fi
  done
  local admin_ids=""
  while [[ -z "$admin_ids" ]]; do
    read -rp "Введите ID администраторов через запятую (ADMIN_IDS): " admin_ids
    if [[ -z "$admin_ids" ]]; then
      print_error "Хотя бы один ID администратора обязателен!"
    fi
  done
  local web_api_token=""
  read -rp "Введите токен для Web API (WEB_API_DEFAULT_TOKEN, Enter для автогенерации): " web_api_token
  if [[ -z "$web_api_token" ]]; then
    print_warning "Токен Web API не указан, будет сгенерирован случайный"
    web_api_token=$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 64)
    print_success "Сгенерирован токен: ${web_api_token:0:16}..."
  fi
  local remnawave_url=""
  while [[ -z "$remnawave_url" ]]; do
    read -rp "Введите URL API Remnawave (REMNAWAVE_API_URL): " remnawave_url
    if [[ -z "$remnawave_url" ]]; then
      print_error "URL API Remnawave обязателен!"
    fi
  done
  local remnawave_key=""
  while [[ -z "$remnawave_key" ]]; do
    read -rp "Введите ключ API Remnawave (REMNAWAVE_API_KEY): " remnawave_key
    if [[ -z "$remnawave_key" ]]; then
      print_error "Ключ API Remnawave обязателен!"
    fi
  done
  print_section "Дополнительные параметры авторизации"
  echo -e "${CYAN}[1]${NC} API Key (по умолчанию)"
  echo -e "${CYAN}[2]${NC} Basic Auth"
  echo ""
  read -rp "Выберите тип авторизации [1]: " auth_choice
  auth_choice=${auth_choice:-1}
  local auth_type="api_key"
  local remnawave_username=""
  local remnawave_password=""
  local remnawave_secret=""
  if [[ "$auth_choice" == "2" ]]; then
    auth_type="basic_auth"
    read -rp "Введите имя пользователя для Basic Auth (REMNAWAVE_USERNAME): " remnawave_username
    read -rsp "Введите пароль для Basic Auth (REMNAWAVE_PASSWORD): " remnawave_password
    echo ""
  fi
  echo ""
  read -rp "Используете панель, установленную скриптом eGames? [y/N]: " use_egames
  if [[ "${use_egames,,}" == "y" ]]; then
    read -rp "Введите секретный ключ в формате XXXXXXX:DDDDDDDD (REMNAWAVE_SECRET_KEY): " remnawave_secret
  fi
  local postgres_password=""
  read -rp "Введите пароль для PostgreSQL (Enter для генерации): " postgres_password
  if [[ -z "$postgres_password" ]]; then
    postgres_password=$(openssl rand -base64 24 2>/dev/null || head -c 24 /dev/urandom | base64 | tr -dc 'a-zA-Z0-9' | head -c 32)
    print_success "Сгенерирован пароль PostgreSQL"
  fi
  print_section "Создание .env файла"
  cat > "$env_file" <<EOF
BOT_TOKEN=$bot_token
ADMIN_IDS=$admin_ids
WEB_API_DEFAULT_TOKEN=$web_api_token
REMNAWAVE_API_URL=$remnawave_url
REMNAWAVE_API_KEY=$remnawave_key
REMNAWAVE_AUTH_TYPE=$auth_type
EOF
  if [[ "$auth_type" == "basic_auth" ]]; then
    cat >> "$env_file" <<EOF
REMNAWAVE_USERNAME=$remnawave_username
REMNAWAVE_PASSWORD=$remnawave_password
EOF
  fi
  if [[ -n "$remnawave_secret" ]]; then
    cat >> "$env_file" <<EOF
REMNAWAVE_SECRET_KEY=$remnawave_secret
EOF
  fi
  cat >> "$env_file" <<EOF
POSTGRES_USER=postgres
POSTGRES_PASSWORD=$postgres_password
POSTGRES_DB=remnawave_bot
REDIS_HOST=redis
REDIS_PORT=6379
NODE_ENV=production
LOG_LEVEL=INFO
EOF
  chmod 600 "$env_file"
  print_success ".env файл создан: $env_file"
  print_info "Конфигурация сохранена, файл защищён (права 600)"
}

edit_env() {
  print_header "РЕДАКТИРОВАНИЕ КОНФИГУРАЦИИ (.env)"
  local env_file="$INSTALL_PATH/.env"
  if [[ ! -f "$env_file" ]]; then
    print_error ".env файл не найден!"
    read -rp "Создать новый .env файл? [Y/n]: " create_new
    if [[ "${create_new,,}" != "n" ]]; then
      setup_env
    fi
    return
  fi
  echo -e "${CYAN}[1]${NC} Редактировать в текстовом редакторе"
  echo -e "${CYAN}[2]${NC} Изменить конкретные параметры"
  echo -e "${CYAN}[3]${NC} Показать текущую конфигурацию"
  echo -e "${CYAN}[4]${NC} Пересоздать .env с нуля"
  echo -e "${CYAN}[0]${NC} Вернуться назад"
  echo ""
  read -rp "Выберите опцию: " choice
  case $choice in
    1)
      cp "$env_file" "$env_file.backup.$(date +%Y%m%d_%H%M%S)"
      print_info "Создана резервная копия"
      ${EDITOR:-nano} "$env_file"
      print_success "Файл сохранен"
      print_warning "Необходимо перезапустить сервисы для применения изменений"
      read -rp "Перезапустить сервисы сейчас? [Y/n]: " restart_now
      if [[ "${restart_now,,}" != "n" ]]; then
        run_compose restart
        print_success "Сервисы перезапущены"
      fi
      ;;
    2)
      edit_specific_env_params
      ;;
    3)
      print_section "Текущая конфигурация"
      cat "$env_file" | while IFS='=' read -r key value; do
        if [[ "$key" =~ (TOKEN|PASSWORD|SECRET|KEY)$ ]] && [[ ! "$key" =~ ^# ]]; then
          echo -e "${CYAN}$key${NC}=${YELLOW}****${NC}"
        elif [[ ! "$key" =~ ^# ]] && [[ -n "$key" ]]; then
          echo -e "${CYAN}$key${NC}=${GREEN}$value${NC}"
        else
          echo -e "${PURPLE}$key${NC}"
        fi
      done
      ;;
    4)
      print_warning "Текущий .env будет перезаписан!"
      read -rp "Продолжить? [y/N]: " confirm
      if [[ "${confirm,,}" == "y" ]]; then
        setup_env
      fi
      ;;
    0)
      return
      ;;
    *)
      print_error "Неверный выбор"
      ;;
  esac
}

edit_specific_env_params() {
  local env_file="$INSTALL_PATH/.env"
  print_section "Изменение параметров"
  echo -e "${CYAN}[1]${NC} BOT_TOKEN"
  echo -e "${CYAN}[2]${NC} ADMIN_IDS"
  echo -e "${CYAN}[3]${NC} WEB_API_DEFAULT_TOKEN"
  echo -e "${CYAN}[4]${NC} REMNAWAVE_API_URL"
  echo -e "${CYAN}[5]${NC} REMNAWAVE_API_KEY"
  echo -e "${CYAN}[6]${NC} REMNAWAVE_AUTH_TYPE"
  echo -e "${CYAN}[7]${NC} REMNAWAVE_USERNAME (Basic Auth)"
  echo -e "${CYAN}[8]${NC} REMNAWAVE_PASSWORD (Basic Auth)"
  echo -e "${CYAN}[9]${NC} REMNAWAVE_SECRET_KEY (eGames)"
  echo -e "${CYAN}[10]${NC} Пароль PostgreSQL"
  echo -e "${CYAN}[0]${NC} Назад"
  echo ""
  read -rp "Выберите параметр для изменения: " param_choice
  local param_name=""
  local param_prompt=""
  local param_value=""
  local is_secret=false
  case $param_choice in
    1)
      param_name="BOT_TOKEN"
      param_prompt="Введите новый токен бота"
      is_secret=true
      ;;
    2)
      param_name="ADMIN_IDS"
      param_prompt="Введите ID администраторов через запятую"
      ;;
    3)
      param_name="WEB_API_DEFAULT_TOKEN"
      param_prompt="Введите новый токен Web API"
      is_secret=true
      ;;
    4)
      param_name="REMNAWAVE_API_URL"
      param_prompt="Введите URL API Remnawave"
      ;;
    5)
      param_name="REMNAWAVE_API_KEY"
      param_prompt="Введите ключ API Remnawave"
      is_secret=true
      ;;
    6)
      param_name="REMNAWAVE_AUTH_TYPE"
      echo -e "${CYAN}[1]${NC} api_key"
      echo -e "${CYAN}[2]${NC} basic_auth"
      read -rp "Выберите тип авторизации: " auth_choice
      if [[ "$auth_choice" == "1" ]]; then
        param_value="api_key"
      else
        param_value="basic_auth"
      fi
      ;;
    7)
      param_name="REMNAWAVE_USERNAME"
      param_prompt="Введите имя пользователя для Basic Auth"
      ;;
    8)
      param_name="REMNAWAVE_PASSWORD"
      param_prompt="Введите пароль для Basic Auth"
      is_secret=true
      ;;
    9)
      param_name="REMNAWAVE_SECRET_KEY"
      param_prompt="Введите секретный ключ (формат: XXXXXXX:DDDDDDDD)"
      is_secret=true
      ;;
    10)
      param_name="POSTGRES_PASSWORD"
      param_prompt="Введите новый пароль PostgreSQL"
      is_secret=true
      ;;
    0)
      return
      ;;
    *)
      print_error "Неверный выбор"
      return
      ;;
  esac
  if [[ -z "$param_value" ]]; then
    if $is_secret; then
      read -rsp "$param_prompt: " param_value
      echo ""
    else
      read -rp "$param_prompt: " param_value
    fi
  fi
  if [[ -z "$param_value" ]]; then
    print_warning "Значение не указано, изменения не внесены"
    return
  fi
  cp "$env_file" "$env_file.backup.$(date +%Y%m%d_%H%M%S)"
  if grep -q "^$param_name=" "$env_file"; then
    sed -i "s|^$param_name=.*|$param_name=$param_value|" "$env_file"
    print_success "Параметр $param_name обновлен"
  else
    echo "$param_name=$param_value" >> "$env_file"
    print_success "Параметр $param_name добавлен"
  fi
  print_warning "Необходимо перезапустить сервисы для применения изменений"
  read -rp "Перезапустить сервисы сейчас? [Y/n]: " restart_now
  if [[ "${restart_now,,}" != "n" ]]; then
    run_compose restart
    print_success "Сервисы перезапущены"
  fi
}

resolve_compose_command() {
  if docker compose version >/dev/null 2>&1; then
    COMPOSE_BIN=(docker compose)
  elif docker-compose version >/dev/null 2>&1; then
    COMPOSE_BIN=(docker-compose)
  else
    print_error "Docker Compose не найден."
    exit 1
  fi
}

run_compose() {
  (cd "$INSTALL_PATH" && "${COMPOSE_BIN[@]}" "$@")
}

get_service_status() {
  local service=$1
  local status
  status=$(run_compose ps -q "$service" 2>/dev/null | xargs -r docker inspect -f '{{.State.Status}}' 2>/dev/null || echo "not_found")
  echo "$status"
}

check_webserver() {
  local caddy_installed=false
  local caddy_path=""
  if docker ps -a --format '{{.Names}}' | grep -q "caddy"; then
    caddy_installed=true
    local caddy_container
    caddy_container=$(docker ps -a --format '{{.Names}}' | grep "caddy" | head -n1)
    caddy_path=$(docker inspect "$caddy_container" 2>/dev/null | \
      grep -A 1 'Caddyfile' | \
      grep 'Source' | \
      sed 's/.*"Source": "\(.*\)".*/\1/' | \
      sed 's/\/Caddyfile$//')
    if [[ -z "$caddy_path" ]] || [[ ! -d "$caddy_path" ]]; then
      if [[ -f "/opt/caddy/Caddyfile" ]]; then
        caddy_path="/opt/caddy"
      elif [[ -f "$INSTALL_PATH/caddy/Caddyfile" ]]; then
        caddy_path="$INSTALL_PATH/caddy"
      fi
    fi
  fi
  echo "$caddy_installed|$caddy_path"
}

update_existing_caddy_compose() {
  local caddy_compose_path=$1
  print_info "Обновляем docker-compose.yml существующего Caddy..."
  if [[ ! -f "$caddy_compose_path" ]]; then
    print_error "Файл $caddy_compose_path не найден"
    return 1
  fi
  cp "$caddy_compose_path" "$caddy_compose_path.backup.$(date +%Y%m%d_%H%M%S)"
  print_info "Создана резервная копия"
  if grep -q "network_mode:" "$caddy_compose_path"; then
    if ! grep -q 'network_mode:.*host' "$caddy_compose_path"; then
      print_warning "Caddy использует другой network_mode, требуется ручная настройка"
      return 1
    fi
  else
    if command -v python3 >/dev/null 2>&1; then
      python3 - "$caddy_compose_path" "$INSTALL_PATH" <<'PY'
import sys
import yaml
import os

compose_path = sys.argv[1]
install_path = sys.argv[2]

with open(compose_path, 'r') as f:
    compose = yaml.safe_load(f)

if 'services' in compose:
    for service_name, service in compose['services'].items():
        if 'caddy' in service_name.lower():
            service['network_mode'] = 'host'
            if 'networks' in service:
                del service['networks']
            if 'ports' in service:
                del service['ports']
            
            # Ensure volumes list exists
            if 'volumes' not in service:
                service['volumes'] = []
            
            # Add miniapp volume if not present
            miniapp_volume = f"{install_path}/miniapp:/var/www/remnawave-miniapp:ro"
            if not any(miniapp_volume in str(v) for v in service['volumes']):
                service['volumes'].append(miniapp_volume)
            break

if 'networks' not in compose:
    compose['networks'] = {}
compose['networks']['default'] = {
    'name': 'bot_network',
    'external': True
}

with open(compose_path, 'w') as f:
    yaml.dump(compose, f, default_flow_style=False, sort_keys=False)
PY
      print_success "docker-compose.yml обновлен"
    else
      print_warning "Python3 не найден, добавьте вручную network_mode: host и volume для miniapp в docker-compose.yml"
      return 1
    fi
  fi
  return 0
}

install_caddy() {
  print_section "Установка Caddy"
  local caddy_dir="$INSTALL_PATH/caddy"
  mkdir -p "$caddy_dir/logs"
  mkdir -p "$INSTALL_PATH/miniapp/redirect"
  cat > "$caddy_dir/Caddyfile" <<'EOF'
# Caddy configuration
EOF
  cat > "$caddy_dir/docker-compose.yml" <<EOF
services:
  caddy:
    image: caddy:2.9.1
    container_name: caddy-bot-proxy
    restart: unless-stopped
    network_mode: "host"
    volumes:
      - ./Caddyfile:/etc/caddy/Caddyfile
      - ./logs:/var/log/caddy
      - caddy_data:/data
      - caddy_config:/config
      - $INSTALL_PATH/miniapp:/var/www/remnawave-miniapp:ro
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"
volumes:
  caddy_data:
  caddy_config:
networks:
  default:
    name: bot_network
    external: true
EOF
  print_info "Запускаем Caddy..."
  (cd "$caddy_dir" && docker compose up -d)
  sleep 2
  if docker ps | grep -q "caddy-bot-proxy"; then
    print_success "Caddy успешно установлен и запущен"
    print_info "Путь к конфигурации: $caddy_dir"
    return 0
  else
    print_error "Не удалось запустить Caddy"
    return 1
  fi
}

configure_webhook_proxy() {
  echo -e "\n${BLUE}${BOLD}${ARROW} Настройка прокси для webhook${NC}" >&2
  echo -e "${BLUE}─────────────────────────────────────────────────────${NC}" >&2
  local webhook_domain
  read -rp "Введите домен для webhook (например, webhook.example.com): " webhook_domain
  webhook_domain=$(echo "$webhook_domain" | tr -d '\r\n\t' | xargs | LC_ALL=C sed 's/[^a-zA-Z0-9.-]//g')
  if [[ -z "$webhook_domain" ]]; then
    echo -e "${RED}${CROSS} Домен не указан${NC}" >&2
    return 1
  fi
  if ! [[ "$webhook_domain" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$ ]]; then
    echo -e "${RED}${CROSS} Невалидный домен: $webhook_domain${NC}" >&2
    return 1
  fi
  echo -e "${CYAN}ℹ Используем домен: ${YELLOW}$webhook_domain${NC}" >&2
  cat <<EOF
$webhook_domain {
    handle /tribute-webhook* {
        reverse_proxy localhost:8081
    }
    handle /cryptobot-webhook* {
        reverse_proxy localhost:8081
    }
    handle /mulenpay-webhook* {
        reverse_proxy localhost:8081
    }
    handle /pal24-webhook* {
        reverse_proxy localhost:8084
    }
    handle /yookassa-webhook* {
        reverse_proxy localhost:8082
    }
    handle /health {
        reverse_proxy localhost:8081/health
    }
}
EOF
}

configure_miniapp_proxy() {
  echo -e "\n${BLUE}${BOLD}${ARROW} Настройка прокси для miniapp${NC}" >&2
  echo -e "${BLUE}─────────────────────────────────────────────────────${NC}" >&2
  local miniapp_domain
  read -rp "Введите домен для miniapp (например, miniapp.example.com): " miniapp_domain
  miniapp_domain=$(echo "$miniapp_domain" | tr -d '\r\n\t' | xargs | LC_ALL=C sed 's/[^a-zA-Z0-9.-]//g')
  if [[ -z "$miniapp_domain" ]]; then
    echo -e "${RED}${CROSS} Домен не указан${NC}" >&2
    return 1
  fi
  if ! [[ "$miniapp_domain" =~ ^[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*$ ]]; then
    echo -e "${RED}${CROSS} Невалидный домен: $miniapp_domain${NC}" >&2
    return 1
  fi
  echo -e "${CYAN}ℹ Используем домен: ${YELLOW}$miniapp_domain${NC}" >&2
  cat <<EOF
$miniapp_domain {
    encode gzip zstd
    root * /var/www/remnawave-miniapp
    file_server
    @config path /app-config.json
    header @config Access-Control-Allow-Origin "*"
    @redirect path /miniapp/redirect/index.html
    redir @redirect /miniapp/redirect/index.html permanent
    reverse_proxy /miniapp/* 127.0.0.1:8080 {
        header_up Host {host}
        header_up X-Real-IP {remote_host}
    }
}
EOF
}

upsert_caddy_block() {
  local caddy_file=$1
  local config=$2
  local label=$3
  local stripped
  stripped=$(echo "$config" | tr -d ' \t\n\r')
  if [[ -z "$stripped" ]]; then
    return 0
  fi
  local first_line
  first_line=$(echo "$config" | sed -n '1p')
  local domain=${first_line%% *}
  if [[ -z "$domain" ]]; then
    print_warning "Не удалось определить домен для секции $label"
    return 1
  fi
  local domain_marker="$domain {"
  if [[ -f "$caddy_file" ]] && grep -Fq "$domain_marker" "$caddy_file"; then
    if ! command -v python3 >/dev/null 2>&1; then
      print_error "Python3 не найден, не могу обновить существующую конфигурацию домена $domain"
      return 1
    fi
    print_info "Обновляем конфигурацию домена $domain"
    python3 - "$caddy_file" "$domain" <<'PY'
import os
import sys
path, domain = sys.argv[1:]
if not os.path.exists(path):
    sys.exit(0)
with open(path, encoding="utf-8") as fh:
    lines = fh.read().splitlines()
result = []
skip = False
brace_level = 0
for line in lines:
    stripped = line.lstrip()
    if not skip:
        if stripped.startswith(domain) and stripped[len(domain):].lstrip().startswith('{'):
            skip = True
            brace_level = line.count('{') - line.count('}')
            continue
        result.append(line)
        continue
    brace_level += line.count('{') - line.count('}')
    if brace_level <= 0:
        skip = False
text = "\n".join(result)
if text and not text.endswith("\n"):
    text += "\n"
with open(path, "w", encoding="utf-8") as fh:
    fh.write(text)
PY
  else
    print_info "Добавляем новый домен $domain"
  fi
  if [[ -s "$caddy_file" ]]; then
    if [[ $(tail -c1 "$caddy_file" 2>/dev/null | od -An -tx1) != "0a" ]]; then
      echo >> "$caddy_file"
    fi
    local last_line
    last_line=$(tail -n1 "$caddy_file" 2>/dev/null || echo '')
    if [[ -n "$last_line" ]]; then
      echo >> "$caddy_file"
    fi
  fi
  printf '%s\n' "$config" >> "$caddy_file"
  print_success "Конфигурация для домена $domain обновлена"
}

apply_caddy_config() {
  local caddy_dir=$1
  local webhook_config=$2
  local miniapp_config=$3
  local caddy_file="$caddy_dir/Caddyfile"
  mkdir -p "$caddy_dir"
  if [[ -f "$caddy_file" ]]; then
    cp "$caddy_file" "$caddy_dir/Caddyfile.backup.$(date +%Y%m%d_%H%M%S)"
    print_info "Резервная копия создана"
  else
    print_info "Создаем новый Caddyfile"
  fi
  if [[ ! -s "$caddy_file" ]]; then
    cat > "$caddy_file" <<EOF
# Caddy configuration for Remnawave Bot
EOF
  fi
  upsert_caddy_block "$caddy_file" "$webhook_config" "webhook"
  upsert_caddy_block "$caddy_file" "$miniapp_config" "miniapp"
  print_success "Конфигурация записана в $caddy_file"
  print_info "Перезагружаем Caddy..."
  local caddy_container
  caddy_container=$(docker ps --filter "name=caddy" --format "{{.Names}}" | head -n1)
  if [[ -n "$caddy_container" ]]; then
    if docker exec "$caddy_container" caddy validate --config /etc/caddy/Caddyfile 2>/dev/null; then
      print_success "Конфигурация валидна"
      if docker exec "$caddy_container" caddy reload --config /etc/caddy/Caddyfile 2>/dev/null; then
        print_success "Caddy перезагружен успешно"
      else
        print_warning "Перезагрузка через reload не удалась, перезапускаем контейнер..."
        docker restart "$caddy_container"
        sleep 3
        print_success "Контейнер перезапущен"
      fi
    else
      print_error "Ошибка валидации конфигурации Caddy"
      print_warning "Восстанавливаем предыдущую конфигурацию..."
      local last_backup
      last_backup=$(ls -t "$caddy_dir"/Caddyfile.backup.* 2>/dev/null | head -n1)
      if [[ -n "$last_backup" ]]; then
        cp "$last_backup" "$caddy_dir/Caddyfile"
        print_info "Предыдущая конфигурация восстановлена"
      fi
      return 1
    fi
  else
    print_error "Caddy контейнер не найден или не запущен"
    print_info "Попробуйте запустить: docker start caddy"
    return 1
  fi
}

show_proxy_status() {
  print_header "СТАТУС ОБРАТНОГО ПРОКСИ"
  local webserver_info
  webserver_info=$(check_webserver)
  IFS='|' read -r caddy_installed caddy_path <<< "$webserver_info"
  print_section "Установленные веб-серверы"
  if [[ "$caddy_installed" == "true" ]]; then
    local caddy_container
    caddy_container=$(docker ps --filter "name=caddy" --format "{{.Names}}" | head -n1)
    local caddy_status
    caddy_status=$(docker inspect -f '{{.State.Status}}' "$caddy_container" 2>/dev/null || echo "not_found")
    print_status "$caddy_status" "Caddy: $caddy_status"
    if [[ -n "$caddy_path" ]]; then
      echo -e "   ${CYAN}Путь к конфигурации: ${YELLOW}$caddy_path${NC}"
    fi
    if [[ -f "$caddy_path/Caddyfile" ]]; then
      print_info "Настроенные домены в Caddy:"
      grep -E "^[a-zA-Z0-9\.-]+ \{" "$caddy_path/Caddyfile" | sed 's/ {//' | while read -r domain; do
        echo -e "   ${GREEN}→${NC} $domain"
      done
    fi
    print_info "Caddy работает в режиме host network"
  else
    print_warning "Caddy не установлен"
  fi
}

configure_reverse_proxy() {
  while true; do
    print_header "НАСТРОЙКА ОБРАТНОГО ПРОКСИ"
    local webserver_info
    webserver_info=$(check_webserver)
    IFS='|' read -r caddy_installed caddy_path <<< "$webserver_info"
    echo -e "${CYAN}[1]${NC} 📊 Показать статус прокси"
    echo -e "${CYAN}[2]${NC} ⚙️  Настроить Caddy (webhook + miniapp)"
    if [[ "$caddy_installed" == "false" ]]; then
      echo -e "${CYAN}[3]${NC} 📦 Установить Caddy"
    else
      echo -e "${CYAN}[3]${NC} 📝 Редактировать Caddyfile вручную"
      echo -e "${CYAN}[4]${NC} 🔧 Обновить docker-compose.yml Caddy"
    fi
    echo -e "${CYAN}[5]${NC} 🔄 Перезагрузить Caddy"
    echo -e "${CYAN}[0]${NC} 🔙 Вернуться в главное меню"
    echo ""
    read -rp "$(echo -e ${WHITE}${BOLD}Выберите опцию: ${NC})" choice
    case $choice in
      1)
        show_proxy_status
        ;;
      2)
        if [[ "$caddy_installed" == "false" ]]; then
          print_warning "Caddy не установлен"
          read -rp "Установить Caddy сейчас? [y/N]: " install_confirm
          if [[ "${install_confirm,,}" == "y" ]]; then
            install_caddy
            caddy_path="$INSTALL_PATH/caddy"
          else
            continue
          fi
        fi
        if [[ -z "$caddy_path" ]] || [[ ! -d "$caddy_path" ]]; then
          print_warning "Автоматически определить путь не удалось"
          echo -e "${CYAN}Обнаруженные пути с Caddyfile:${NC}"
          local found_paths=()
          while IFS= read -r caddyfile; do
            local dir_path
            dir_path=$(dirname "$caddyfile")
            echo -e "  ${GREEN}→${NC} $dir_path"
            found_paths+=("$dir_path")
          done < <(find /opt /root "$INSTALL_PATH" -name "Caddyfile" 2>/dev/null | head -n 5)
          if [[ ${#found_paths[@]} -eq 1 ]]; then
            caddy_path="${found_paths[0]}"
            print_info "Используем найденный путь: $caddy_path"
          else
            read -rp "Введите путь к директории с Caddyfile: " caddy_path
          fi
        fi
        if [[ ! -d "$caddy_path" ]]; then
          print_error "Директория не найдена: $caddy_path"
          continue
        fi
        if [[ ! -f "$caddy_path/Caddyfile" ]]; then
          print_error "Файл Caddyfile не найден в $caddy_path"
          read -rp "Создать новый Caddyfile? [y/N]: " create_new
          if [[ "${create_new,,}" != "y" ]]; then
            continue
          fi
          touch "$caddy_path/Caddyfile"
        fi
        local webhook_config
        local miniapp_config
        webhook_config=$(configure_webhook_proxy)
        miniapp_config=$(configure_miniapp_proxy)
        echo ""
        print_info "Предпросмотр конфигурации:"
        echo -e "${YELLOW}$webhook_config${NC}"
        echo -e "${YELLOW}$miniapp_config${NC}"
        read -rp "Применить эту конфигурацию? [y/N]: " confirm
        if [[ "${confirm,,}" == "y" ]]; then
          apply_caddy_config "$caddy_path" "$webhook_config" "$miniapp_config"
        fi
        ;;
      3)
        if [[ "$caddy_installed" == "false" ]]; then
          install_caddy
        else
          if [[ -z "$caddy_path" ]]; then
            read -rp "Введите путь к директории с Caddyfile: " caddy_path
          fi
          if [[ ! -f "$caddy_path/Caddyfile" ]]; then
            print_error "Caddyfile не найден в $caddy_path"
            continue
          fi
          print_info "Открываем Caddyfile для редактирования..."
          print_warning "Будет создана резервная копия"
          cp "$caddy_path/Caddyfile" "$caddy_path/Caddyfile.backup.$(date +%Y%m%d_%H%M%S)"
          ${EDITOR:-nano} "$caddy_path/Caddyfile"
          print_info "Проверяем конфигурацию..."
          local caddy_container
          caddy_container=$(docker ps --filter "name=caddy" --format "{{.Names}}" | head -n1)
          if [[ -n "$caddy_container" ]]; then
            if docker exec "$caddy_container" caddy validate --config /etc/caddy/Caddyfile 2>&1; then
              print_success "Конфигурация валидна"
              read -rp "Перезагрузить Caddy? [Y/n]: " reload_confirm
              if [[ "${reload_confirm,,}" != "n" ]]; then
                docker exec "$caddy_container" caddy reload --config /etc/caddy/Caddyfile
                print_success "Caddy перезагружен"
              fi
            else
              print_error "Конфигурация содержит ошибки!"
              read -rp "Восстановить из резервной копии? [Y/n]: " restore_confirm
              if [[ "${restore_confirm,,}" != "n" ]]; then
                local last_backup
                last_backup=$(ls -t "$caddy_path"/Caddyfile.backup.* 2>/dev/null | head -n1)
                if [[ -n "$last_backup" ]]; then
                  cp "$last_backup" "$caddy_path/Caddyfile"
                  print_success "Конфигурация восстановлена"
                fi
              fi
            fi
          fi
        fi
        ;;
      4)
        if [[ "$caddy_installed" == "true" ]] && [[ -n "$caddy_path" ]]; then
          if [[ -f "$caddy_path/docker-compose.yml" ]]; then
            update_existing_caddy_compose "$caddy_path/docker-compose.yml"
            print_info "Перезапускаем Caddy с новой конфигурацией..."
            (cd "$caddy_path" && docker compose down && docker compose up -d)
            sleep 2
            if docker ps | grep -q "caddy"; then
              print_success "Caddy перезапущен с обновленной конфигурацией"
            else
              print_error "Ошибка при перезапуске Caddy"
            fi
          else
            print_error "docker-compose.yml не найден в $caddy_path"
          fi
        else
          print_error "Caddy не установлен или путь не определен"
        fi
        ;;
      5)
        print_section "Перезагрузка Caddy"
        if [[ "$caddy_installed" == "true" ]]; then
          local caddy_container
          caddy_container=$(docker ps --filter "name=caddy" --format "{{.Names}}" | head -n1)
          if [[ -n "$caddy_container" ]]; then
            print_info "Перезагружаем Caddy..."
            docker restart "$caddy_container"
            sleep 2
            if docker ps --filter "name=caddy" --filter "status=running" | grep -q caddy; then
              print_success "Caddy перезапущен успешно"
            else
              print_error "Ошибка при перезапуске Caddy"
            fi
          fi
        fi
        ;;
      0)
        return 0
        ;;
      *)
        print_error "Неверный выбор"
        ;;
    esac
    echo ""
    read -rp "$(echo -e ${CYAN}Нажмите Enter для продолжения...${NC})"
  done
}

show_monitoring() {
  print_header "МОНИТОРИНГ СЕРВИСОВ БОТА"
  print_section "Статус контейнеров"
  local services=("bot" "postgres" "redis")
  local all_running=true
  for service in "${services[@]}"; do
    local status
    status=$(get_service_status "$service")
    local uptime=""
    if [[ "$status" == "running" ]]; then
      uptime=$(run_compose ps "$service" 2>/dev/null | tail -n1 | awk '{for(i=1;i<=NF;i++){if($i~/Up/){print $(i+1), $(i+2); break}}}')
      print_status "running" "$service: работает (uptime: $uptime)"
    elif [[ "$status" == "exited" ]] || [[ "$status" == "stopped" ]]; then
      print_status "stopped" "$service: остановлен"
      all_running=false
    else
      print_status "unknown" "$service: не найден"
      all_running=false
    fi
  done
  print_section "Использование ресурсов"
  local stats
  stats=$(docker stats --no-stream --format "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}" 2>/dev/null | grep -E "bot|postgres|redis" || echo "")
  if [[ -n "$stats" ]]; then
    echo -e "${WHITE}${BOLD}КОНТЕЙНЕР          CPU       ПАМЯТЬ${NC}"
    echo "$stats" | tail -n+2 | while IFS="$(printf '\t')" read -r name cpu mem; do
      echo -e "${CYAN}${name}${NC}  ${YELLOW}${cpu}${NC}  ${PURPLE}${mem}${NC}"
    done
  else
    print_warning "Статистика недоступна"
  fi
  print_section "Размер логов"
  if [[ -d "$INSTALL_PATH/logs" ]]; then
    local log_size
    log_size=$(du -sh "$INSTALL_PATH/logs" 2>/dev/null | cut -f1)
    echo -e "${CYAN}Логи: ${YELLOW}${log_size}${NC}"
  fi
  print_section "Последние ошибки (если есть)"
  local errors
  errors=$(run_compose logs --tail=100 bot 2>/dev/null | grep -i "error\|exception\|critical" | tail -n 5 || echo "")
  if [[ -n "$errors" ]]; then
    echo "$errors" | while read -r line; do
      print_error "$line"
    done
  else
    print_success "Ошибок не обнаружено"
  fi
  echo ""
  if $all_running; then
    print_success "Все сервисы работают нормально!"
  else
    print_warning "Некоторые сервисы не запущены"
  fi
}

update_containers() {
  print_header "ОБНОВЛЕНИЕ ОБРАЗОВ КОНТЕЙНЕРОВ"
  print_info "Получаем последние версии образов через Docker Compose..."
  if run_compose pull; then
    print_success "Образы успешно обновлены"
  else
    print_error "Не удалось обновить образы контейнеров"
    return 1
  fi
  echo ""
  read -rp "$(echo -e ${YELLOW}Перезапустить сервисы после обновления? [Y/n]: ${NC})" restart_after_pull
  if [[ "${restart_after_pull,,}" != "n" ]]; then
    print_info "Запускаем сервисы с обновленными образами..."
    if run_compose up -d; then
      print_success "Сервисы запущены"
      show_monitoring
    else
      print_error "Не удалось запустить сервисы"
      return 1
    fi
  else
    print_warning "Перезапустите сервисы позже для применения обновлений"
  fi
}

update_from_git() {
  print_header "ОБНОВЛЕНИЕ ИЗ GIT РЕПОЗИТОРИЯ"
  if [[ ! -d "$INSTALL_PATH/.git" ]]; then
    print_error "Git репозиторий не найден в $INSTALL_PATH"
    print_info "Инициализируем репозиторий..."
    local repo_url
    read -rp "Введите URL Git репозитория: " repo_url
    if [[ -z "$repo_url" ]]; then
      print_error "URL не указан"
      return 1
    fi
    (cd "$INSTALL_PATH" && git init && git remote add origin "$repo_url")
  fi
  print_section "Проверка обновлений"
  (cd "$INSTALL_PATH" && git fetch origin 2>&1)
  local current_commit
  local remote_commit
  current_commit=$(cd "$INSTALL_PATH" && git rev-parse HEAD 2>/dev/null || echo "unknown")
  remote_commit=$(cd "$INSTALL_PATH" && git rev-parse origin/main 2>/dev/null || git rev-parse origin/master 2>/dev/null || echo "unknown")
  if [[ "$current_commit" == "$remote_commit" ]]; then
    print_success "Бот уже имеет последнюю версию"
    return 0
  fi
  print_info "Найдены обновления"
  echo -e "${CYAN}Текущий коммит: ${YELLOW}${current_commit:0:8}${NC}"
  echo -e "${CYAN}Новый коммит:   ${YELLOW}${remote_commit:0:8}${NC}"
  print_section "Список изменений"
  (cd "$INSTALL_PATH" && git log --oneline HEAD..origin/main 2>/dev/null || git log --oneline HEAD..origin/master 2>/dev/null || true)
  echo ""
  read -rp "$(echo -e ${YELLOW}Применить обновления? [y/N]: ${NC})" confirm
  if [[ "${confirm,,}" != "y" ]]; then
    print_warning "Обновление отменено"
    return 1
  fi
  print_info "Создаем резервную копию перед обновлением..."
  create_backup "pre-update"
  print_section "Применение обновлений"
  print_info "Останавливаем сервисы..."
  run_compose down
  print_info "Обновляем код..."
  (cd "$INSTALL_PATH" && git pull origin main 2>/dev/null || git pull origin master 2>/dev/null)
  print_info "Пересобираем и запускаем сервисы..."
  run_compose up -d --build
  print_success "Обновление завершено!"
  echo ""
  read -rp "$(echo -e ${YELLOW}Показать логи запуска? [y/N]: ${NC})" show_logs
  if [[ "${show_logs,,}" == "y" ]]; then
    run_compose logs --tail=50 -f bot
  fi
}

create_backup() {
  local backup_type=${1:-manual}
  local timestamp
  timestamp=$(date +%Y%m%d_%H%M%S)
  local backup_name="backup_${backup_type}_${timestamp}"
  local backup_path="$BACKUP_DIR/$backup_name"
  print_header "СОЗДАНИЕ РЕЗЕРВНОЙ КОПИИ"
  mkdir -p "$BACKUP_DIR"
  mkdir -p "$backup_path"
  print_section "Архивирование данных"
  print_info "Сохраняем конфигурацию..."
  cp "$INSTALL_PATH/.env" "$backup_path/" 2>/dev/null || true
  cp "$INSTALL_PATH/docker-compose.yml" "$backup_path/" 2>/dev/null || true
  if [[ $(get_service_status "postgres") == "running" ]]; then
    print_info "Экспортируем базу данных PostgreSQL..."
    run_compose exec -T postgres pg_dump -U postgres remnawave_bot > "$backup_path/database.sql" 2>/dev/null || {
      print_warning "Не удалось экспортировать БД"
    }
  fi
  if [[ -d "$INSTALL_PATH/data" ]]; then
    print_info "Копируем пользовательские данные..."
    cp -r "$INSTALL_PATH/data" "$backup_path/" 2>/dev/null || true
  fi
  print_info "Создаем архив..."
  (cd "$BACKUP_DIR" && tar -czf "${backup_name}.tar.gz" "$backup_name" && rm -rf "$backup_name")
  local backup_size
  backup_size=$(du -h "$BACKUP_DIR/${backup_name}.tar.gz" | cut -f1)
  print_success "Резервная копия создана: $BACKUP_DIR/${backup_name}.tar.gz"
  echo -e "${CYAN}Размер: ${YELLOW}${backup_size}${NC}"
  print_info "Очистка старых бэкапов..."
  (cd "$BACKUP_DIR" && ls -t backup_*.tar.gz 2>/dev/null | tail -n +11 | xargs -r rm -f)
  local backup_count
  backup_count=$(ls -1 "$BACKUP_DIR"/backup_*.tar.gz 2>/dev/null | wc -l)
  print_info "Всего резервных копий: $backup_count"
}

restore_backup() {
  print_header "ВОССТАНОВЛЕНИЕ ИЗ РЕЗЕРВНОЙ КОПИИ"
  if [[ ! -d "$BACKUP_DIR" ]] || [[ -z "$(ls -A "$BACKUP_DIR"/*.tar.gz 2>/dev/null)" ]]; then
    print_error "Резервные копии не найдены"
    return 1
  fi
  print_section "Доступные резервные копии"
  local backups=()
  local i=1
  while IFS= read -r backup; do
    local backup_name
    local backup_size
    local backup_date
    backup_name=$(basename "$backup")
    backup_size=$(du -h "$backup" | cut -f1)
    backup_date=$(stat -c %y "$backup" 2>/dev/null | cut -d' ' -f1,2 | cut -d'.' -f1 || stat -f "%Sm" "$backup")
    echo -e "${CYAN}[$i]${NC} ${WHITE}$backup_name${NC}"
    echo -e "    Размер: ${YELLOW}$backup_size${NC}, Дата: ${PURPLE}$backup_date${NC}"
    backups+=("$backup")
    ((i++))
  done < <(ls -t "$BACKUP_DIR"/*.tar.gz 2>/dev/null)
  echo ""
  read -rp "Выберите номер резервной копии для восстановления [1-$((i-1))]: " selection
  if [[ ! "$selection" =~ ^[0-9]+$ ]] || [[ "$selection" -lt 1 ]] || [[ "$selection" -ge "$i" ]]; then
    print_error "Неверный выбор"
    return 1
  fi
  local selected_backup="${backups[$((selection-1))]}"
  print_warning "ВНИМАНИЕ: Текущие данные будут перезаписаны!"
  read -rp "$(echo -e ${RED}${BOLD}Продолжить восстановление? [y/N]: ${NC})" confirm
  if [[ "${confirm,,}" != "y" ]]; then
    print_warning "Восстановление отменено"
    return 1
  fi
  print_info "Создаем резервную копию текущего состояния..."
  create_backup "pre-restore"
  print_section "Восстановление данных"
  print_info "Останавливаем сервисы..."
  run_compose down
  print_info "Распаковываем резервную копию..."
  local temp_dir
  temp_dir=$(mktemp -d)
  tar -xzf "$selected_backup" -C "$temp_dir"
  local backup_folder
  backup_folder=$(ls "$temp_dir")
  if [[ -f "$temp_dir/$backup_folder/.env" ]]; then
    print_info "Восстанавливаем конфигурацию..."
    cp "$temp_dir/$backup_folder/.env" "$INSTALL_PATH/"
  fi
  if [[ -d "$temp_dir/$backup_folder/data" ]]; then
    print_info "Восстанавливаем пользовательские данные..."
    rm -rf "$INSTALL_PATH/data"
    cp -r "$temp_dir/$backup_folder/data" "$INSTALL_PATH/"
  fi
  print_info "Запускаем сервисы..."
  run_compose up -d
  if [[ -f "$temp_dir/$backup_folder/database.sql" ]]; then
    print_info "Ожидаем запуска PostgreSQL..."
    sleep 5
    print_info "Восстанавливаем базу данных..."
    run_compose exec -T postgres psql -U postgres remnawave_bot < "$temp_dir/$backup_folder/database.sql" 2>/dev/null || {
      print_warning "Не удалось восстановить БД (возможно, структура уже актуальна)"
    }
  fi
  rm -rf "$temp_dir"
  print_success "Восстановление завершено!"
  echo ""
  show_monitoring
}

view_logs() {
  print_header "ПРОСМОТР ЛОГОВ"
  echo -e "${CYAN}[1]${NC} Логи бота (последние 100 строк)"
  echo -e "${CYAN}[2]${NC} Логи PostgreSQL (последние 100 строк)"
  echo -e "${CYAN}[3]${NC} Логи Redis (последние 100 строк)"
  echo -e "${CYAN}[4]${NC} Все логи (последние 100 строк)"
  echo -e "${CYAN}[5]${NC} Следить за логами в реальном времени"
  echo -e "${CYAN}[6]${NC} Поиск по логам"
  echo ""
  read -rp "Выберите опцию [1-6]: " choice
  case $choice in
    1)
      run_compose logs --tail=100 bot
      ;;
    2)
      run_compose logs --tail=100 postgres
      ;;
    3)
      run_compose logs --tail=100 redis
      ;;
    4)
      run_compose logs --tail=100
      ;;
    5)
      print_info "Нажмите Ctrl+C для выхода"
      run_compose logs -f
      ;;
    6)
      read -rp "Введите текст для поиска: " search_term
      run_compose logs | grep -i "$search_term" --color=always | tail -n 50
      ;;
    *)
      print_error "Неверный выбор"
      ;;
  esac
}

manage_services() {
  print_header "УПРАВЛЕНИЕ СЕРВИСАМИ"
  echo -e "${CYAN}[1]${NC} Запустить все сервисы"
  echo -e "${CYAN}[2]${NC} Остановить все сервисы"
  echo -e "${CYAN}[3]${NC} Перезапустить все сервисы"
  echo -e "${CYAN}[4]${NC} Пересобрать и запустить"
  echo -e "${CYAN}[5]${NC} Остановить и удалить контейнеры"
  echo ""
  read -rp "Выберите опцию [1-5]: " choice
  case $choice in
    1)
      print_info "Запускаем сервисы..."
      run_compose up -d
      print_success "Сервисы запущены"
      show_monitoring
      ;;
    2)
      print_info "Останавливаем сервисы..."
      run_compose stop
      print_success "Сервисы остановлены"
      ;;
    3)
      print_info "Перезапускаем сервисы..."
      run_compose restart
      print_success "Сервисы перезапущены"
      show_monitoring
      ;;
    4)
      print_info "Пересобираем и запускаем..."
      run_compose up -d --build
      print_success "Сервисы пересобраны и запущены"
      show_monitoring
      ;;
    5)
      print_warning "Контейнеры будут удалены (данные сохранятся в volumes)"
      read -rp "$(echo -e ${YELLOW}Продолжить? [y/N]: ${NC})" confirm
      if [[ "${confirm,,}" == "y" ]]; then
        run_compose down
        print_success "Контейнеры остановлены и удалены"
      fi
      ;;
    *)
      print_error "Неверный выбор"
      ;;
  esac
}

cleanup_system() {
  print_header "ОЧИСТКА СИСТЕМЫ"
  echo -e "${CYAN}[1]${NC} Очистить старые логи (старше 7 дней)"
  echo -e "${CYAN}[2]${NC} Очистить старые резервные копии (оставить 5 последних)"
  echo -e "${CYAN}[3]${NC} Очистить неиспользуемые Docker образы"
  echo -e "${CYAN}[4]${NC} Полная очистка (всё вышеперечисленное)"
  echo ""
  read -rp "Выберите опцию [1-4]: " choice
  case $choice in
    1)
      print_info "Очищаем старые логи..."
      find "$INSTALL_PATH/logs" -type f -mtime +7 -delete 2>/dev/null || true
      print_success "Старые логи удалены"
      ;;
    2)
      print_info "Очищаем старые бэкапы..."
      (cd "$BACKUP_DIR" && ls -t backup_*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f)
      print_success "Старые бэкапы удалены"
      ;;
    3)
      print_info "Очищаем неиспользуемые Docker образы..."
      docker image prune -f
      print_success "Неиспользуемые образы удалены"
      ;;
    4)
      print_info "Выполняем полную очистку..."
      find "$INSTALL_PATH/logs" -type f -mtime +7 -delete 2>/dev/null || true
      (cd "$BACKUP_DIR" && ls -t backup_*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f)
      docker image prune -f
      docker volume prune -f
      print_success "Полная очистка завершена"
      ;;
    *)
      print_error "Неверный выбор"
      ;;
  esac
}

show_menu() {
  clear
  echo -e "${PURPLE}${BOLD}"
  cat << "EOF"
╔════════════════════════════════════════════════════════════╗
║                                                            ║
║     ██████╗  ██████╗ ████████╗    ███╗   ███╗ ██████╗ ██████╗  
║     ██╔══██╗██╔═══██╗╚══██╔══╝    ████╗ ████║██╔════╝ ██╔══██╗
║     ██████╔╝██║   ██║   ██║       ██╔████╔██║██║  ███╗██████╔╝
║     ██╔══██╗██║   ██║   ██║       ██║╚██╔╝██║██║   ██║██╔══██╗
║     ██████╔╝╚██████╔╝   ██║       ██║ ╚═╝ ██║╚██████╔╝██║  ██║
║     ╚═════╝  ╚═════╝    ╚═╝       ╚═╝     ╚═╝ ╚═════╝ ╚═╝  ╚═╝
║                                                            ║
║              Система управления Telegram ботом            ║
╚════════════════════════════════════════════════════════════╝
EOF
  echo -e "${NC}"
  echo -e "${WHITE}${BOLD}Путь установки:${NC} ${CYAN}$INSTALL_PATH${NC}"
  echo ""
  echo -e "${WHITE}${BOLD}Статус контейнеров:${NC}"
  local services=("bot" "postgres" "redis")
  local status=""
  for service in "${services[@]}"; do
    status=$(get_service_status "$service")
    if [[ "$status" == "running" ]]; then
      print_status "running" "$service: работает"
    elif [[ "$status" == "exited" ]] || [[ "$status" == "stopped" ]]; then
      print_status "stopped" "$service: остановлен"
    else
      print_status "unknown" "$service: статус неизвестен"
    fi
  done
  echo ""
  echo -e "${GREEN}${BOLD}[1]${NC} ${STAR} Мониторинг и статус сервисов"
  echo -e "${BLUE}${BOLD}[2]${NC} ${GEAR} Управление сервисами"
  echo -e "${YELLOW}${BOLD}[3]${NC} 📋 Просмотр логов"
  echo -e "${PURPLE}${BOLD}[4]${NC} ⬇️  Обновить контейнеры (docker compose pull)"
  echo -e "${PURPLE}${BOLD}[5]${NC} 🔄 Обновление из Git"
  echo -e "${CYAN}${BOLD}[6]${NC} 💾 Создать резервную копию"
  echo -e "${YELLOW}${BOLD}[7]${NC} 📦 Восстановить из резервной копии"
  echo -e "${RED}${BOLD}[8]${NC} 🧹 Очистка системы"
  echo -e "${PURPLE}${BOLD}[9]${NC} 🌐 Настройка обратного прокси (Caddy)"
  echo -e "${GREEN}${BOLD}[10]${NC} ⚙️  Настройка конфигурации (.env)"
  echo -e "${WHITE}${BOLD}[0]${NC} 🚪 Выход"
  echo ""
}

main() {
  load_state
  resolve_compose_command
  while true; do
    show_menu
    read -rp "$(echo -e ${WHITE}${BOLD}Выберите опцию: ${NC})" choice
    case $choice in
      1)
        show_monitoring
        ;;
      2)
        manage_services
        ;;
      3)
        view_logs
        ;;
      4)
        update_containers
        ;;
      5)
        update_from_git
        ;;
      6)
        create_backup "manual"
        ;;
      7)
        restore_backup
        ;;
      8)
        cleanup_system
        ;;
      9)
        configure_reverse_proxy
        ;;
      10)
        if check_env_exists; then
          edit_env
        else
          setup_env
        fi
        ;;
      0)
        print_success "До свидания!"
        exit 0
        ;;
      *)
        print_error "Неверный выбор. Попробуйте снова."
        ;;
    esac
    echo ""
    read -rp "$(echo -e ${CYAN}Нажмите Enter для продолжения...${NC})"
  done
}

main "$@"
