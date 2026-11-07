# KotoLancer

Локальная мастерская для ведения заказов: карточки, входящие материалы, архив и корзина. Дополнительные интеграции: Telegram и Mini App, анализ заказов через OpenAI и чтение репозиториев GitHub.

Этот снимок предназначен для локального запуска. Начальный профиль содержит вымышленные примеры; его нужно настроить под себя. Внутренние инструкции выпуска и эксплуатации, данные заказов и история закрытого репозитория сюда не входят.

## Локальный запуск

Нужны Python 3.12+ и Node.js с npm. Сначала создай и активируй виртуальное окружение Python: `python -m venv .venv`, затем в Windows PowerShell `.venv\Scripts\Activate.ps1` или в Linux/macOS `source .venv/bin/activate`. Скопируй `.env.example` в `.env`. Для PostgreSQL запусти `docker compose up -d postgres`. Для локальной SQLite вместо этого укажи `KOTO_DATABASE_URL=sqlite:///data/kotolancer.db` и создай папку `data`.

```sh
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
npm --prefix frontend ci
npm --prefix frontend run build
python -m kotolancer.manage init
python -m uvicorn kotolancer.app:app --host 127.0.0.1 --port 8010 --no-proxy-headers
```

Открой http://127.0.0.1:8010. Локальный режим не требует ключей; держи его на loopback. Для дополнительных интеграций заполни настройки из `.env.example` своими значениями. Для GitHub укажи своего владельца репозиториев. Не добавляй `.env` и рабочие данные в Git.

Проверки: `python -m pytest -q`, `npm --prefix frontend test`, `npm --prefix frontend run build`. Тесты внутреннего развёртывания, резервного копирования и платных прогонов не включены.

Лицензия на использование и распространение пока не выбрана владельцем.
