# Deploy en Hostinger VPS (Ubuntu)

Esta guía deja la tienda Django corriendo con `gunicorn` + `nginx`.

## 1. Conectarte al VPS

```bash
ssh root@TU_IP
```

## 2. Instalar paquetes base

```bash
apt update && apt upgrade -y
apt install -y python3 python3-venv python3-pip nginx git
```

## 3. Clonar el proyecto

```bash
cd /var/www
git clone https://github.com/isaithechose/CULTCLASSIC.git
cd CULTCLASSIC
```

## 4. Crear entorno virtual e instalar dependencias

```bash
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

## 5. Crear archivo `.env`

```bash
nano .env
```

Pega algo como esto:

```env
SECRET_KEY=pon_aqui_tu_secret_key_real
ALLOWED_HOSTS=TU_DOMINIO,www.TU_DOMINIO,TU_IP
CSRF_TRUSTED_ORIGINS=https://TU_DOMINIO,https://www.TU_DOMINIO
SITE_ID=4

STRIPE_PUBLIC_KEY=
STRIPE_SECRET_KEY=

GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
FACEBOOK_CLIENT_ID=
FACEBOOK_CLIENT_SECRET=

EMAIL_BACKEND=django.core.mail.backends.smtp.EmailBackend
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USE_TLS=True
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
DEFAULT_FROM_EMAIL=

DJANGO_SETTINGS_MODULE=CULTCALLE.settings.prod
SECURE_SSL_REDIRECT=False
SESSION_COOKIE_SECURE=False
CSRF_COOKIE_SECURE=False
```

Nota:
- Antes de activar SSL con tu dominio, deja `SECURE_SSL_REDIRECT=False`
- Cuando ya tengas HTTPS funcionando, cambia:
  - `SECURE_SSL_REDIRECT=True`
  - `SESSION_COOKIE_SECURE=True`
  - `CSRF_COOKIE_SECURE=True`

## 6. Migraciones y archivos estáticos

```bash
source venv/bin/activate
python manage.py migrate
python manage.py collectstatic --noinput
python manage.py createsuperuser
```

## 7. Probar gunicorn

```bash
source venv/bin/activate
gunicorn --bind 0.0.0.0:8000 CULTCALLE.wsgi:application
```

Si carga sin error, detén con `Ctrl + C`.

## 8. Crear servicio systemd

```bash
nano /etc/systemd/system/cultclasiccs.service
```

Contenido:

```ini
[Unit]
Description=Cult Clasiccs Django App
After=network.target

[Service]
User=root
Group=www-data
WorkingDirectory=/var/www/CULTCLASSIC
Environment="DJANGO_SETTINGS_MODULE=CULTCALLE.settings.prod"
ExecStart=/var/www/CULTCLASSIC/venv/bin/gunicorn \
  --workers 3 \
  --bind 127.0.0.1:8000 \
  CULTCALLE.wsgi:application

[Install]
WantedBy=multi-user.target
```

Activar:

```bash
systemctl daemon-reload
systemctl enable cultclasiccs
systemctl start cultclasiccs
systemctl status cultclasiccs
```

## 9. Configurar Nginx

```bash
nano /etc/nginx/sites-available/cultclasiccs
```

Contenido:

```nginx
server {
    listen 80;
    server_name TU_DOMINIO www.TU_DOMINIO TU_IP;

    location /static/ {
        alias /var/www/CULTCLASSIC/staticfiles/;
    }

    location /media/ {
        alias /var/www/CULTCLASSIC/CULTCALLE/media/;
    }

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

Activar sitio:

```bash
ln -s /etc/nginx/sites-available/cultclasiccs /etc/nginx/sites-enabled/
nginx -t
systemctl restart nginx
```

## 10. SSL con Let's Encrypt

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d TU_DOMINIO -d www.TU_DOMINIO
```

Después de SSL:
- cambia en `.env`:
  - `SECURE_SSL_REDIRECT=True`
  - `SESSION_COOKIE_SECURE=True`
  - `CSRF_COOKIE_SECURE=True`

Luego reinicia:

```bash
systemctl restart cultclasiccs
systemctl restart nginx
```

## 11. Cada vez que actualices desde GitHub

```bash
cd /var/www/CULTCLASSIC
git pull origin main
source venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py collectstatic --noinput
systemctl restart cultclasiccs
systemctl restart nginx
```
