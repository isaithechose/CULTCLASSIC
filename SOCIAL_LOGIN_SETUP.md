# Social Login Setup

El proyecto ya usa `django-allauth` para Google y Facebook.

## Variables de entorno

Agrega estas variables en tu `.env`:

```env
GOOGLE_CLIENT_ID=tu_google_client_id
GOOGLE_CLIENT_SECRET=tu_google_client_secret
FACEBOOK_CLIENT_ID=tu_facebook_app_id
FACEBOOK_CLIENT_SECRET=tu_facebook_app_secret
```

## URLs de callback

Usa estas URLs en tus consolas de OAuth:

### Google

- Desarrollo local:
  - `http://127.0.0.1:8000/accounts/google/login/callback/`
  - `http://localhost:8000/accounts/google/login/callback/`

### Facebook

- Desarrollo local:
  - `http://127.0.0.1:8000/accounts/facebook/login/callback/`
  - `http://localhost:8000/accounts/facebook/login/callback/`

## Rutas de acceso

- Login principal: `/accounts/login/`
- Signup: `/accounts/signup/`
- Google login: `/accounts/google/login/`
- Facebook login: `/accounts/facebook/login/`

## Importante

- Reinicia el servidor después de cambiar `.env`.
- Si Google o Facebook muestran error de redirección, revisa que la URL coincida exactamente.
- Si Facebook no devuelve correo, revisa que el permiso `email` esté aprobado en tu app.
