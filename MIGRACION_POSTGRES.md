# Migración de SQLite a PostgreSQL (VPS en producción)

Producción corre hoy con SQLite y 3 workers de gunicorn. Bajo concurrencia eso
provoca errores `database is locked` y empeora condiciones de carrera en el
checkout. PostgreSQL lo resuelve. El código ya soporta Postgres vía variables de
entorno (`DB_ENGINE`, `DB_NAME`, etc. en `settings/prod.py`); solo falta instalar
el motor y migrar los datos.

> ⚠️ **Esto toca datos reales (pedidos, contabilidad).** Hazlo en una ventana de
> mantenimiento, con el sitio detenido durante el volcado, y **conserva siempre el
> respaldo de SQLite** hasta confirmar que todo quedó bien.

## 0. Respaldo previo (imprescindible)

```bash
cd /var/www/CULTCLASSIC
cp db.sqlite3 db.sqlite3.bak_$(date +%F_%H%M)
```

## 1. Instalar PostgreSQL en el VPS

```bash
apt update
apt install -y postgresql postgresql-contrib libpq-dev
```

## 2. Crear base de datos y usuario

```bash
sudo -u postgres psql
```

Dentro de `psql` (cambia la contraseña):

```sql
CREATE DATABASE cultclassics;
CREATE USER cultuser WITH PASSWORD 'PON_UNA_CONTRASEÑA_FUERTE';
ALTER ROLE cultuser SET client_encoding TO 'utf8';
ALTER ROLE cultuser SET default_transaction_isolation TO 'read committed';
ALTER ROLE cultuser SET timezone TO 'America/Mexico_City';
GRANT ALL PRIVILEGES ON DATABASE cultclassics TO cultuser;
\q
```

En PostgreSQL 15+ el usuario también necesita permiso sobre el esquema `public`:

```bash
sudo -u postgres psql -d cultclassics -c "GRANT ALL ON SCHEMA public TO cultuser;"
```

## 3. Instalar el driver de Python

```bash
cd /var/www/CULTCLASSIC
git pull origin main          # trae psycopg2-binary en requirements.txt
source venv/bin/activate
pip install -r requirements.txt
```

## 4. Detener el sitio y volcar los datos desde SQLite

Con el `.env` TODAVÍA apuntando a SQLite (sin variables `DB_*`):

```bash
systemctl stop cultclasiccs          # nadie escribe durante el volcado
source venv/bin/activate

python manage.py dumpdata \
  --natural-foreign --natural-primary \
  -e contenttypes -e auth.permission -e admin.logentry -e sessions.session \
  --indent 2 -o datadump.json
```

Excluimos `contenttypes` y `auth.permission` porque Django los recrea solo; si no
los excluyes, `loaddata` choca con llaves duplicadas.

## 5. Apuntar `.env` a PostgreSQL

```bash
nano .env
```

Agrega (ajusta la contraseña a la del paso 2):

```env
DB_ENGINE=django.db.backends.postgresql
DB_NAME=cultclassics
DB_USER=cultuser
DB_PASSWORD=PON_UNA_CONTRASEÑA_FUERTE
DB_HOST=127.0.0.1
DB_PORT=5432
```

## 6. Crear el esquema y cargar los datos en Postgres

```bash
source venv/bin/activate
python manage.py migrate                 # crea las tablas vacías en Postgres
python manage.py loaddata datadump.json  # carga tus datos reales
```

Si `loaddata` se queja de algún registro de `contenttypes`, vuelve a generar el
dump del paso 4 confirmando los `-e` y reintenta sobre la base recién migrada.

## 7. Reactivar el sitio y verificar

```bash
python manage.py check
systemctl start cultclasiccs
systemctl restart nginx
systemctl status cultclasiccs
```

Comprobaciones rápidas:
- Entra al admin y confirma que aparecen pedidos, productos y pólizas contables.
- Haz un pedido de prueba completo (checkout → pago de prueba) y revisa que el
  stock se descuente una sola vez.
- Revisa logs: `journalctl -u cultclasiccs -n 50 --no-pager`.

## 8. Limpieza (solo cuando todo esté confirmado)

```bash
rm datadump.json          # contiene datos del negocio: no lo dejes en el repo
```

Conserva `db.sqlite3.bak_*` unos días como red de seguridad.

## Rollback

Si algo sale mal, vuelve a SQLite sin perder nada:

1. `nano .env` → borra/comenta las variables `DB_*`.
2. `systemctl restart cultclasiccs`.

El sitio vuelve a usar `db.sqlite3` (tu base original intacta).

---

### Nota sobre respaldos a futuro (Postgres)

Una vez en Postgres, programa un respaldo diario:

```bash
pg_dump -U cultuser -h 127.0.0.1 cultclassics | gzip > /root/backups/cult_$(date +%F).sql.gz
```

(Agrega esa línea a un cron diario y rota los archivos viejos.)
