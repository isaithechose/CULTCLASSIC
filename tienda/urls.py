from django.urls import path, include
from django.views.generic import RedirectView
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
    path('producto/<int:producto_id>/resena/', views.submit_reseña, name='submit_reseña'),
    path('productos/', views.lista_productos, name='lista_productos'),
    path('buscar/', views.buscar_productos, name='buscar'),
    # La pagina de archivo se retiro: no tenia productos (no existe la
    # categoria 'archivo') y estaba en el sitemap, asi que la redireccion
    # evita romper lo ya indexado y los enlaces viejos.
    path('archivo/', RedirectView.as_view(pattern_name='tienda:lista_productos', permanent=True)),
    path('google994215bd513f755c.html', views.google_site_verification, name='google_verify'),
    path('mayoreo/', views.mayoreo_view, name='mayoreo'),
    path('faq/', views.faq_view, name='faq'),
    path('devoluciones/', views.devoluciones_view, name='devoluciones'),
    path('culto-calle/', views.culto_calle_view, name='culto_calle'),
    # La pagina nacio en /cult-calle/. Se renombro a Culto Calle el mismo
    # dia; la redireccion evita romper cualquier enlace ya compartido.
    path('cult-calle/', RedirectView.as_view(pattern_name='tienda:culto_calle', permanent=True)),
    path('privacidad/', views.privacidad_view, name='privacidad'),
    path('newsletter/signup/', views.newsletter_signup, name='newsletter_signup'),
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
    path('webhooks/stripe/', views.stripe_webhook, name='stripe_webhook'),
    path('diseños/', views.catalogo_diseños, name='catalogo_diseños'),
    path('diseños-propios/', views.catalogo_diseños_propios, name='catalogo_diseños_propios'),
    path('subir_diseno_personalizado/', views.subir_diseño_personalizado, name='subir_diseño_personalizado'),
    path('creador-diseno/', views.design_creator, name='design_creator'),
    path('creador-diseno/eliminar/<str:filename>/', views.delete_design, name='delete_design'),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
