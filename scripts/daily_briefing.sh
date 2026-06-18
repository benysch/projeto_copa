#!/bin/bash
# Wrapper do cron: envia o briefing diário da Copa no Telegram.
cd /home/murie/projetos/projeto_copa || exit 1
exec .venv/bin/python -m src.daily_briefing
