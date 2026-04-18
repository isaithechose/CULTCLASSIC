from .base import *
import os
from decouple import config
import stripe

# =========================
# SEGURIDAD / ENTORNO
# =========================
SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)

ALLOWED_HOSTS = config(
    "ALLOWED_HOSTS",
    default="cultclassics.shop,www.cultclassics.shop,187.124.250.115"
).split(",")

CSRF_TRUSTED_ORIGINS = config(
    "CSRF_TRUSTED_ORIGINS",
    default="https://cultclassics.shop,https://www.cultclassics.shop"
).split(",")

# Si estás detrás de proxy/SSL en Nginx
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Recomendado en producción
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "SAMEORIGIN"

# HSTS (actívalo solo si ya confirmaste que HTTPS funciona bien)
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=31536000, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True


# =========================
# APPS
# =========================
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

SITE_ID = config("SITE_ID", default=4, cast=int)


# =========================
# AUTH / ALLAUTH
# =========================
AUTHENTICATION_BACKENDS = [
    "django.contrib.auth.backends.ModelBackend",
    "allauth.account.auth_backends.AuthenticationBackend",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # sirve estáticos en producción
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "allauth.account.middleware.AccountMiddleware",
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
        "METHOD": "oauth2",
        "SCOPE": ["email", "public_profile"],
        "FIELDS": ["id", "email", "name", "first_name", "last_name"],
        "VERIFIED_EMAIL": False,
        "VERSION": "v19.0",
        "APP": {
            "client_id": config("FACEBOOK_CLIENT_ID", default=""),
            "secret": config("FACEBOOK_CLIENT_SECRET", default=""),
            "key": "",
        },
    },
}


# =========================
# STRIPE
# =========================
STRIPE_PUBLIC_KEY = config("STRIPE_PUBLIC_KEY", default="")
STRIPE_SECRET_KEY = config("STRIPE_SECRET_KEY", default="")
META_PIXEL_ID = config("META_PIXEL_ID", default="")
stripe.api_key = STRIPE_SECRET_KEY


# =========================
# SKYDROPX
# =========================
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


# =========================
# TEMPLATES
# =========================
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR.parent / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "tienda.context_processors.meta_pixel",
            ],
        },
    },
]


# =========================
# BASE DE DATOS
# =========================
DATABASES = {
    "default": {
        "ENGINE": config("DB_ENGINE", default="django.db.backends.sqlite3"),
        "NAME": config("DB_NAME", default=str(BASE_DIR / "db.sqlite3")),
        "USER": config("DB_USER", default=""),
        "PASSWORD": config("DB_PASSWORD", default=""),
        "HOST": config("DB_HOST", default=""),
        "PORT": config("DB_PORT", default=""),
    }
}


# =========================
# ARCHIVOS ESTÁTICOS Y MEDIA
# =========================
STATIC_URL = "/static/"
STATICFILES_DIRS = [
    BASE_DIR.parent / "static",
]
STATIC_ROOT = BASE_DIR.parent / "staticfiles"

STATICFILES_STORAGE = "whitenoise.storage.CompressedStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR.parent / "media"


# =========================
# EMAIL
# =========================
EMAIL_BACKEND = config(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.smtp.EmailBackend"
)
EMAIL_HOST = config("EMAIL_HOST", default="smtp.gmail.com")
EMAIL_PORT = config("EMAIL_PORT", default=587, cast=int)
EMAIL_USE_TLS = config("EMAIL_USE_TLS", default=True, cast=bool)
EMAIL_HOST_USER = config("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = config("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = config("DEFAULT_FROM_EMAIL", default=EMAIL_HOST_USER)


# =========================
# JAZZMIN
# =========================
JAZZMIN_SETTINGS = {
    "site_title": "Cult Classics Admin",
    "site_header": "Cult Classics Admin",
    "site_brand": "Cult Classics",
    "site_logo": "images/logo.png",
    "login_logo": "images/logo.png",
    "login_logo_dark": None,
    "site_logo_classes": "img-circle",
    "welcome_sign": "Panel de control para catalogo, pedidos y operacion diaria.",
    "copyright": "Cult Classics",
    "search_model": ["tienda.Producto", "tienda.ProductVariant", "tienda.Order", "tienda.Expense", "auth.User"],
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
        "tienda.ProductVariant": "fas fa-tags",
        "tienda.InventoryMovement": "fas fa-boxes-stacked",
        "tienda.ExpenseCategory": "fas fa-folder-tree",
        "tienda.Expense": "fas fa-wallet",
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
        "tienda.ProductVariant",
        "tienda.InventoryMovement",
        "tienda.ExpenseCategory",
        "tienda.Expense",
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
        }, {
            "name": "Inventario",
            "url": "/admin/tienda/producto/inventory-matrix/",
            "icon": "fas fa-boxes-stacked",
            "permissions": ["tienda.view_producto"],
        }, {
            "name": "Variantes",
            "url": "/admin/tienda/productvariant/",
            "icon": "fas fa-tags",
            "permissions": ["tienda.view_productvariant"],
        }, {
            "name": "Movimientos inventario",
            "url": "/admin/tienda/inventorymovement/",
            "icon": "fas fa-right-left",
            "permissions": ["tienda.view_inventorymovement"],
        }, {
            "name": "Dashboard contable",
            "url": "/admin/tienda/expense/accounting-dashboard/",
            "icon": "fas fa-chart-line",
            "permissions": ["tienda.view_expense"],
        }, {
            "name": "Gastos",
            "url": "/admin/tienda/expense/",
            "icon": "fas fa-wallet",
            "permissions": ["tienda.view_expense"],
        }]
    },
}


# =========================
# LOGGING BÁSICO
# =========================
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}
