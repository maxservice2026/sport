# Nasazeni na VPS (sportapp.softmax.cz / 81.95.108.17)

Tenhle projekt je Django + Gunicorn. Pro VPS doporucuji: `nginx` (reverse proxy) + `systemd` (service) + (idealne) PostgreSQL.

## 0) DNS
Ujisti se, ze `sportapp.softmax.cz` ma A zaznam na `81.95.108.17`.

## 1) Balicky (Ubuntu/Debian)
```bash
sudo apt update
sudo apt install -y git nginx python3-venv python3-dev build-essential
```

Pokud chces HTTPS pres Let's Encrypt:
```bash
sudo apt install -y certbot python3-certbot-nginx
```

## 2) Uzivatel a adresare
```bash
sudo adduser --disabled-password --gecos "" sportapp
sudo mkdir -p /srv/sportapp
sudo chown -R sportapp:sportapp /srv/sportapp
sudo mkdir -p /etc/sportapp
sudo chown -R root:root /etc/sportapp
sudo chmod 0750 /etc/sportapp
```

## 3) Nahrani kodu
Varianta A (git):
```bash
sudo -u sportapp git clone <REPO_URL> /srv/sportapp/app
```

Varianta B (rsync z lokalniho pocitace):
```bash
rsync -av --delete \
  --exclude .git --exclude .venv --exclude __pycache__ \
  "/Users/mpmp/Documents/SK Mnisecko app/" \
  sportapp@81.95.108.17:/srv/sportapp/app/
```

## 4) Virtualenv + zavislosti
```bash
sudo -u sportapp python3 -m venv /srv/sportapp/venv
sudo -u sportapp /srv/sportapp/venv/bin/pip install -U pip
sudo -u sportapp /srv/sportapp/venv/bin/pip install -r /srv/sportapp/app/requirements.txt
```

## 5) Produkcni konfigurace (.env pro systemd)
Vytvor `/etc/sportapp/sportapp.env` podle `deploy/vps/sportapp.env.example`.

Poznamky:
- `DJANGO_ALLOWED_HOSTS` nastav minimalne na `sportapp.softmax.cz`.
- `DJANGO_CSRF_TRUSTED_ORIGINS` nastav na `https://sportapp.softmax.cz`.
- Pokud pouzijes Postgres bez SSL na VPS, nastav `DJANGO_DB_SSL_REQUIRE=0`.

## 6) DB a prenos dat (zvol jednu variantu)

### Varianta 1: SQLite (nejjednodussi prenos DATA)
1. Na VPS nepoustej Postgres promenny (`DATABASE_URL`, `POSTGRES_*`) a app pojede na SQLite.
2. Prenes `db.sqlite3` do `/srv/sportapp/app/db.sqlite3` (a pripadnou slozku `media/`, pokud existuje).

Priklad (z lokalniho pocitace):
```bash
scp "/Users/mpmp/Documents/SK Mnisecko app/db.sqlite3" sportapp@81.95.108.17:/srv/sportapp/app/db.sqlite3
```

Pozor: pro SQLite doporucuji `GUNICORN_WORKERS=1` (kvuli zamykani DB).

### Varianta 2: PostgreSQL (doporuceno pro produkci)
Nainstaluj Postgres a zaloz DB/uzivatele (priklad):
```bash
sudo apt install -y postgresql
sudo -u postgres psql
```
```sql
CREATE DATABASE sportapp;
CREATE USER sportapp WITH PASSWORD 'CHANGE_ME';
ALTER ROLE sportapp SET client_encoding TO 'utf8';
ALTER ROLE sportapp SET default_transaction_isolation TO 'read committed';
ALTER ROLE sportapp SET timezone TO 'Europe/Prague';
GRANT ALL PRIVILEGES ON DATABASE sportapp TO sportapp;
```

Pak nastav v `/etc/sportapp/sportapp.env` bud `DATABASE_URL=...` nebo `POSTGRES_*`.

Pro presun dat ze SQLite do Postgres je nejcistsi varianta `dumpdata/loaddata` (muze vyzadovat doladeni podle realnych modelu).

## 7) Migrace + statiky (na VPS)
```bash
sudo -u sportapp /srv/sportapp/venv/bin/python /srv/sportapp/app/manage.py migrate --noinput
sudo -u sportapp /srv/sportapp/venv/bin/python /srv/sportapp/app/manage.py collectstatic --noinput
```

## 8) systemd (gunicorn)
1. Zkopiruj `deploy/vps/sportapp-gunicorn.service` do:
`/etc/systemd/system/sportapp-gunicorn.service`
2. Uprav cesty/uzivatele, pokud jsi je menil.
3. Aktivuj:
```bash
sudo systemctl daemon-reload
sudo systemctl enable --now sportapp-gunicorn
sudo systemctl status sportapp-gunicorn --no-pager
```

Logy:
```bash
journalctl -u sportapp-gunicorn -n 200 --no-pager
```

## 9) nginx
1. Zkopiruj `deploy/vps/nginx-sportapp.softmax.cz.conf` do:
`/etc/nginx/sites-available/sportapp.softmax.cz`
2. Zapni site:
```bash
sudo ln -s /etc/nginx/sites-available/sportapp.softmax.cz /etc/nginx/sites-enabled/sportapp.softmax.cz
sudo nginx -t
sudo systemctl reload nginx
```

## 10) HTTPS (certbot)
```bash
sudo certbot --nginx -d sportapp.softmax.cz
```

## 11) Overeni
```bash
curl -I http://sportapp.softmax.cz/
```

