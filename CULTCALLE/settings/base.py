from pathlib import Path
from decouple import config

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = config('SECRET_KEY', default='django-insecure-anw7&5=-h!@=x6gkj2y+%7vk_$q+_2@9z8%^+$2@u)6a-4do4!')

INSTALLED_APPS = [
    'jazzmin',
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
            ],
        },
    },
]

SITE_ID = config('SITE_ID', default=4, cast=int)

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

JAZZMIN_SETTINGS = {
    'site_title': 'Cult Clasiccs Admin',
    'site_header': 'Cult Clasiccs Admin',
    'site_brand': 'Cult Clasiccs',
    'site_logo': 'images/logo.png',
    'login_logo': 'images/logo.png',
    'login_logo_dark': None,
    'site_logo_classes': 'img-circle',
    'welcome_sign': 'Panel de control para catalogo, pedidos y operacion diaria.',
    'copyright': 'Cult Clasiccs',
    'search_model': ['tienda.Producto', 'tienda.Order', 'auth.User'],
    'topmenu_links': [
        {'name': 'Inicio', 'url': '/', 'permissions': ['auth.view_user']},
        {'model': 'tienda.Producto'},
        {'model': 'tienda.Order'},
        {'model': 'auth.User'},
    ],
    'usermenu_links': [
        {'name': 'Ver tienda', 'url': '/', 'new_window': True},
    ],
    'icons': {
        'auth': 'fas fa-users-cog',
        'auth.user': 'fas fa-user',
        'auth.Group': 'fas fa-users',
        'sites.Site': 'fas fa-globe',
        'socialaccount.SocialApp': 'fas fa-share-alt',
        'tienda.Categoria': 'fas fa-layer-group',
        'tienda.Subcategoria': 'fas fa-sitemap',
        'tienda.Producto': 'fas fa-shirt',
        'tienda.ProductVariant': 'fas fa-tags',
        'tienda.InventoryMovement': 'fas fa-boxes-stacked',
        'tienda.ExpenseCategory': 'fas fa-folder-tree',
        'tienda.Expense': 'fas fa-wallet',
        'tienda.CashRegisterClosure': 'fas fa-cash-register',
        'tienda.BusinessPayment': 'fas fa-calendar-check',
        'tienda.Order': 'fas fa-bag-shopping',
        'tienda.OrderItem': 'fas fa-box-open',
        'tienda.Carrito': 'fas fa-cart-shopping',
        'tienda.Reseña': 'fas fa-star',
        'tienda.ShippingAddress': 'fas fa-location-dot',
        'tienda.ShippingUpdate': 'fas fa-truck-fast',
    },
    'order_with_respect_to': [
        'auth', 'sites', 'socialaccount', 'tienda',
        'tienda.Categoria', 'tienda.Subcategoria', 'tienda.Producto',
        'tienda.ProductVariant', 'tienda.InventoryMovement',
        'tienda.ExpenseCategory', 'tienda.Expense',
        'tienda.CashRegisterClosure', 'tienda.BusinessPayment',
        'tienda.Order', 'tienda.Carrito', 'tienda.Reseña',
    ],
    'navigation_expanded': True,
    'hide_apps': [],
    'custom_links': {
        'tienda': [
            {'name': 'Punto de venta', 'url': '/admin/tienda/order/point-of-sale/', 'icon': 'fas fa-cash-register', 'permissions': ['tienda.view_order']},
            {'name': 'Cierre de caja', 'url': '/admin/tienda/cashregisterclosure/daily-close/', 'icon': 'fas fa-cash-register', 'permissions': ['tienda.view_cashregisterclosure']},
            {'name': 'Inventario', 'url': '/admin/tienda/producto/inventory-matrix/', 'icon': 'fas fa-boxes-stacked', 'permissions': ['tienda.view_producto']},
            {'name': 'Recepción de compra', 'url': '/admin/tienda/inventorymovement/receive-purchase/', 'icon': 'fas fa-truck-ramp-box', 'permissions': ['tienda.view_inventorymovement']},
            {'name': 'Pedidos pendientes', 'url': '/admin/tienda/order/?status__exact=Pending', 'icon': 'fas fa-clock', 'permissions': ['tienda.view_order']},
            {'name': 'Pagos programados', 'url': '/admin/tienda/businesspayment/', 'icon': 'fas fa-calendar-check', 'permissions': ['tienda.view_businesspayment']},
            {'name': 'Dashboard contable', 'url': '/admin/tienda/expense/accounting-dashboard/', 'icon': 'fas fa-chart-line', 'permissions': ['tienda.view_expense']},
            {'name': 'Gastos', 'url': '/admin/tienda/expense/', 'icon': 'fas fa-wallet', 'permissions': ['tienda.view_expense']},
        ]
    },
}
