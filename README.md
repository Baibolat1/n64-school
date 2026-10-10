# Школа — закрытый сайт для учеников

Закрытый веб-сайт для учеников школы: доступ по учётным записям, контент виден только
авторизованным пользователям. Проект находится на раннем этапе разработки.

## Стек

- Python 3
- FastAPI + Uvicorn
- SQLAlchemy 2.x + Alembic
- Pydantic Settings
- Jinja2 (шаблоны)

## Окружение

Создать виртуальное окружение и установить зависимости:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Скопировать пример настроек и при необходимости отредактировать:

```bash
cp .env.example .env
```

Переменные окружения (читаются из `.env`):

- `APP_NAME` — название приложения (заголовок FastAPI)
- `SECRET_KEY` — секретный ключ для подписи сессий и токенов
- `DATABASE_URL` — строка подключения к БД (по умолчанию `sqlite:///./school.db`)
- `DEBUG` — режим отладки

## Запуск

```bash
.venv/bin/uvicorn app.main:app --reload
```

Проверка работоспособности: `GET /health` возвращает `{"status": "ok"}`.

## Миграции

```bash
.venv/bin/alembic upgrade head
```

Создание новой миграции:

```bash
.venv/bin/alembic revision --autogenerate -m "описание"
```
