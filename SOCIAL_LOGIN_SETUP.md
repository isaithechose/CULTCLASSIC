# Social Login Setup

El proyecto usa `django-allauth` para autenticación social.

Hoy el flujo que está realmente activo y alineado es:

- `Google` en local y producción
- `Facebook` pendiente de configuración completa

## Variables de entorno

Agrega estas variables en tu `.env`:

```env
GOOGLE_CLIENT_ID=tu_google_client_id
GOOGLE_CLIENT_SECRET=tu_google_client_secret
FACEBOOK_CLIENT_ID=
FACEBOOK_CLIENT_SECRET=
```

En desarrollo local, Google se toma desde `CULTCALLE/settings/local.py`.
En producción, la configuración se resuelve desde `CULTCALLE/settings/prod.py` y/o `SocialApp` en admin.

## URLs de callback

Usa estas URLs en tus consolas OAuth.

### Google

#### Desarrollo local

- `http://127.0.0.1:8000/accounts/google/login/callback/`
- `http://localhost:8000/accounts/google/login/callback/`

#### Producción

- `https://cultclassics.shop/accounts/google/login/callback/`
- `https://www.cultclassics.shop/accounts/google/login/callback/`

### Google Authorized JavaScript origins

#### Desarrollo local

- `http://127.0.0.1:8000`
- `http://localhost:8000`

#### Producción

- `https://cultclassics.shop`
- `https://www.cultclassics.shop`

### Facebook

Facebook no está terminado para local ni producción en este momento.
Si se vuelve a activar, entonces habrá que registrar también:

- `http://127.0.0.1:8000/accounts/facebook/login/callback/`
- `http://localhost:8000/accounts/facebook/login/callback/`
- `https://cultclassics.shop/accounts/facebook/login/callback/`
- `https://www.cultclassics.shop/accounts/facebook/login/callback/`

## Rutas de acceso

- Login principal: `/accounts/login/`
- Signup: `/accounts/signup/`
- Google login: `/accounts/google/login/`

Facebook no debe mostrarse en local mientras no tenga `SocialApp` o configuración completa.

## Importante

- Reinicia el servidor después de cambiar `.env`.
- Si Google muestra `redirect_uri_mismatch`, revisa que la URL coincida exactamente, incluyendo:
  - `http` o `https`
  - host (`127.0.0.1` vs `localhost`)
  - puerto `8000`
  - slash final `/`
- En local, evita mezclar dos fuentes de configuración para Google al mismo tiempo:
  - `SOCIALACCOUNT_PROVIDERS['google']['APP']`
  - `SocialApp` en base de datos

Debe existir solo una fuente activa por entorno.

- En producción, `Site` y `SocialApp` deben apuntar al dominio correcto.
- Si Google devuelve al callback pero termina en error de autenticación, revisa:
  - `SITE_ID`
  - `django_site`
  - `socialaccount_socialapp`
  - `socialaccount_socialapp_sites`
