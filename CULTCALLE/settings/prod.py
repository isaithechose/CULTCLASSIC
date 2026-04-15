from .base import *

import os
from decouple import config


DEBUG = False

SECRET_KEY = config("SECRET_KEY", default=SECRET_KEY)

ALLOWED_HOSTS = config(
    "ALLOWED_HOSTS",
    default="127.0.0.1,localhost"
).split(",")

CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in config("CSRF_TRUSTED_ORIGINS", default="").split(",")
    if origin.strip()
]

INSTALLED_APPS = [
    "jazzmin",
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    "allauth",
    "allauth.account",
    "allauth.socialaccount",
    "allauth.socialaccount.providers.google",
    "allauth.socialaccount.providers.facebook",
    "tienda",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
]

SITE_ID = config("SITE_ID", default=4, cast=int)

AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

LOGIN_REDIRECT_URL = "tienda:tienda"
LOGOUT_REDIRECT_URL = "tienda:tienda"

ACCOUNT_LOGOUT_ON_GET = True
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_AUTHENTICATION_METHOD = "email"
ACCOUNT_EMAIL_VERIFICATION = "none"
SOCIALACCOUNT_LOGIN_ON_GET = True

SOCIALACCOUNT_PROVIDERS = {
    "google": {
        "APP": {
            "client_id": config("GOOGLE_CLIENT_ID", default=""),
            "secret": config("GOOGLE_CLIENT_SECRET", default=""),
            "key": "",
        },
        "SCOPE": ["email", "profile"],
        "AUTH_PARAMS": {"access_type": "online"},
        "OAUTH_PKCE_ENABLED": True,
    },
    "facebook": {
        "APP": {
            "client_id": config("FACEBOOK_CLIENT_ID", default=""),
            "secret": config("FACEBOOK_CLIENT_SECRET", default=""),
            "key": "",
        },
        "METHOD": "oauth2",
        "SCOPE": ["email", "public_profile"],
        "FIELDS": ["id", "email", "name", "first_name", "last_name"],
        "VERIFIED_EMAIL": False,
        "VERSION": "v19.0",
    },
}

STRIPE_PUBLIC_KEY = config("STRIPE_PUBLIC_KEY", default="")
STRIPE_SECRET_KEY = config("STRIPE_SECRET_KEY", default="")
SKYDROP_CLIENT_ID = config("SKYDROP_CLIENT_ID", default="")
SKYDROP_CLIENT_SECRET = config("SKYDROP_CLIENT_SECRET", default="")
SKYDROP_API_BASE_URL = config("SKYDROP_API_BASE_URL", default="https://pro.skydropx.com")
SKYDROP_WEBHOOK_SECRET = config("SKYDROP_WEBHOOK_SECRET", default="")
SKYDROP_PRINTING_FORMAT = config("SKYDROP_PRINTING_FORMAT", default="standard")
SKYDROP_DEFAULT_PHONE = config("SKYDROP_DEFAULT_PHONE", default="")
SKYDROP_DEFAULT_EMAIL = config("SKYDROP_DEFAULT_EMAIL", default="")
SKYDROP_DEFAULT_PARCEL_WEIGHT = config("SKYDROP_DEFAULT_PARCEL_WEIGHT", default=0.8, cast=float)
SKYDROP_DEFAULT_PARCEL_LENGTH = config("SKYDROP_DEFAULT_PARCEL_LENGTH", default=35, cast=int)
SKYDROP_DEFAULT_PARCEL_WIDTH = config("SKYDROP_DEFAULT_PARCEL_WIDTH", default=28, cast=int)
SKYDROP_DEFAULT_PARCEL_HEIGHT = config("SKYDROP_DEFAULT_PARCEL_HEIGHT", default=6, cast=int)
SKYDROP_DISTANCE_UNIT = config("SKYDROP_DISTANCE_UNIT", default="CM")
SKYDROP_MASS_UNIT = config("SKYDROP_MASS_UNIT", default="KG")
SKYDROP_ORIGIN_NAME = config("SKYDROP_ORIGIN_NAME", default="Cult Classics")
SKYDROP_ORIGIN_COMPANY = config("SKYDROP_ORIGIN_COMPANY", default="Cult Classics")
SKYDROP_ORIGIN_PHONE = config("SKYDROP_ORIGIN_PHONE", default="")
SKYDROP_ORIGIN_EMAIL = config("SKYDROP_ORIGIN_EMAIL", default="")
SKYDROP_ORIGIN_STREET1 = config("SKYDROP_ORIGIN_STREET1", default="")
SKYDROP_ORIGIN_STREET2 = config("SKYDROP_ORIGIN_STREET2", default="")
SKYDROP_ORIGIN_REFERENCE = config("SKYDROP_ORIGIN_REFERENCE", default="")
SKYDROP_ORIGIN_POSTAL_CODE = config("SKYDROP_ORIGIN_POSTAL_CODE", default="")
SKYDROP_ORIGIN_STATE = config("SKYDROP_ORIGIN_STATE", default="")
SKYDROP_ORIGIN_CITY = config("SKYDROP_ORIGIN_CITY", default="")
SKYDROP_ORIGIN_NEIGHBORHOOD = config("SKYDROP_ORIGIN_NEIGHBORHOOD", default="")
SKYDROP_ORIGIN_COUNTRY_CODE = config("SKYDROP_ORIGIN_COUNTRY_CODE", default="MX")

