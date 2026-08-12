#!/usr/bin/env bash
#
# Подготовка бота к работе одной командой:
#     bash setup.sh
#
# Скрипт проверяет Python, ставит библиотеки в отдельное окружение,
# убеждается, что .env на месте, и проверяет ключи X.
# Останавливается на первом же несоответствии и говорит, что чинить.

set -u

cd "$(dirname "$0")"

BOLD=$(printf '\033[1m'); DIM=$(printf '\033[2m')
RED=$(printf '\033[31m'); GREEN=$(printf '\033[32m'); YELLOW=$(printf '\033[33m')
OFF=$(printf '\033[0m')

step() { printf '\n%s==> %s%s\n' "$BOLD" "$1" "$OFF"; }
ok()   { printf '%s  ✓ %s%s\n' "$GREEN" "$1" "$OFF"; }
warn() { printf '%s  ! %s%s\n' "$YELLOW" "$1" "$OFF"; }
die()  { printf '\n%s  ✗ %s%s\n\n' "$RED" "$1" "$OFF"; exit 1; }

# ---------------------------------------------------------------- 1. Python

step "Проверяю Python"

PY=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || die "Python не найден. Поставь его с https://www.python.org/downloads/"

if ! "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    die "Нужен Python 3.10 или новее, а найден $("$PY" --version 2>&1). Обнови с python.org"
fi
ok "$("$PY" --version 2>&1)"

# ------------------------------------------------------------ 2. Окружение

step "Готовлю окружение и ставлю библиотеки"

if [ ! -d .venv ]; then
    "$PY" -m venv .venv || die "Не смог создать окружение .venv"
    ok "окружение .venv создано"
else
    ok "окружение .venv уже есть"
fi

VENV_PY=".venv/bin/python"
[ -x "$VENV_PY" ] || VENV_PY=".venv/Scripts/python.exe"   # Windows / Git Bash
[ -x "$VENV_PY" ] || die "В .venv нет интерпретатора. Удали папку .venv и запусти скрипт заново."

"$VENV_PY" -m pip install --quiet --upgrade pip
"$VENV_PY" -m pip install --quiet -r requirements.txt || die "Не смог установить библиотеки из requirements.txt"
ok "библиотеки установлены"

# ----------------------------------------------------------------- 3. .env

step "Ищу файл .env"

if [ ! -f .env ]; then
    printf '\n%s  ✗ Файла .env нет рядом с main.py.%s\n\n' "$RED" "$OFF"
    printf '  Положи его в эту папку:\n    %s%s%s\n\n' "$DIM" "$(pwd)" "$OFF"
    printf '  Если файла нет вовсе — скопируй шаблон и заполни ключами:\n'
    printf '    %scp .env.example .env%s\n\n' "$DIM" "$OFF"
    exit 1
fi
ok ".env на месте"

if [ -f .env.txt ]; then
    warn "рядом лежит .env.txt — похоже, Windows дописал расширение. Нужен именно .env"
fi

# --------------------------------------------------------------- 4. Ключи

step "Проверяю ключи X"

if "$VENV_PY" main.py check; then
    printf '\n%s==> Готово. Бот настроен.%s\n\n' "$BOLD" "$OFF"
    printf '  Дальше:\n'
    printf '    %s%s main.py preview%s   показать следующий пост\n' "$DIM" "$VENV_PY" "$OFF"
    printf '    %s%s main.py once%s      опубликовать один пост\n' "$DIM" "$VENV_PY" "$OFF"
    printf '    %s%s main.py run%s       постить по расписанию, пока открыт терминал\n\n' "$DIM" "$VENV_PY" "$OFF"
    printf '  Пока в .env стоит DRY_RUN=true, твит НЕ отправляется — это безопасно.\n'
    printf '  Когда всё понравится, поменяй на DRY_RUN=false.\n\n'
else
    printf '\n%s  ✗ Ключи не прошли проверку — смотри сообщение выше.%s\n\n' "$RED" "$OFF"
    printf '  Разбор частых причин: раздел «Если что-то не работает» в README.md\n\n'
    exit 1
fi
