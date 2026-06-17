#!/usr/bin/env bash
# Запуск домашнего облака (Linux/macOS)
set -e
cd "$(dirname "$0")"

# Создаём виртуальное окружение, если его нет
if [ ! -d "venv" ]; then
    echo "Создаю виртуальное окружение..."
    python3 -m venv venv
fi

# Активируем и ставим зависимости
source venv/bin/activate
pip install -q -r requirements.txt

echo ""
echo "================================================================"
echo "  🚀 Запускаю домашнее облако..."
echo "================================================================"
echo ""
python app.py
