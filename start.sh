#!/usr/bin/env bash
set -e

# 1) запускаем веб (порт для Render)
gunicorn -b 0.0.0.0:${PORT:-10000} web:app &

# 2) запускаем телеграм-бота (polling)
python bot.py
