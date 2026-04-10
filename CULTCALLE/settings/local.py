from .base import *
# SECURITY WARNING: don't run with debug turned on in production!
import os
from decouple import config
import stripe


STRIPE_PUBLIC_KEY = config('STRIPE_PUBLIC_KEY', default='')
STRIPE_SECRET_KEY = config('STRIPE_SECRET_KEY', default='')


# Imprime el valor de la variable de entorno para verificar que se esté cargando correctamente
print("GOOGLE_CLIENT_ID:", config('GOOGLE_CLIENT_ID', default='No configurado'))

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

SITE_ID = 4



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

# Configuración de proveedores sociales (Google y Facebook)
SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'APP': {
            'client_id': config('GOOGLE_CLIENT_ID', default='TU_CLIENT_ID_DE_PRUEBA'),
            'secret': config('GOOGLE_CLIENT_SECRET', default='TU_SECRET_DE_PRUEBA'),
            'key': ''
        },
        'SCOPE': ['email', 'profile'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'OAUTH_PKCE_ENABLED': True,
    },
    'facebook': {
        'APP': {
            'client_id': config('FACEBOOK_CLIENT_ID', default='TU_FACEBOOK_APP_ID'),
            'secret': config('FACEBOOK_CLIENT_SECRET', default='TU_FACEBOOK_APP_SECRET'),
            'key': ''
        },
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
