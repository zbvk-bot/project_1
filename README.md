## Как запустить

```bash
pip install -r requirements.txt
python main.py register admin
python main.py parse
python main.py serve
```

После `register admin` логин и пароль для входа — `admin` / `admin`.

Сайт: http://127.0.0.1:8080/

## Команды

- `register [имя]` — добавить пользователя в БД
- `parse` — разобрать логи и записать в базу
- `view` — посмотреть данные в терминале (фильтры ниже)
- `serve` — веб-интерфейс
- `cron` — то же, что `parse`, удобно повесить на расписание

### Просмотр в консоли

```bash
python main.py view
python main.py view --date-from 2024-10-10 --date-to 2024-10-12
python main.py view --ip 192.168.1.10
python main.py view --group-by ip
python main.py view --group-by date
```

## Cron

python main.py cron

