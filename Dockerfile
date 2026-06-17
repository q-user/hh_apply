FROM python:3.13-slim

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

# Системные зависимости
RUN apt-get update && apt-get install -y --no-install-recommends \
  gcc \
  libc6-dev \
  procps \
  cron \
  dos2unix \
  tzdata \
  less

# Настройка пользователя
ARG UID=1000
ARG GID=1000
RUN groupadd -g $GID docker && \
  useradd -u $UID -g docker -m -s /bin/bash docker

WORKDIR /app

COPY pyproject.toml uv.lock* README.md /app/

# 1. Копируем исходный код (нужен для editable install)
COPY src /app/src

# 2. Устанавливаем пакет со ВСЕМИ extras и dev-зависимостями из uv.lock
# uv sync гарантирует воспроизводимость версий по uv.lock
RUN uv sync --system --all-extras --all-groups --no-dev

# 3. Скачиваем браузер и системные зависимости (этот тяжелый слой кэшируется отдельно)
RUN playwright install-deps chromium && \
    su docker -c "playwright install chromium"

# Очистка кеша пакетов для уменьшения веса контейнера
RUN rm -rf /var/lib/apt/lists/*

# Fix: падение, если каталог config не существует
#RUN mkdir -p /app/config

# Копируем конфиги и скрипты
COPY config /app/config
COPY scripts /app/scripts

# Настройка крона
RUN touch /var/log/cron.log && chown docker:docker /var/log/cron.log && \
  dos2unix /app/config/crontab && \
  chmod +x /app/scripts/startup.sh && \
  chmod 0644 /app/config/crontab && \
  crontab -u docker /app/config/crontab

# Дефолтный CMD — заглушка. Реальная команда задаётся в docker-compose.yml
# для каждого сервиса (hh_collector: cron + tail, hh_tg_bot: telegram-bot,
# hh_apply_worker: apply-worker).
# cron не видит переменные окружения, переданные главному процессу — он
# стартует новую сессию, где $CONFIG_DIR / $HH_PROFILE_ID / $RESUME_ID / $SEARCH_QUERY
# могут быть пустыми. Сервис hh_collector пробрасывает нужные переменные в
# /etc/environment в своём command (см. docker-compose.yml).
CMD ["sh", "-c", "echo 'hh-applicant-tool image: override CMD via docker compose to start hh_collector / hh_tg_bot / hh_apply_worker.' && exit 1"]
