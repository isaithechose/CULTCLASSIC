from pathlib import Path
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY', default='django-insecure-anw7&5=-h!@=x6gkj2y+%7vk_$q+_2@9z8%^+$2@u)6a-4do4!')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
    'allauth.socialaccount.providers.facebook',
    'tienda',
    'mercadolibre',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
]

ROOT_URLCONF = 'CULTCALLE.urls'
WSGI_APPLICATION = 'CULTCALLE.wsgi.application'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR.parent / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'tienda.context_processors.meta_pixel',
                'tienda.context_processors.admin_nav_context',
                'tienda.context_processors.instagram_feed',
            ],
        },
    },
]

SITE_ID = config('SITE_ID', default=4, cast=int)

# ── Instagram (Behold.so widget) ───────────────────────────────────────────
# Conecta @cultclasiccs en https://behold.so/ y pega aquí el feed ID:
#   BEHOLD_FEED_ID=xxxxxxxxxxxxxxxxxxxx
BEHOLD_FEED_ID = config('BEHOLD_FEED_ID', default='')
INSTAGRAM_USERNAME = config('INSTAGRAM_USERNAME', default='cultclasiccs')

# ── Mercado Libre ──────────────────────────────────────────────────────────
# Crea tu app en https://developers.mercadolibre.com.mx/devcenter
# Pon estas variables en tu .env:
#   ML_APP_ID=xxxxx
#   ML_APP_SECRET=xxxxx
#   ML_REDIRECT_URI=https://tudominio.com/mercadolibre/callback/
ML_APP_ID = config('ML_APP_ID', default='')
ML_APP_SECRET = config('ML_APP_SECRET', default='')
ML_REDIRECT_URI = config('ML_REDIRECT_URI', default='http://127.0.0.1:8000/mercadolibre/callback/')
# Categoría default para publicar productos (MLM173159 = Ropa en MX).
ML_DEFAULT_CATEGORY_ID = config('ML_DEFAULT_CATEGORY_ID', default='MLM173159')
ML_DEFAULT_LISTING_TYPE = config('ML_DEFAULT_LISTING_TYPE', default='gold_special')
# Tasas reales que ML cobra a Cult Clasiccs (se aplican como fallback cuando
# el payload todavía no trae el billing real — p.ej. pedidos del día).
ML_FALLBACK_FEE_PCT = config('ML_FALLBACK_FEE_PCT', default='19.5', cast=float)
ML_FALLBACK_SHIPPING_COST = config('ML_FALLBACK_SHIPPING_COST', default='67.60', cast=float)
SITE_URL = config('SITE_URL', default='https://cultclassics.shop')

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

LOGIN_REDIRECT_URL = 'tienda:tienda'
LOGOUT_REDIRECT_URL = 'tienda:tienda'

ACCOUNT_LOGOUT_ON_GET = True
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_AUTHENTICATION_METHOD = 'email'
ACCOUNT_EMAIL_VERIFICATION = 'none'
SOCIALACCOUNT_LOGIN_ON_GET = True

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'APP': {
            'client_id': config('GOOGLE_CLIENT_ID', default=''),
            'secret': config('GOOGLE_CLIENT_SECRET', default=''),
            'key': '',
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
    },
}

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'es-mx'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR.parent / 'static']

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

