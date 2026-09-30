#!/bin/bash
# Запуск на Mac: двойной клик по файлу или команда  bash start.command
cd "$(dirname "$0")" || exit 1

pause() { read -n 1 -s -r -p "Нажмите любую клавишу, чтобы закрыть окно..."; echo; }

if ! command -v python3 >/dev/null 2>&1; then
  echo "Не найден Python 3."
  echo "Установите его с https://www.python.org/downloads/macos/ и запустите этот файл снова."
  pause; exit 1
fi

if [ ! -d .venv ]; then
  echo "Первый запуск: создаю окружение (1–2 минуты)..."
  python3 -m venv .venv || { echo "Не удалось создать окружение."; pause; exit 1; }
fi

source .venv/bin/activate
pip install -q -r requirements.txt || { echo "Не удалось установить зависимости. Проверьте интернет."; pause; exit 1; }

echo
echo "Запускаю сервер..."

# Ждём, пока сервер РЕАЛЬНО начнёт отвечать, и только потом открываем браузер —
# вместо того чтобы открывать его через фиксированную паузу и гадать, успел ли сервер стартовать.
# Адрес 127.0.0.1 указан явно: Safari иногда не может разрешить "localhost".
(
  for i in $(seq 1 60); do
    if curl -s -o /dev/null "http://127.0.0.1:8000/healthz" 2>/dev/null; then
      echo "Готово: панель открывается на http://127.0.0.1:8000"
      command -v open >/dev/null 2>&1 && open "http://127.0.0.1:8000"
      exit 0
    fi
    sleep 0.5
  done
  echo "Сервер не ответил за 30 секунд. Откройте http://127.0.0.1:8000 вручную, когда будете готовы,"
  echo "или пришлите мне то, что написано выше в этом окне."
) &

python run.py
pause
