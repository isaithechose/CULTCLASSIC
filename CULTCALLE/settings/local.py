from .base import *
# SECURITY WARNING: don't run with debug turned on in production!
import os
from decouple import config
import stripe


STRIPE_PUBLIC_KEY = config('STRIPE_PUBLIC_KEY', default='')
STRIPE_SECRET_KEY = config('STRIPE_SECRET_KEY', default='')
META_PIXEL_ID = config("META_PIXEL_ID", default="")
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

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

INSTALLED_APPS = [
    # Aplicaciones predeterminadas de Django
    'jazzmin',  # Admin personalizado (opcional)
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'tienda',  # Tu app principal

    # Apps necesarias para django-allauth
    'django.contrib.sites',  # Necesario para allauth
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    # Proveedores de autenticación (por ejemplo, Google o Facebook)
    'allauth.socialaccount.providers.google',  # Para Google
    'allauth.socialaccount.providers.facebook',  # Para Facebook
]

SITE_ID = config("SITE_ID", default=4, cast=int)



AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',  # Backend por defecto de Django
    'allauth.account.auth_backends.AuthenticationBackend',  # Backend de django-allauth
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',  # Middleware de allauth
]

# Redirecciones después del login/logout
LOGIN_REDIRECT_URL = 'tienda:tienda'  # A dónde redirigir después del inicio de sesión
LOGOUT_REDIRECT_URL = 'tienda:tienda'          # A dónde redirigir después del cierre de sesión

# Configuración de proveedores sociales.
# En local usamos las credenciales desde .env para no depender del admin.
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'APP': {
            'client_id': config('GOOGLE_CLIENT_ID', default=''),
            'secret': config('GOOGLE_CLIENT_SECRET', default=''),
            'key': ''
        },
        'SCOPE': ['email', 'profile'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'OAUTH_PKCE_ENABLED': True,
    },
    'facebook': {
        'METHOD': 'oauth2',
        'SCOPE': ['email', 'public_profile'],
        'FIELDS': ['id', 'email', 'name', 'first_name', 'last_name'],
        'VERIFIED_EMAIL': False,
        'VERSION': 'v19.0',
    }
}

# Configuración de correo (aquí se usa Gmail como ejemplo)
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True
EMAIL_HOST_USER = 'tu_correo@gmail.com'
EMAIL_HOST_PASSWORD = 'tu_contraseña_o_app_password'

# Configuración de django-allauth
ACCOUNT_LOGOUT_ON_GET = True       # Permite cerrar sesión con un solo clic (sin formulario de confirmación)
ACCOUNT_USERNAME_REQUIRED = False  # Opcional: no requiere nombre de usuario
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_AUTHENTICATION_METHOD = 'email'  # Autenticación basada en correo
ACCOUNT_EMAIL_VERIFICATION = 'none'
SOCIALACCOUNT_LOGIN_ON_GET = True

# Configuraciones de Jazzmin (panel de administración)
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

DEBUG = True

ALLOWED_HOSTS = ['127.0.0.1', 'localhost']

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': ["templates"],  # Asegúrate de incluir la carpeta de templates
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",  # Necesario para Django-Allauth
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "tienda.context_processors.meta_pixel",
            ],
        },
    },
]

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

# Rutas estáticas
STATIC_URL = '/static/'
STATICFILES_DIRS = [
    BASE_DIR.parent / "static",  # Ajusta la ruta según tu estructura
]
STATIC_ROOT = BASE_DIR.parent / 'staticfiles'

# Adaptador de SocialAccount (opcional, aquí se usa el por defecto)
SOCIALACCOUNT_ADAPTER = 'allauth.socialaccount.adapter.DefaultSocialAccountAdapter'