EMAIL_BACKEND = config(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = config("EMAIL_HOST", default="smtp.gmail.com")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default=EMAIL_HOST_USER or "no-reply@localhost")

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR.parent / "static"]
STATIC_ROOT = BASE_DIR.parent / "staticfiles"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SESSION_COOKIE_SECURE = config("SESSION_COOKIE_SECURE", default=True, cast=bool)
CSRF_COOKIE_SECURE = config("CSRF_COOKIE_SECURE", default=True, cast=bool)
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=False, cast=bool)

JAZZMIN_SETTINGS = {
    "site_title": "Cult Clasiccs Admin",
    "site_header": "Cult Clasiccs Admin",
    "site_brand": "Cult Clasiccs",
    "site_logo": "images/logo.png",
    "login_logo": "images/logo.png",
    "login_logo_dark": None,
    "site_logo_classes": "img-circle",
    "welcome_sign": "Panel de control para catalogo, pedidos y operacion diaria.",
    "copyright": "Cult Clasiccs",
    "search_model": ["tienda.Producto", "tienda.Order", "auth.User"],
    "topmenu_links": [
        {"name": "Inicio", "url": "/", "permissions": ["auth.view_user"]},
        {"model": "tienda.Producto"},
        {"model": "tienda.Order"},
        {"model": "auth.User"},
    ],
    "usermenu_links": [
        {"name": "Ver tienda", "url": "/", "new_window": True},
    ],
    "icons": {
        "auth": "fas fa-users-cog",
        "auth.user": "fas fa-user",
        "auth.Group": "fas fa-users",
        "sites.Site": "fas fa-globe",
        "socialaccount.SocialApp": "fas fa-share-alt",
        "tienda.Categoria": "fas fa-layer-group",
        "tienda.Subcategoria": "fas fa-sitemap",
        "tienda.Producto": "fas fa-shirt",
        "tienda.Order": "fas fa-bag-shopping",
        "tienda.OrderItem": "fas fa-box-open",
        "tienda.Carrito": "fas fa-cart-shopping",
        "tienda.Reseña": "fas fa-star",
        "tienda.ShippingAddress": "fas fa-location-dot",
        "tienda.ShippingUpdate": "fas fa-truck-fast",
    },
    "order_with_respect_to": [
        "auth",
        "sites",
        "socialaccount",
        "tienda",
        "tienda.Categoria",
        "tienda.Subcategoria",
        "tienda.Producto",
        "tienda.Order",
        "tienda.Carrito",
        "tienda.Reseña",
    ],
    "navigation_expanded": True,
    "hide_apps": [],
    "custom_links": {
        "tienda": [{
            "name": "Pedidos pendientes",
            "url": "/admin/tienda/order/?status__exact=Pending",
            "icon": "fas fa-clock",
            "permissions": ["tienda.view_order"],
        }, {
            "name": "Productos sin stock",
            "url": "/admin/tienda/producto/?stock__exact=0",
            "icon": "fas fa-triangle-exclamation",
            "permissions": ["tienda.view_producto"],
        }]
    },
}
