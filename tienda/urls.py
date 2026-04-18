from django.urls import path, include
from . import views
from .views import (
    my_orders,
    order_detail,
    stripe_checkout,
    payment_success,
    payment_success_done,
    payment_cancel,
    shipping_details,
)
from django.conf import settings
from django.conf.urls.static import static



app_name = 'tienda'

urlpatterns = [
    path('', views.tienda_view, name='tienda'),
    path('perfil/', views.profile_view, name='profile'),
    path('agregar/<int:producto_id>/', views.agregar_al_carrito, name='agregar_al_carrito'),
    path('carrito/', views.carrito_view, name='carrito'),
    path('eliminar/<int:producto_id>/', views.eliminar_del_carrito, name='eliminar_del_carrito'),
    path('producto/<int:producto_id>/', views.detalle_producto, name='detalle_producto'),
    path('productos/', views.lista_productos, name='lista_productos'),
    path('archivo/', views.archivo_view, name='archivo'),
    path('proceso_compra/', views.proceso_compra, name='proceso_compra'),
    path('checkout/', views.checkout, name='checkout'),
    path('my-orders/', my_orders, name='my_orders'),
    path('order/<int:order_id>/', order_detail, name='order_detail'),
    # Rutas para Stripe Checkout
    path('stripe_checkout/', stripe_checkout, name='stripe_checkout'),
    path('payment_success/', payment_success, name='payment_success'),
    path('payment_success/done/', payment_success_done, name='payment_success_done'),
    path('payment_cancel/', payment_cancel, name='payment_cancel'),
    path('shipping/', shipping_details, name='shipping_details'),
    path('order/<int:order_id>/tracking/', views.order_tracking, name='order_tracking'),
    path('order/<int:order_id>/sync-skydrop/', views.sync_skydrop_order, name='sync_skydrop_order'),
    path('tracking/', views.tracking_view, name='tracking'),
    path('webhooks/skydrop/', views.skydrop_webhook, name='skydrop_webhook'),
    path('diseños/', views.catalogo_diseños, name='catalogo_diseños'),
    path('diseños-propios/', views.catalogo_diseños_propios, name='catalogo_diseños_propios'),
    path('subir_diseno_personalizado/', views.subir_diseño_personalizado, name='subir_diseño_personalizado'),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
