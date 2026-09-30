# motorhof-bot: бот + воркер в одном процессе. Сборка и запуск: docker compose up -d --build
FROM python:3.12-slim

# Кириллица в путях Drive (НАЛИЧИЕ, Фотографии, На выгрузку) и время по Вене
ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Europe/Vienna \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# rclone — официальный релиз с GitHub, фиксированная версия и контрольная сумма
# (имена ARG не начинаются с RCLONE_: rclone принял бы их за свои флаги)
# (пакет из Debian слишком старый: нет SHA256 у Google Drive)
ARG RELEASE_RCLONE=v1.71.1
ARG SHA256_RCLONE_ZIP=417e3da236f3a12d292da4e7287d67b1df558b8c2b280d092e563958ed724be7
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl unzip tzdata \
    && curl -fsSL -o /tmp/rclone.zip \
       "https://github.com/rclone/rclone/releases/download/${RELEASE_RCLONE}/rclone-${RELEASE_RCLONE}-linux-amd64.zip" \
    && echo "${SHA256_RCLONE_ZIP}  /tmp/rclone.zip" | sha256sum -c - \
    && unzip -q /tmp/rclone.zip -d /tmp \
    && install -m 0755 "/tmp/rclone-${RELEASE_RCLONE}-linux-amd64/rclone" /usr/local/bin/rclone \
    && rm -rf /tmp/rclone.zip "/tmp/rclone-${RELEASE_RCLONE}-linux-amd64" \
    && apt-get purge -y curl unzip \
    && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/* \
    && rclone version

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# data/ приходит volume'ом (./data), конфиг rclone — каталогом (./rclone/rclone.conf)
ENV RCLONE_CONFIG=/config/rclone/rclone.conf
RUN mkdir -p /app/data /config/rclone

# Процесс работает от root: ./data Docker создаёт на хосте от root, а rclone/rclone.conf после
# `rclone config` принадлежит root с правами 0600 — непривилегированный пользователь не смог бы
# ни писать базу, ни обновлять токен. Контейнер наружу портов не открывает.

CMD ["python", "-m", "bot.main"]
