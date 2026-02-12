# SK MNÍŠECKO – Django aplikace

## Rychlý start (lokální)

1. Vytvořte a aktivujte venv:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Nainstalujte závislosti:

```bash
pip install -r requirements.txt
```

3. Migrace databáze:

```bash
python manage.py makemigrations
python manage.py migrate
```

4. Základní sporty:

```bash
python manage.py seed_sports
```

5. Admin účet:

```bash
python manage.py createsuperuser
```

6. Spuštění serveru:

```bash
python manage.py runserver
```

Aplikace poběží na `http://127.0.0.1:8000/`.

## PostgreSQL (produkce)

Pokud chcete použít PostgreSQL, nastavte prostředí:

```
POSTGRES_DB=skmnisecko
POSTGRES_USER=...
POSTGRES_PASSWORD=...
POSTGRES_HOST=...
POSTGRES_PORT=5432
```

Bez těchto proměnných se použije SQLite.
