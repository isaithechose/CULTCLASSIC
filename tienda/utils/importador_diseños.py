# tienda/utils/importador_disenos.py
import os
from django.conf import settings
from tienda.models import Producto, Categoria

def importar_diseños_desde_carpeta():
    ruta = os.path.join(settings.MEDIA_ROOT, 'diseños_nuevos')
    categoria, _ = Categoria.objects.get_or_create(nombre="Diseños")

    nuevos = 0
    if not os.path.exists(ruta):
        os.makedirs(ruta)

    for nombre_archivo in os.listdir(ruta):
        if nombre_archivo.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
            nombre_producto = os.path.splitext(nombre_archivo)[0]
            if not Producto.objects.filter(nombre=nombre_producto).exists():
                Producto.objects.create(
                    nombre=nombre_producto,
                    descripcion="Diseño generado automáticamente.",
                    precio=199.00,
                    stock=10,
                    imagen=f'diseños_nuevos/{nombre_archivo}',
                    categoria=categoria,
                    tallas_disponibles="S,M,L",
                    colores_disponibles="Negro,Blanco",
                    disponible=True
                )
                nuevos += 1
def importar_diseños_propios():
    ruta = os.path.join(settings.MEDIA_ROOT, 'diseños_propios')
    categoria, _ = Categoria.objects.get_or_create(nombre="Diseños Propios")

    nuevos = 0
    if not os.path.exists(ruta):
        os.makedirs(ruta)

    for archivo in os.listdir(ruta):
        if archivo.lower().endswith(('.jpg', '.jpeg', '.png', '.gif')):
            nombre = os.path.splitext(archivo)[0]
            if not Producto.objects.filter(nombre=nombre).exists():
                Producto.objects.create(
                    nombre=nombre,
                    descripcion="Diseño propio subido automáticamente.",
                    precio=250.00,
                    stock=5,
                    imagen=f'diseños_propios/{archivo}',
                    categoria=categoria,
                    tallas_disponibles="S,M,L",
                    colores_disponibles="Negro,Blanco",
                    disponible=True
                )
                nuevos += 1
    return nuevos
