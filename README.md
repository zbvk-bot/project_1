# Агрегатор access-логов Apache

Приложение читает access-логи, складывает их в PostgreSQL и даёт посмотреть данные из консоли или браузера.

Нужны Python 3.10+ и PostgreSQL (по умолчанию `localhost:5432`, пользователь `postgres` / `postgres` — правится в `config.ini`).

При первом запуске сам создаётся `config.ini` и папка с примером лога, если своих файлов ещё нет. Базу `apache_logs` тоже попробует создать, если PostgreSQL уже запущен.

## Как запустить

```bash
pip install -r requirements.txt
python main.py register admin
python main.py parse
python main.py serve
```

После `register admin` логин и пароль для входа — `admin` / `admin`.

Сайт: http://127.0.0.1:8080/

Логи можно добавить двумя способами:

1. **Веб** — на панели загрузить файл (XHR + прогресс), затем разбор в БД (WebSocket + прогресс).
2. **Файловая система / CLI** — положить в каталог из конфига (`logs` → `directory`, обычно `data/sample_logs`), маска — `access*.log`, затем `python main.py parse`.

Лимит размера загрузки и число строк превью — в `config.ini`, секция `[upload]`.

## Команды

- `register [имя]` — добавить пользователя в БД
- `parse` — разобрать логи и записать в базу
- `view` — посмотреть данные в терминале (фильтры ниже)
- `serve` — веб-интерфейс
- `cron` — то же, что `parse`, удобно повесить на расписание

Другой конфиг: `python main.py -c путь\к\config.ini parse`

### Просмотр в консоли

```bash
python main.py view
python main.py view --date-from 2024-10-10 --date-to 2024-10-12
python main.py view --ip 192.168.1.10
python main.py view --group-by ip
python main.py view --group-by date
```

## Cron

Раз в час, например:

```bash
python main.py cron
```

В Linux это можно добавить в crontab, в Windows — в «Планировщик заданий».
