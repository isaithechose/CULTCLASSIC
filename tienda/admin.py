from django.contrib import admin
from .models import Categoria, Subcategoria, Producto, ShippingUpdate
import os
from django.conf import settings

# Acción personalizada
@admin.action(description="Importar imágenes desde /media/diseños_nuevos/")
def importar_disenos(modeladmin, request, queryset):
    ruta = os.path.join(settings.MEDIA_ROOT, 'diseños_nuevos')
    categoria, _ = Categoria.objects.get_or_create(nombre="Diseños")
    creados = 0

    if not os.path.exists(ruta):
        os.makedirs(ruta)

    for archivo in os.listdir(ruta):
        if archivo.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
            nombre = os.path.splitext(archivo)[0]
            if not Producto.objects.filter(nombre=nombre).exists():
                Producto.objects.create(
                    nombre=nombre,
                    descripcion="Diseño importado automáticamente.",
                    precio=199.00,
                    stock=10,
                    imagen=f'diseños_nuevos/{archivo}',
                    categoria=categoria,
                    tallas_disponibles="S,M,L",
                    colores_disponibles="Negro,Blanco",
                    disponible=True
                )
                creados += 1

    modeladmin.message_user(request, f"{creados} productos fueron creados desde imágenes.")

# Registro de modelos
@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'descripcion')

@admin.register(Subcategoria)
class SubcategoriaAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'categoria', 'descripcion')

@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'precio', 'stock', 'categoria', 'subcategoria', 'fecha_creacion')
    list_filter = ('categoria', 'subcategoria', 'fecha_creacion')
    search_fields = ('nombre', 'descripcion')
    actions = [importar_disenos]  # <-- acción agregada aquí

@admin.register(ShippingUpdate)
class ShippingUpdateAdmin(admin.ModelAdmin):
    list_display = ('order', 'status_message', 'updated_at')
    list_filter = ('order', 'updated_at')
