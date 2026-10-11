"""Admin de catalogo: categorias, productos, variantes y movimientos de inventario."""

from django.conf import settings
from django.contrib import admin
from django.contrib import messages
from django import forms
from django.db.models import Count, F, Prefetch, Sum
from django.db.models.functions import Coalesce
from django.forms import formset_factory
from django.http import HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.html import format_html
import json
from datetime import timedelta
from ..models import (
    Categoria,
    JournalEntry,
    Order,
    OrderItem,
    Producto,
    ProductImage,
    ProductVariant,
    SizeChart,
    InventoryMovement,
    Subcategoria,
    record_inventory_movement,
)
import os
from decimal import Decimal
from django.utils import timezone
from ..utils.variant_image_assignment import (
    existing_thumbnail_or_image_name,
    find_best_image_for_variant,
)

from .comun import (
    PURCHASE_PAYMENT_ACCOUNTS,
    PURCHASE_PAYMENT_CHOICES,
    _money,
    _post_purchase_journal_entry,
    _product_unit_cost,
    _split_variant_values,
    _variant_margin,
    _variant_profit,
    _variant_sale_price,
    _variant_unit_cost,
)


@admin.action(description="Importar imagenes desde /media/diseños_nuevos/")
def importar_disenos(modeladmin, request, queryset):
    ruta = os.path.join(settings.MEDIA_ROOT, "diseños_nuevos")
    categoria, _ = Categoria.objects.get_or_create(nombre="Diseños")
    creados = 0

    if not os.path.exists(ruta):
        os.makedirs(ruta)

    precio_default = Decimal(str(getattr(settings, "IMPORT_DISENO_PRECIO_DEFAULT", "199.00")))
    stock_default = getattr(settings, "IMPORT_DISENO_STOCK_DEFAULT", 10)
    tallas_default = getattr(settings, "IMPORT_DISENO_TALLAS_DEFAULT", "S,M,L,XL")
    colores_default = getattr(settings, "IMPORT_DISENO_COLORES_DEFAULT", "Negro,Blanco")

    for archivo in os.listdir(ruta):
        if archivo.lower().endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
            nombre = os.path.splitext(archivo)[0]
            if not Producto.objects.filter(nombre=nombre).exists():
                Producto.objects.create(
                    nombre=nombre,
                    descripcion="Diseño importado automaticamente.",
                    precio=precio_default,
                    stock=stock_default,
                    imagen=f"diseños_nuevos/{archivo}",
                    categoria=categoria,
                    tallas_disponibles=tallas_default,
                    colores_disponibles=colores_default,
                    disponible=True,
                )
                creados += 1

    modeladmin.message_user(request, f"{creados} productos fueron creados desde imagenes.")


@admin.action(description="Marcar productos como disponibles")
def marcar_disponibles(modeladmin, request, queryset):
    updated = queryset.update(disponible=True)
    modeladmin.message_user(request, f"{updated} productos marcados como disponibles.")


@admin.action(description="Marcar productos como no disponibles")
def marcar_no_disponibles(modeladmin, request, queryset):
    updated = queryset.update(disponible=False)
    modeladmin.message_user(request, f"{updated} productos marcados como no disponibles.")


@admin.action(description="Generar variantes faltantes desde tallas y colores")
def generar_variantes_faltantes(modeladmin, request, queryset):
    total_created = 0
    total_products = 0
    for product in queryset:
        created_count = modeladmin._create_missing_variants_for_product(product)
        total_created += created_count
        total_products += 1
    if total_created:
        modeladmin.message_user(
            request,
            f"Se generaron {total_created} variantes nuevas en {total_products} productos.",
            level=messages.SUCCESS,
        )
    else:
        modeladmin.message_user(
            request,
            "No había variantes faltantes por crear. Revisa que tallas y colores estén separados por comas.",
            level=messages.INFO,
        )


@admin.action(description="Asignar imágenes de variante desde media/productos")
def asignar_imagenes_variantes(modeladmin, request, queryset):
    assigned = 0
    missing = 0

    for variant in queryset.select_related("product"):
        image_name = find_best_image_for_variant(variant)
        if not image_name:
            missing += 1
            continue

        if variant.imagen.name == image_name:
            continue

        variant.imagen.name = image_name
        variant.save(update_fields=["imagen", "updated_at"])
        assigned += 1

    if assigned:
        modeladmin.message_user(
            request,
            f"Se asignaron {assigned} imágenes de variante.",
            level=messages.SUCCESS,
        )
    if missing:
        modeladmin.message_user(
            request,
            f"{missing} variantes no encontraron imagen compatible en media/productos.",
            level=messages.WARNING,
        )


@admin.action(description="Marcar variantes sin imagen compatible")
def validar_imagenes_variantes(modeladmin, request, queryset):
    missing = []
    checked = 0

    for variant in queryset.select_related("product"):
        checked += 1
        if not (variant.imagen.name or find_best_image_for_variant(variant)):
            missing.append(str(variant))

    if missing:
        preview = ", ".join(missing[:8])
        extra = "" if len(missing) <= 8 else f" y {len(missing) - 8} más"
        modeladmin.message_user(
            request,
            f"{len(missing)} de {checked} variantes no tienen imagen compatible: {preview}{extra}.",
            level=messages.WARNING,
        )
    else:
        modeladmin.message_user(
            request,
            f"Todas las {checked} variantes revisadas tienen imagen compatible.",
            level=messages.SUCCESS,
        )


class SubcategoriaInline(admin.TabularInline):
    model = Subcategoria
    extra = 0
    fields = ("nombre", "descripcion")


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 1
    fields = ("image", "image_preview", "alt_text", "order")
    readonly_fields = ("image_preview",)

    @admin.display(description="Vista previa")
    def image_preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="height:56px;border-radius:8px;object-fit:cover;">', obj.image.url)
        return "—"


class SizeChartInline(admin.TabularInline):
    model = SizeChart
    extra = 1
    fields = ("talla", "pecho", "cintura", "largo", "hombro", "manga")
    verbose_name = "Medida"
    verbose_name_plural = "Tabla de tallas"


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    fields = (
        "sku",
        "talla",
        "color",
        "imagen",
        "stock",
        "precio_venta_display",
        "costo_producto_display",
        "utilidad_display",
        "margen_display",
        "activo",
    )
    readonly_fields = ("precio_venta_display", "costo_producto_display", "utilidad_display", "margen_display")
    autocomplete_fields = ()
    verbose_name = "Variante real"
    verbose_name_plural = "Variantes reales de inventario (color + talla)"

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        return formfield

    @admin.display(description="Precio venta")
    def precio_venta_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        return f"${_variant_sale_price(obj):.2f}"

    @admin.display(description="Costo producto")
    def costo_producto_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        return f"${_variant_unit_cost(obj):.2f}"

    @admin.display(description="Utilidad")
    def utilidad_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        return f"${_variant_profit(obj):.2f}"

    @admin.display(description="Margen")
    def margen_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        return f"{_variant_margin(obj):.1f}%"


class InventoryMovementInline(admin.TabularInline):
    model = InventoryMovement
    extra = 0
    fields = ("created_at", "movement_type", "variant", "quantity_change", "stock_before", "stock_after", "note")
    readonly_fields = ("created_at", "movement_type", "variant", "quantity_change", "stock_before", "stock_after", "note")
    can_delete = False
    show_change_link = True
    verbose_name = "Movimiento de inventario"
    verbose_name_plural = "Historial de movimientos de inventario"


@admin.register(Categoria)
class CategoriaAdmin(admin.ModelAdmin):
    list_display = ("nombre", "descripcion_corta", "total_subcategorias", "total_productos")
    search_fields = ("nombre", "descripcion")
    inlines = [SubcategoriaInline]

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset.annotate(
            subcategorias_count=Count("subcategorias", distinct=True),
            productos_count=Count("producto", distinct=True),
        )

    @admin.display(description="Descripcion")
    def descripcion_corta(self, obj):
        if not obj.descripcion:
            return "-"
        return (obj.descripcion[:60] + "...") if len(obj.descripcion) > 60 else obj.descripcion

    @admin.display(ordering="subcategorias_count", description="Subcategorias")
    def total_subcategorias(self, obj):
        return obj.subcategorias_count

    @admin.display(ordering="productos_count", description="Productos")
    def total_productos(self, obj):
        return obj.productos_count


@admin.register(Subcategoria)
class SubcategoriaAdmin(admin.ModelAdmin):
    list_select_related = ("categoria",)
    list_display = ("nombre", "categoria", "descripcion_corta")
    list_filter = ("categoria",)
    search_fields = ("nombre", "descripcion", "categoria__nombre")
    autocomplete_fields = ("categoria",)

    @admin.display(description="Descripcion")
    def descripcion_corta(self, obj):
        if not obj.descripcion:
            return "-"
        return (obj.descripcion[:60] + "...") if len(obj.descripcion) > 60 else obj.descripcion


@admin.register(Producto)
class ProductoAdmin(admin.ModelAdmin):
    list_select_related = ("categoria", "subcategoria")
    list_display = (
        "preview_imagen",
        "nombre",
        "categoria",
        "subcategoria",
        "costo_producto",
        "precio_venta_base",
        "inventory_mode",
        "stock",
        "variant_stock_summary",
        "inventory_value_summary",
        "disponible",
        "fecha_actualizacion",
    )
    list_filter = ("disponible", "categoria", "subcategoria", "fecha_creacion", "fecha_actualizacion")
    search_fields = ("nombre", "descripcion", "slug_imagen")
    autocomplete_fields = ("categoria", "subcategoria")
    actions = [importar_disenos, marcar_disponibles, marcar_no_disponibles, generar_variantes_faltantes, "publicar_en_mercadolibre"]

    @admin.action(description="Publicar en Mercado Libre")
    def publicar_en_mercadolibre(self, request, queryset):
        from mercadolibre import api as ml_api
        from mercadolibre.models import MercadoLibreCredential, MercadoLibreListing
        cred = MercadoLibreCredential.objects.first()
        if not cred:
            self.message_user(request, "No hay cuenta de Mercado Libre conectada.", level=messages.ERROR)
            return
        ok = skipped = errs = 0
        for p in queryset:
            if MercadoLibreListing.objects.filter(producto=p).exists():
                skipped += 1
                continue
            try:
                listing = ml_api.publish_product_to_ml(cred, p)
                ok += 1
            except Exception as exc:
                errs += 1
                self.message_user(request, f"{p.nombre}: {str(exc)[:200]}", level=messages.WARNING)
        self.message_user(
            request,
            f"Publicados: {ok} · Saltados (ya estaban): {skipped} · Errores: {errs}",
            level=messages.SUCCESS if ok > 0 else messages.INFO,
        )
    list_editable = ("stock", "disponible")
    readonly_fields = (
        "fecha_creacion",
        "fecha_actualizacion",
        "imagen_preview_large",
        "inventory_guide",
        "inventory_snapshot",
        "variant_generation_panel",
        "stock_count_panel",
    )
    inlines = [ProductImageInline, SizeChartInline, ProductVariantInline, InventoryMovementInline]
    fieldsets = (
        ("Base del producto", {
            "fields": ("nombre", "slug_imagen", "descripcion")
        }),
        ("Venta general", {
            "fields": ("categoria", "subcategoria", "costo", "precio", "stock", "disponible")
        }),
        ("Inventario y variantes", {
            "fields": (
                "inventory_guide",
                "tallas_disponibles",
                "colores_disponibles",
                "variant_generation_panel",
                "stock_count_panel",
                "inventory_snapshot",
            )
        }),
        ("Imagen general del producto", {
            "fields": ("imagen", "imagen_preview_large")
        }),
        ("Control", {
            "fields": ("fecha_creacion", "fecha_actualizacion")
        }),
    )

    list_per_page = 30

    def get_queryset(self, request):
        return (
            super()
            .get_queryset(request)
            .prefetch_related(
                Prefetch(
                    "variants",
                    queryset=ProductVariant.objects.filter(activo=True),
                    to_attr="active_variants",
                )
            )
        )

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "design-catalog/",
                self.admin_site.admin_view(self.design_catalog_view),
                name="tienda_producto_design_catalog",
            ),
            path(
                "monthly-sales/",
                self.admin_site.admin_view(self.monthly_sales_view),
                name="tienda_producto_monthly_sales",
            ),
            path(
                "inventory-matrix/",
                self.admin_site.admin_view(self.inventory_matrix_view),
                name="tienda_producto_inventory_matrix",
            ),
            path(
                "inventory-dashboard/",
                self.admin_site.admin_view(self.inventory_dashboard_view),
                name="tienda_producto_inventory_dashboard",
            ),
            path(
                "sales-summary/",
                self.admin_site.admin_view(self.sales_summary_view),
                name="tienda_producto_sales_summary",
            ),
            path(
                "stock-count-bulk/",
                self.admin_site.admin_view(self.stock_count_bulk_view),
                name="tienda_producto_stock_count_bulk",
            ),
            path(
                "<int:product_id>/generate-variants/",
                self.admin_site.admin_view(self.generate_variants_view),
                name="tienda_producto_generate_variants",
            ),
            path(
                "<int:product_id>/stock-count/",
                self.admin_site.admin_view(self.stock_count_view),
                name="tienda_producto_stock_count",
            ),
        ]
        return custom_urls + urls

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        formfield = super().formfield_for_dbfield(db_field, request, **kwargs)
        if db_field.name == "precio" and formfield:
            formfield.label = "Precio venta base"
            formfield.help_text = "Precio de venta del producto. Todas sus variantes usan este mismo precio."
        if db_field.name == "costo" and formfield:
            formfield.label = "Costo unitario del producto"
            formfield.help_text = "Costo por pieza. Todas las variantes usan este mismo costo."
        return formfield

    @admin.display(description="Imagen")
    def preview_imagen(self, obj):
        if obj.imagen:
            return format_html(
                '<img src="{}" style="width:42px;height:42px;object-fit:cover;border-radius:8px;" />',
                obj.imagen.url,
            )
        return "-"

    @admin.display(description="Vista previa")
    def imagen_preview_large(self, obj):
        if obj.imagen:
            return format_html(
                '<img src="{}" style="max-width:220px;border-radius:14px;border:1px solid #ddd;" />',
                obj.imagen.url,
            )
        return "Sin imagen"

    @admin.display(description="Precio venta base", ordering="precio")
    def precio_venta_base(self, obj):
        return f"${obj.precio:.2f}"

    @admin.display(description="Costo producto", ordering="costo")
    def costo_producto(self, obj):
        return f"${_product_unit_cost(obj):.2f}"

    @admin.display(description="Inventario")
    def inventory_mode(self, obj):
        # active_variants viene del prefetch del listado; sin él sería un
        # exists() por fila.
        variants = getattr(obj, "active_variants", None)
        uses_variants = bool(variants) if variants is not None else obj.uses_variant_inventory()
        if uses_variants:
            return format_html('<strong style="color:#2f67b0;">Variantes mandan</strong>')
        return format_html('<span style="color:#888;">Stock general manda</span>')

    @admin.display(description="Cómo funciona este inventario")
    def inventory_guide(self, obj):
        if obj.uses_variant_inventory():
            return format_html(
                """
                <div style="padding:0.9rem 1rem;border-radius:14px;background:#eff6ff;border:1px solid #bfdbfe;">
                  <strong style="display:block;margin-bottom:0.35rem;color:#1d4ed8;">Este producto ya trabaja por variantes.</strong>
                  <div style="color:#334155;">
                    El stock real vive en cada combinación de <strong>color + talla</strong>. El campo <strong>stock</strong> del producto solo refleja la suma y no deberías capturarlo manualmente.
                  </div>
                </div>
                """
            )
        return format_html(
            """
            <div style="padding:0.9rem 1rem;border-radius:14px;background:#fff7ed;border:1px solid #fed7aa;">
              <strong style="display:block;margin-bottom:0.35rem;color:#c2410c;">Este producto todavía usa stock general.</strong>
              <div style="color:#7c2d12;">
                Si vendes por talla y color, genera variantes. Mientras no existan variantes activas, el campo <strong>stock</strong> del producto es el que manda en ventas.
              </div>
            </div>
            """
        )

    @admin.display(description="Stock variantes")
    def variant_stock_summary(self, obj):
        variants = getattr(obj, "active_variants", None)
        if variants is None:
            variants = list(obj.variants.filter(activo=True))
        if not variants:
            return "-"
        return ", ".join(f"{variant.color}/{variant.talla}: {variant.stock}" for variant in variants[:6])

    @admin.display(description="Resumen de inventario")
    def inventory_snapshot(self, obj):
        variants = list(obj.variants.filter(activo=True))
        if not variants:
            sale_value = _money(obj.precio) * Decimal(str(obj.stock))
            return format_html(
                """
                <div style="padding:0.85rem 1rem;border-radius:12px;background:#f8fafc;border:1px solid #e2e8f0;">
                  <strong>Modo actual:</strong> stock general.<br>
                  Si quieres controlar talla y color por separado, primero genera variantes abajo y luego captura el inventario en la mesa o en el conteo físico.
                  <div style="margin-top:0.7rem;"><strong>Valor potencial venta:</strong> ${}</div>
                </div>
                """,
                f"{sale_value:.2f}",
            )
        total_units = sum(variant.stock for variant in variants)
        cost_value = sum(_variant_unit_cost(variant) * Decimal(str(variant.stock)) for variant in variants)
        sale_value = sum(_variant_sale_price(variant) * Decimal(str(variant.stock)) for variant in variants)
        profit_value = sale_value - cost_value
        rows = [
            "<div style='display:flex;flex-wrap:wrap;gap:0.45rem;'>"
        ]
        for variant in variants:
            rows.append(
                f"<span style='padding:0.3rem 0.6rem;border-radius:999px;background:#2f67b022;color:#2f67b0;font-weight:700;'>{variant.color} / {variant.talla}: {variant.stock}</span>"
            )
        rows.append("</div>")
        rows.append(
            f"<p style='margin-top:0.8rem;'><strong>Total sincronizado:</strong> {obj.stock}</p>"
            f"<p style='margin-top:0.35rem;'><strong>Valor al costo:</strong> ${cost_value:.2f} &nbsp; <strong>Valor venta:</strong> ${sale_value:.2f} &nbsp; <strong>Utilidad potencial:</strong> ${profit_value:.2f}</p>"
            "<p style='margin-top:0.35rem;color:#475569;'>Este total se recalcula desde las variantes activas.</p>"
        )
        return format_html("".join(rows))

    @admin.display(description="Valor inventario")
    def inventory_value_summary(self, obj):
        variants = getattr(obj, "active_variants", None)
        if variants is None:
            variants = list(obj.variants.filter(activo=True))
        if variants:
            cost_value = sum(_variant_unit_cost(variant) * Decimal(str(variant.stock)) for variant in variants)
            sale_value = sum(_variant_sale_price(variant) * Decimal(str(variant.stock)) for variant in variants)
        else:
            cost_value = _money(obj.precio) * Decimal(str(obj.stock))
            sale_value = cost_value
        profit_value = sale_value - cost_value
        return format_html(
            "<span title='Costo: ${} | Venta: ${} | Utilidad potencial: ${}'>${}</span>",
            f"{cost_value:.2f}",
            f"{sale_value:.2f}",
            f"{profit_value:.2f}",
            f"{sale_value:.2f}",
        )

    @admin.display(description="Crear variantes")
    def variant_generation_panel(self, obj):
        if not obj.pk:
            return "Guarda el producto primero para poder generar variantes."
        if not obj.tallas_disponibles or not obj.colores_disponibles:
            return "Agrega tallas y colores separados por comas para generar combinaciones."
        generate_url = reverse("admin:tienda_producto_generate_variants", args=[obj.pk])
        sizes = ", ".join(_split_variant_values(obj.tallas_disponibles))
        colors = ", ".join(_split_variant_values(obj.colores_disponibles))
        return format_html(
            """
            <p style="margin-bottom:0.6rem;color:#475569;">Aquí defines las combinaciones que después se convierten en variantes editables.</p>
            <p style="margin-bottom:0.6rem;"><strong>Tallas base:</strong> {}</p>
            <p style="margin-bottom:0.9rem;"><strong>Colores base:</strong> {}</p>
            <a class="button" href="{}">Generar variantes faltantes</a>
            """,
            sizes or "-",
            colors or "-",
            generate_url,
        )

    @admin.display(description="Inventario físico")
    def stock_count_panel(self, obj):
        if not obj.pk:
            return "Guarda el producto primero para capturar inventario físico."
        count_url = reverse("admin:tienda_producto_stock_count", args=[obj.pk])
        return format_html(
            """
            <p style="margin-bottom:0.9rem;">Si ya hay variantes, aquí cuentas cada color/talla. Si no hay variantes, ajustas el stock general del producto.</p>
            <a class="button" href="{}">Capturar inventario físico</a>
            """,
            count_url,
        )

    def _create_missing_variants_for_product(self, product):
        sizes = _split_variant_values(product.tallas_disponibles)
        colors = _split_variant_values(product.colores_disponibles)
        created_count = 0
        for color in colors:
            for size in sizes:
                _, created = ProductVariant.objects.get_or_create(
                    product=product,
                    talla=size,
                    color=color,
                    defaults={
                        "stock": 0,
                        "activo": True,
                    },
                )
                if created:
                    created_count += 1
        product.sync_stock_from_variants()
        return created_count

    def generate_variants_view(self, request, product_id):
        product = self.get_object(request, product_id)
        if not product:
            self.message_user(request, "No encontramos ese producto.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:tienda_producto_changelist"))

        created_count = self._create_missing_variants_for_product(product)
        if created_count:
            self.message_user(
                request,
                f"Se generaron {created_count} variantes faltantes para {product.nombre}.",
                level=messages.SUCCESS,
            )
        else:
            self.message_user(
                request,
                "No había variantes nuevas por crear para este producto.",
                level=messages.INFO,
            )
        return HttpResponseRedirect(reverse("admin:tienda_producto_change", args=[product.pk]))

    def stock_count_view(self, request, product_id):
        product = self.get_object(request, product_id)
        if not product:
            self.message_user(request, "No encontramos ese producto.", level=messages.ERROR)
            return HttpResponseRedirect(reverse("admin:tienda_producto_changelist"))

        variants = list(product.variants.filter(activo=True).order_by("color", "talla"))

        class StockCountLineForm(forms.Form):
            variant_id = forms.IntegerField(widget=forms.HiddenInput, required=False)
            label = forms.CharField(required=False, widget=forms.HiddenInput)
            current_stock = forms.IntegerField(required=False, widget=forms.HiddenInput)
            counted_stock = forms.IntegerField(min_value=0, label="Conteo real")

        StockCountFormSet = formset_factory(StockCountLineForm, extra=0)

        if request.method == "POST":
            formset = StockCountFormSet(request.POST, prefix="count")
            note = request.POST.get("note", "").strip() or "Conteo físico desde admin."
            if formset.is_valid():
                adjustments = 0
                if variants:
                    variant_map = {variant.id: variant for variant in variants}
                    for form in formset:
                        variant_id = form.cleaned_data.get("variant_id")
                        counted_stock = form.cleaned_data.get("counted_stock")
                        variant = variant_map.get(variant_id)
                        if variant is None or counted_stock is None:
                            continue
                        difference = int(counted_stock) - int(variant.stock)
                        if difference != 0:
                            record_inventory_movement(
                                product=product,
                                variant=variant,
                                movement_type="adjustment",
                                quantity_change=difference,
                                note=note,
                                created_by=request.user,
                                metadata={"counted_stock": counted_stock},
                            )
                            adjustments += 1
                else:
                    counted_stock = request.POST.get("general_counted_stock")
                    if counted_stock not in (None, ""):
                        counted_stock = int(counted_stock)
                        difference = counted_stock - int(product.stock)
                        if difference != 0:
                            record_inventory_movement(
                                product=product,
                                movement_type="adjustment",
                                quantity_change=difference,
                                note=note,
                                created_by=request.user,
                                metadata={"counted_stock": counted_stock},
                            )
                            adjustments += 1

                if adjustments:
                    self.message_user(
                        request,
                        f"Conteo guardado. Se registraron {adjustments} ajustes para {product.nombre}.",
                        level=messages.SUCCESS,
                    )
                else:
                    self.message_user(
                        request,
                        "Conteo guardado sin diferencias. No hizo falta ajustar inventario.",
                        level=messages.INFO,
                    )
                return HttpResponseRedirect(reverse("admin:tienda_producto_change", args=[product.pk]))
        else:
            initial = []
            for variant in variants:
                initial.append(
                    {
                        "variant_id": variant.id,
                        "label": f"{variant.color} / {variant.talla}",
                        "current_stock": variant.stock,
                        "counted_stock": variant.stock,
                    }
                )
            formset = StockCountFormSet(initial=initial, prefix="count")

        # Los datos visibles salen de la variante, no de form.initial: en un POST con
        # errores los formularios vienen ligados y initial llega vacío (etiquetas en blanco).
        rows = []
        for variant, form in zip(variants, formset.forms):
            rows.append(
                {
                    "form": form,
                    "label": f"{variant.color} / {variant.talla}",
                    "current_stock": variant.stock,
                }
            )
        for form in formset.forms[len(variants):]:
            rows.append(
                {
                    "form": form,
                    "label": form.initial.get("label", ""),
                    "current_stock": form.initial.get("current_stock", 0),
                }
            )

        context = dict(
            self.admin_site.each_context(request),
            title=f"Conteo físico: {product.nombre}",
            product=product,
            rows=rows,
            formset=formset,
            opts=self.model._meta,
            has_variants=bool(variants),
        )
        return TemplateResponse(request, "admin/tienda/product_stock_count.html", context)

    def inventory_dashboard_view(self, request):
        active_products = Producto.objects.filter(disponible=True)
        active_variants = ProductVariant.objects.filter(activo=True, product__disponible=True).select_related("product")
        low_stock_variants = list(active_variants.filter(stock__lte=3).order_by("stock", "product__nombre", "color", "talla")[:12])
        out_of_stock_variants = list(active_variants.filter(stock=0).order_by("product__nombre", "color", "talla")[:12])
        general_products = list(
            active_products.filter(variants__isnull=True).order_by("stock", "nombre")[:12]
        )
        recent_movements = list(
            InventoryMovement.objects.select_related("product", "variant", "created_by")
            .order_by("-created_at")[:14]
        )

        total_units = 0
        total_inventory_cost_value = Decimal("0.00")
        total_inventory_sale_value = Decimal("0.00")
        products_using_variants = 0
        products_using_general_stock = 0
        chart_labels = []
        chart_stock = []
        chart_variants_by_product = {}

        for product in active_products.prefetch_related("variants"):
            variants = [variant for variant in product.variants.all() if variant.activo]
            if variants:
                products_using_variants += 1
                p_stock = sum(variant.stock for variant in variants)
                total_units += p_stock
                total_inventory_cost_value += sum(_variant_unit_cost(variant) * Decimal(str(variant.stock)) for variant in variants)
                total_inventory_sale_value += sum(_variant_sale_price(variant) * Decimal(str(variant.stock)) for variant in variants)
                chart_variants_by_product[product.nombre] = [
                    {"color": v.color, "talla": v.talla, "stock": v.stock} for v in variants
                ]
            else:
                products_using_general_stock += 1
                p_stock = product.stock
                total_units += p_stock
                total_inventory_cost_value += _product_unit_cost(product) * Decimal(str(product.stock))
                total_inventory_sale_value += _money(product.precio) * Decimal(str(product.stock))
                chart_variants_by_product[product.nombre] = [
                    {"color": "—", "talla": "General", "stock": product.stock}
                ]
            chart_labels.append(product.nombre)
            chart_stock.append(p_stock)

        all_variants_list = list(active_variants)
        variants_ok = sum(1 for v in all_variants_list if v.stock > 3)
        variants_low = sum(1 for v in all_variants_list if 0 < v.stock <= 3)
        variants_out = sum(1 for v in all_variants_list if v.stock == 0)
        all_variants_data = [
            {"producto": v.product.nombre, "color": v.color, "talla": v.talla, "stock": v.stock}
            for v in all_variants_list
        ]

        context = dict(
            self.admin_site.each_context(request),
            title="Dashboard de inventario",
            subtitle="Resumen operativo del stock actual",
            total_products=active_products.count(),
            total_variants=active_variants.count(),
            total_units=total_units,
            total_inventory_value=total_inventory_cost_value,
            total_inventory_cost_value=total_inventory_cost_value,
            total_inventory_sale_value=total_inventory_sale_value,
            total_inventory_profit_value=total_inventory_sale_value - total_inventory_cost_value,
            products_using_variants=products_using_variants,
            products_using_general_stock=products_using_general_stock,
            low_stock_variants=low_stock_variants,
            out_of_stock_variants=out_of_stock_variants,
            general_products=general_products,
            recent_movements=recent_movements,
            stock_count_bulk_url=reverse("admin:tienda_producto_stock_count_bulk"),
            inventory_matrix_url=reverse("admin:tienda_producto_inventory_matrix"),
            opts=self.model._meta,
            chart_labels_json=json.dumps(chart_labels),
            chart_stock_json=json.dumps(chart_stock),
            chart_health_json=json.dumps([variants_ok, variants_low, variants_out]),
            chart_variants_by_product_json=json.dumps(chart_variants_by_product),
            all_variants_data_json=json.dumps(all_variants_data),
        )
        return TemplateResponse(request, "admin/tienda/inventory_dashboard.html", context)

    def monthly_sales_view(self, request):
        """Ventas mes a mes: ingresos, pedidos, piezas, costo y utilidad."""
        import json
        from django.db.models import Sum, Count, F, Q
        from django.db.models.functions import TruncMonth

        months_back = int(request.GET.get("months", 12))
        months_back = max(3, min(months_back, 36))

        _MES = ["", "Ene", "Feb", "Mar", "Abr", "May", "Jun",
                "Jul", "Ago", "Sep", "Oct", "Nov", "Dic"]

        # Ingresos y piezas por mes (desde OrderItem)
        item_rows = (
            OrderItem.objects
            .filter(order__status="Completed")
            .annotate(month=TruncMonth("order__created_at"))
            .values("month")
            .annotate(
                revenue=Sum(F("price") * F("quantity")),
                units=Sum("quantity"),
                cogs=Sum(Coalesce(F("unit_cost"), F("product__costo")) * F("quantity")),
            )
        )
        # Pedidos por mes (desde Order)
        order_rows = (
            Order.objects
            .filter(status="Completed")
            .annotate(month=TruncMonth("created_at"))
            .values("month")
            .annotate(orders=Count("id"))
        )

        by_month = {}
        for r in item_rows:
            m = r["month"]
            if not m:
                continue
            key = (m.year, m.month)
            by_month.setdefault(key, {})
            by_month[key]["revenue"] = r["revenue"] or Decimal("0")
            by_month[key]["units"] = r["units"] or 0
            by_month[key]["cogs"] = r["cogs"] or Decimal("0")
        for r in order_rows:
            m = r["month"]
            if not m:
                continue
            key = (m.year, m.month)
            by_month.setdefault(key, {})
            by_month[key]["orders"] = r["orders"] or 0

        # Ventas simuladas (polizas SIM-VENTA-YYYY-MM): ingreso en cta 4000, costo en cta 5000.
        # No aportan pedidos ni piezas, solo ingreso/COGS.
        sim_rows = (
            JournalEntry.objects
            .filter(reference__istartswith="SIM-VENTA")
            .annotate(month=TruncMonth("date"))
            .values("month")
            .annotate(
                sim_revenue=Sum("lines__credit", filter=Q(lines__account__code="4000")),
                sim_cogs=Sum("lines__debit", filter=Q(lines__account__code="5000")),
            )
        )
        for r in sim_rows:
            m = r["month"]
            if not m:
                continue
            key = (m.year, m.month)
            entry = by_month.setdefault(key, {})
            sim_rev = r["sim_revenue"] or Decimal("0")
            sim_cogs = r["sim_cogs"] or Decimal("0")
            entry["revenue"] = entry.get("revenue", Decimal("0")) + sim_rev
            entry["cogs"] = entry.get("cogs", Decimal("0")) + sim_cogs
            entry["sim_revenue"] = sim_rev
            entry["sim_cogs"] = sim_cogs

        # Construir rango continuo de los últimos N meses (incluye meses sin ventas)
        today = timezone.localdate()
        year, month = today.year, today.month
        sequence = []
        for _ in range(months_back):
            sequence.append((year, month))
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        sequence.reverse()

        rows = []
        total_rev = total_cogs = Decimal("0")
        total_sim_rev = total_sim_cogs = Decimal("0")
        total_units = total_orders = 0
        for (y, mo) in sequence:
            data = by_month.get((y, mo), {})
            rev = data.get("revenue", Decimal("0"))
            cogs = data.get("cogs", Decimal("0"))
            units = data.get("units", 0)
            orders = data.get("orders", 0)
            sim_rev = data.get("sim_revenue", Decimal("0"))
            sim_cogs = data.get("sim_cogs", Decimal("0"))
            profit = rev - cogs
            margin = float(profit / rev * 100) if rev > 0 else 0.0
            ticket = float(rev / orders) if orders > 0 else 0.0
            rows.append({
                "label": f"{_MES[mo]} {y}",
                "short": f"{_MES[mo]} '{str(y)[2:]}",
                "revenue": rev, "cogs": cogs, "profit": profit,
                "units": units, "orders": orders,
                "margin": margin, "ticket": ticket,
                "sim_revenue": sim_rev, "sim_cogs": sim_cogs,
                "has_sim": sim_rev > 0,
            })
            total_rev += rev
            total_cogs += cogs
            total_sim_rev += sim_rev
            total_sim_cogs += sim_cogs
            total_units += units
            total_orders += orders

        total_profit = total_rev - total_cogs
        # Comparación mes actual vs anterior
        delta_pct = None
        if len(rows) >= 2 and rows[-2]["revenue"] > 0:
            delta_pct = float((rows[-1]["revenue"] - rows[-2]["revenue"]) / rows[-2]["revenue"] * 100)

        best = max(rows, key=lambda r: r["revenue"]) if rows else None

        sim_share_pct = float(total_sim_rev / total_rev * 100) if total_rev > 0 else 0.0
        subtitle = f"Últimos {months_back} meses · pedidos completados + ventas simuladas"
        if total_sim_rev > 0:
            subtitle += f" (${total_sim_rev:,.0f} simulados · {sim_share_pct:.1f}% del total)"

        context = dict(
            self.admin_site.each_context(request),
            title="Ventas por mes",
            subtitle=subtitle,
            rows=rows,
            rows_desc=list(reversed(rows)),
            months_back=months_back,
            total_rev=total_rev, total_cogs=total_cogs, total_profit=total_profit,
            total_sim_rev=total_sim_rev, total_sim_cogs=total_sim_cogs,
            total_units=total_units, total_orders=total_orders,
            avg_ticket=(float(total_rev / total_orders) if total_orders else 0.0),
            delta_pct=delta_pct,
            best_month=best,
            chart_labels=json.dumps([r["short"] for r in rows]),
            chart_revenue=json.dumps([float(r["revenue"]) for r in rows]),
            chart_profit=json.dumps([float(r["profit"]) for r in rows]),
            opts=self.model._meta,
        )
        return TemplateResponse(request, "admin/tienda/monthly_sales.html", context)

    def sales_summary_view(self, request):
        """Resumen de ventas últimos N días + recomendación de restock por velocidad."""
        from collections import defaultdict
        from datetime import timedelta
        from django.db.models import Sum, F

        days = int(request.GET.get("days", 90))
        days = max(7, min(days, 365))
        cutoff = timezone.now() - timedelta(days=days)

        sold_items = (
            OrderItem.objects
            .filter(
                order__status="Completed",
                order__created_at__gte=cutoff,
            )
            .select_related("product")
            .values("product_id", "product__nombre", "color", "talla")
            .annotate(units=Sum("quantity"), revenue=Sum(F("price") * F("quantity")))
            .order_by("-units")
        )

        # Indexar variantes por (product_id, color, talla)
        variants_idx = {
            (v.product_id, v.color, v.talla): v
            for v in ProductVariant.objects.filter(activo=True).select_related("product")
        }
        products_idx = {p.id: p for p in Producto.objects.all()}

        product_stats = {}
        for row in sold_items:
            pid = row["product_id"]
            units = row["units"] or 0
            revenue = row["revenue"] or Decimal("0")
            stats = product_stats.setdefault(pid, {
                "product": products_idx.get(pid),
                "units": 0, "revenue": Decimal("0"),
                "variants": [],
                "colors": defaultdict(int),
                "sizes": defaultdict(int),
            })
            stats["units"] += units
            stats["revenue"] += revenue
            if row["color"]:
                stats["colors"][row["color"]] += units
            if row["talla"]:
                stats["sizes"][row["talla"]] += units
            v = variants_idx.get((pid, row["color"] or "", row["talla"] or ""))
            current_stock = v.stock if v else None
            velocity = (units / days) if days > 0 else 0
            days_left = (current_stock / velocity) if (current_stock is not None and velocity > 0) else None
            if days_left is None:
                recomendacion, tone = "—", "neutral"
            elif days_left < 14:
                recomendacion, tone = "Reabastecer URGENTE", "danger"
            elif days_left < 30:
                recomendacion, tone = "Comprar pronto", "warn"
            else:
                recomendacion, tone = "OK", "ok"
            stats["variants"].append({
                "color": row["color"] or "—",
                "talla": row["talla"] or "—",
                "units": units,
                "revenue": revenue,
                "stock": current_stock,
                "days_left": round(days_left, 1) if days_left is not None else None,
                "recomendacion": recomendacion,
                "tone": tone,
                "variant_id": v.id if v else None,
            })

        product_summary = sorted(product_stats.values(), key=lambda s: s["units"], reverse=True)
        for s in product_summary:
            s["colors_top"] = sorted(s["colors"].items(), key=lambda x: x[1], reverse=True)[:5]
            s["sizes_top"]  = sorted(s["sizes"].items(),  key=lambda x: x[1], reverse=True)[:5]

        # Recomendaciones globales: top variantes a reabastecer
        urgent_restock = []
        for s in product_summary:
            for v in s["variants"]:
                if v["recomendacion"] in ("Reabastecer URGENTE", "Comprar pronto"):
                    urgent_restock.append({
                        "product": s["product"].nombre if s["product"] else "—",
                        "product_id": s["product"].id if s["product"] else None,
                        **v,
                    })
        urgent_restock.sort(key=lambda x: (x["days_left"] if x["days_left"] is not None else 99999, -x["units"]))

        # Variantes con velocidad pero sin stock (oportunidad perdida)
        out_of_stock_movers = [x for x in urgent_restock if x["stock"] == 0]

        # Totales
        total_units = sum(s["units"] for s in product_summary)
        total_revenue = sum(s["revenue"] for s in product_summary)

        context = dict(
            self.admin_site.each_context(request),
            title=f"Resumen de ventas (ult. {days} dias)",
            subtitle="Piezas vendidas, top colores/tallas y recomendación de compra por velocidad",
            days=days,
            cutoff=cutoff,
            total_units=total_units,
            total_revenue=total_revenue,
            product_summary=product_summary,
            urgent_restock=urgent_restock[:30],
            out_of_stock_movers=out_of_stock_movers[:15],
            opts=self.model._meta,
        )
        return TemplateResponse(request, "admin/tienda/sales_summary.html", context)

    def design_catalog_view(self, request):
        from django.utils.text import slugify

        CATALOG_DIR = os.path.join(settings.MEDIA_ROOT, "diseños_nuevos")
        EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        os.makedirs(CATALOG_DIR, exist_ok=True)

        categoria, _ = Categoria.objects.get_or_create(nombre="Diseños")
        precio_default = Decimal(str(getattr(settings, "IMPORT_DISENO_PRECIO_DEFAULT", "199.00")))
        stock_default = getattr(settings, "IMPORT_DISENO_STOCK_DEFAULT", 10)

        msg_ok = msg_err = ""

        if request.method == "POST":
            action = request.POST.get("action")

            # ── upload ──
            if action == "upload":
                uploaded = request.FILES.getlist("files")
                saved = 0
                for f in uploaded:
                    ext = os.path.splitext(f.name)[1].lower()
                    if ext not in EXTS:
                        continue
                    dest = os.path.join(CATALOG_DIR, f.name)
                    with open(dest, "wb") as out:
                        for chunk in f.chunks():
                            out.write(chunk)
                    saved += 1
                msg_ok = f"{saved} archivo(s) subido(s)." if saved else "Ningún archivo válido."

            # ── import single ──
            elif action == "import":
                filename = request.POST.get("file", "").strip()
                filepath = os.path.join(CATALOG_DIR, filename)
                if filename and os.path.exists(filepath):
                    nombre = os.path.splitext(filename)[0]
                    precio = Decimal(request.POST.get("precio", str(precio_default)))
                    if not Producto.objects.filter(nombre=nombre).exists():
                        Producto.objects.create(
                            nombre=nombre,
                            descripcion="Diseño del catálogo.",
                            precio=precio,
                            costo=0,
                            stock=stock_default,
                            imagen=f"diseños_nuevos/{filename}",
                            categoria=categoria,
                            tallas_disponibles="S,M,L,XL",
                            colores_disponibles="Negro,Blanco",
                            disponible=True,
                        )
                        msg_ok = f'"{nombre}" importado como producto.'
                    else:
                        msg_err = f'"{nombre}" ya existe como producto.'
                else:
                    msg_err = "Archivo no encontrado."

            # ── import all ──
            elif action == "import_all":
                precio = Decimal(request.POST.get("precio", str(precio_default)))
                creados = 0
                for f in os.listdir(CATALOG_DIR):
                    if os.path.splitext(f)[1].lower() not in EXTS:
                        continue
                    nombre = os.path.splitext(f)[0]
                    if not Producto.objects.filter(nombre=nombre).exists():
                        Producto.objects.create(
                            nombre=nombre,
                            descripcion="Diseño del catálogo.",
                            precio=precio,
                            costo=0,
                            stock=stock_default,
                            imagen=f"diseños_nuevos/{f}",
                            categoria=categoria,
                            tallas_disponibles="S,M,L,XL",
                            colores_disponibles="Negro,Blanco",
                            disponible=True,
                        )
                        creados += 1
                msg_ok = f"{creados} diseño(s) importado(s) como productos."

            # ── delete file ──
            elif action == "delete":
                filename = request.POST.get("file", "").strip()
                filepath = os.path.join(CATALOG_DIR, filename)
                if filename and os.path.exists(filepath):
                    os.remove(filepath)
                    msg_ok = f'"{filename}" eliminado.'
                else:
                    msg_err = "Archivo no encontrado."

            # ── edit product ──
            elif action == "edit":
                pid = request.POST.get("product_id")
                try:
                    p = Producto.objects.get(pk=pid, categoria=categoria)
                    p.nombre = request.POST.get("nombre", p.nombre).strip() or p.nombre
                    raw_precio = request.POST.get("precio", "").strip()
                    if raw_precio:
                        p.precio = Decimal(raw_precio)
                    p.descripcion = request.POST.get("descripcion", p.descripcion).strip()
                    p.disponible = request.POST.get("disponible") == "1"
                    p.save()
                    msg_ok = f'"{p.nombre}" actualizado.'
                except Producto.DoesNotExist:
                    msg_err = "Producto no encontrado."

            # ── toggle disponible ──
            elif action == "toggle":
                pid = request.POST.get("product_id")
                try:
                    p = Producto.objects.get(pk=pid, categoria=categoria)
                    p.disponible = not p.disponible
                    p.save(update_fields=["disponible"])
                    estado = "activado" if p.disponible else "desactivado"
                    msg_ok = f'"{p.nombre}" {estado}.'
                except Producto.DoesNotExist:
                    msg_err = "Producto no encontrado."

            # ── bulk price ──
            elif action == "bulk_price":
                nuevo_precio = request.POST.get("precio", "").strip()
                solo_inactivos = request.POST.get("solo_inactivos") == "1"
                if nuevo_precio:
                    qs = Producto.objects.filter(categoria=categoria)
                    if solo_inactivos:
                        qs = qs.filter(disponible=False)
                    updated = qs.update(precio=Decimal(nuevo_precio))
                    msg_ok = f"Precio actualizado a ${nuevo_precio} en {updated} diseño(s)."
                else:
                    msg_err = "Ingresa un precio válido."

        # build file list
        productos_map = {
            p.nombre: p
            for p in Producto.objects.filter(categoria=categoria)
        }
        files = []
        for f in sorted(os.listdir(CATALOG_DIR)):
            if os.path.splitext(f)[1].lower() not in EXTS:
                continue
            nombre = os.path.splitext(f)[0]
            product = productos_map.get(nombre)
            files.append({
                "filename": f,
                "nombre": nombre,
                "url": f"{settings.MEDIA_URL}diseños_nuevos/{f}",
                "imported": product is not None,
                "product": product,
            })

        context = dict(
            self.admin_site.each_context(request),
            title="Catálogo de Diseños",
            subtitle=f"{len(files)} diseños en disco · {sum(1 for f in files if f['imported'])} importados",
            files=files,
            precio_default=precio_default,
            msg_ok=msg_ok,
            msg_err=msg_err,
            opts=self.model._meta,
        )
        return TemplateResponse(request, "admin/tienda/design_catalog.html", context)

    def inventory_matrix_view(self, request):
        variants = list(
            ProductVariant.objects.filter(activo=True, product__disponible=True)
            .select_related("product", "product__categoria")
            .order_by("product__nombre", "color", "talla")
        )

        class InventoryMatrixLineForm(forms.Form):
            variant_id = forms.IntegerField(widget=forms.HiddenInput)
            stock = forms.IntegerField(min_value=0, label="Stock")

        InventoryMatrixFormSet = formset_factory(InventoryMatrixLineForm, extra=0)

        if request.method == "POST":
            formset = InventoryMatrixFormSet(request.POST, prefix="matrix")
            note = request.POST.get("note", "").strip() or "Ajuste desde mesa de inventario."
            if formset.is_valid():
                variant_map = {variant.id: variant for variant in variants}
                adjustments = 0
                for form in formset:
                    variant_id = form.cleaned_data.get("variant_id")
                    new_stock = form.cleaned_data.get("stock")
                    variant = variant_map.get(variant_id)
                    if variant is None or new_stock is None:
                        continue
                    difference = int(new_stock) - int(variant.stock)
                    if difference != 0:
                        record_inventory_movement(
                            product=variant.product,
                            variant=variant,
                            movement_type="adjustment",
                            quantity_change=difference,
                            note=note,
                            created_by=request.user,
                            metadata={"target_stock": new_stock, "mode": "inventory_matrix"},
                        )
                        adjustments += 1

                if adjustments:
                    self.message_user(
                        request,
                        f"Mesa de inventario guardada. Se registraron {adjustments} ajustes.",
                        level=messages.SUCCESS,
                    )
                else:
                    self.message_user(
                        request,
                        "No hubo cambios de stock que guardar.",
                        level=messages.INFO,
                    )
                return HttpResponseRedirect(reverse("admin:tienda_producto_inventory_matrix"))
        else:
            initial = [
                {
                    "variant_id": variant.id,
                    "stock": variant.stock,
                }
                for variant in variants
            ]
            formset = InventoryMatrixFormSet(initial=initial, prefix="matrix")

        rows = []
        for variant, form in zip(variants, formset.forms):
            rows.append(
                {
                    "variant": variant,
                    "form": form,
                }
            )

        all_colors = sorted({v.color for v in variants if v.color})
        all_tallas = sorted({v.talla for v in variants if v.talla})
        all_productos = sorted({v.product.nombre for v in variants})
        all_categorias = sorted({
            v.product.categoria.nombre
            for v in variants
            if v.product.categoria
        })

        context = dict(
            self.admin_site.each_context(request),
            title="Inventario por variantes",
            rows=rows,
            formset=formset,
            opts=self.model._meta,
            filter_colors=all_colors,
            filter_tallas=all_tallas,
            filter_categorias=all_categorias,
            filter_productos=all_productos,
            total_variants=len(variants),
        )
        return TemplateResponse(request, "admin/tienda/inventory_matrix.html", context)

    def stock_count_bulk_view(self, request):
        variants = list(
            ProductVariant.objects.filter(activo=True, product__disponible=True)
            .select_related("product")
            .order_by("product__nombre", "color", "talla")
        )

        class BulkStockCountLineForm(forms.Form):
            variant_id = forms.IntegerField(widget=forms.HiddenInput)
            counted_stock = forms.IntegerField(
                min_value=0,
                label="Conteo real",
                widget=forms.NumberInput(attrs={
                    "class": "bulk-qty",
                    "min": "0",
                    "step": "1",
                    "inputmode": "numeric",
                    "autocomplete": "off",
                    "aria-label": "Conteo real",
                }),
            )

        BulkStockCountFormSet = formset_factory(BulkStockCountLineForm, extra=0)

        if request.method == "POST":
            formset = BulkStockCountFormSet(request.POST, prefix="bulk")
            note = request.POST.get("note", "").strip() or "Conteo físico masivo desde admin."
            if formset.is_valid():
                variant_map = {variant.id: variant for variant in variants}
                adjustments = 0
                for form in formset:
                    variant_id = form.cleaned_data.get("variant_id")
                    counted_stock = form.cleaned_data.get("counted_stock")
                    variant = variant_map.get(variant_id)
                    if variant is None or counted_stock is None:
                        continue
                    difference = int(counted_stock) - int(variant.stock)
                    if difference != 0:
                        record_inventory_movement(
                            product=variant.product,
                            variant=variant,
                            movement_type="adjustment",
                            quantity_change=difference,
                            note=note,
                            created_by=request.user,
                            metadata={"counted_stock": counted_stock, "mode": "bulk"},
                        )
                        adjustments += 1

                if adjustments:
                    self.message_user(
                        request,
                        f"Conteo masivo guardado. Se registraron {adjustments} ajustes.",
                        level=messages.SUCCESS,
                    )
                else:
                    self.message_user(
                        request,
                        "Conteo masivo guardado sin diferencias.",
                        level=messages.INFO,
                    )
                return HttpResponseRedirect(reverse("admin:tienda_producto_inventory_dashboard"))
        else:
            initial = [
                {
                    "variant_id": variant.id,
                    "counted_stock": variant.stock,
                }
                for variant in variants
            ]
            formset = BulkStockCountFormSet(initial=initial, prefix="bulk")

        rows = []
        for variant, form in zip(variants, formset.forms):
            rows.append(
                {
                    "variant": variant,
                    "form": form,
                }
            )

        note_value = request.POST.get("note", "") if request.method == "POST" else ""

        context = dict(
            self.admin_site.each_context(request),
            title="Conteo físico masivo",
            rows=rows,
            formset=formset,
            opts=self.model._meta,
            filter_colors=sorted({v.color for v in variants if v.color}),
            filter_tallas=sorted({v.talla for v in variants if v.talla}),
            filter_productos=sorted({v.product.nombre for v in variants}),
            total_variants=len(variants),
            note_value=note_value or "Conteo físico masivo desde admin.",
        )
        return TemplateResponse(request, "admin/tienda/product_stock_count_bulk.html", context)


class InventoryMovementAdminForm(forms.ModelForm):
    class Meta:
        model = InventoryMovement
        fields = ("product", "variant", "movement_type", "quantity_change", "order", "note", "created_by")

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get("product")
        variant = cleaned.get("variant")
        quantity_change = cleaned.get("quantity_change")

        if variant and product and variant.product_id != product.id:
            raise forms.ValidationError("La variante elegida no pertenece al producto seleccionado.")

        if quantity_change == 0:
            raise forms.ValidationError("El movimiento debe cambiar el inventario con una cantidad distinta de cero.")

        return cleaned


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_select_related = ("product",)
    list_display = (
        "preview_imagen",
        "product",
        "sku",
        "color",
        "talla",
        "imagen_status",
        "stock",
        "costo_producto_display",
        "precio_venta_display",
        "utilidad_display",
        "margen_display",
        "activo",
        "updated_at",
    )
    list_filter = ("activo", "color", "talla", "product__categoria")
    search_fields = ("product__nombre", "sku", "color", "talla")
    autocomplete_fields = ("product",)
    list_editable = ("activo",)
    actions = [asignar_imagenes_variantes, validar_imagenes_variantes]
    readonly_fields = (
        "updated_at",
        "created_at",
        "imagen_preview_large",
        "costo_producto_display",
        "precio_venta_display",
        "utilidad_display",
        "margen_display",
        "variant_role_help",
    )
    fieldsets = (
        ("Variante", {
            "fields": ("product", "sku", "color", "talla", "imagen", "activo", "imagen_preview_large", "variant_role_help")
        }),
        ("Inventario", {
            "fields": ("stock", "costo_producto_display", "precio_venta_display", "utilidad_display", "margen_display")
        }),
        ("Control", {
            "fields": ("created_at", "updated_at")
        }),
    )

    list_per_page = 50

    @admin.display(description="Imagen")
    def preview_imagen(self, obj):
        image_name = existing_thumbnail_or_image_name(obj.display_image_name)
        if image_name:
            return format_html(
                '<img src="{}" loading="lazy" decoding="async" style="width:42px;height:42px;object-fit:cover;border-radius:8px;" />',
                f"{settings.MEDIA_URL.rstrip('/')}/{image_name.lstrip('/')}",
            )
        return "-"

    @admin.display(description="Imagen color")
    def imagen_status(self, obj):
        image_name = obj.display_image_name
        if not image_name:
            return format_html(
                '<span style="display:inline-block;padding:0.24rem 0.55rem;border-radius:999px;background:#fee2e2;color:#991b1b;font-weight:700;">Falta</span>'
            )

        source = "Manual" if obj.imagen else "Auto"
        return format_html(
            '<span title="{}" style="display:inline-block;padding:0.24rem 0.55rem;border-radius:999px;background:#dcfce7;color:#166534;font-weight:700;">{}</span>',
            image_name,
            source,
        )

    @admin.display(description="Vista previa")
    def imagen_preview_large(self, obj):
        if not obj:
            return "-"
        image_url = obj.display_image_url
        if not image_url:
            return "-"
        return format_html(
            '<img src="{}" loading="lazy" decoding="async" style="width:220px;height:280px;object-fit:cover;border-radius:8px;border:1px solid #e5e7eb;" />',
            image_url,
        )

    @admin.display(description="Costo producto")
    def costo_producto_display(self, obj):
        return f"${_variant_unit_cost(obj):.2f}"

    @admin.display(description="Precio venta")
    def precio_venta_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        return f"${_variant_sale_price(obj):.2f}"

    @admin.display(description="Utilidad")
    def utilidad_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        profit = _variant_profit(obj)
        color = "#166534" if profit >= 0 else "#991b1b"
        bg = "#dcfce7" if profit >= 0 else "#fee2e2"
        return format_html(
            '<span style="display:inline-block;padding:0.24rem 0.55rem;border-radius:999px;background:{};color:{};font-weight:700;">${}</span>',
            bg,
            color,
            f"{profit:.2f}",
        )

    @admin.display(description="Margen")
    def margen_display(self, obj):
        if not obj or not obj.pk:
            return "-"
        return f"{_variant_margin(obj):.1f}%"

    @admin.display(description="Qué controla esta variante")
    def variant_role_help(self, obj):
        return format_html(
            """
            <div style="padding:0.85rem 1rem;border-radius:12px;background:#eff6ff;border:1px solid #bfdbfe;">
              <strong style="display:block;margin-bottom:0.35rem;color:#1d4ed8;">Esta fila es la existencia real vendible.</strong>
              <div style="color:#334155;">
                Cuando el cliente elige <strong>{color}</strong> y <strong>{talla}</strong>, este es el stock que se descuenta. La imagen aquí debe corresponder al color de esta variante.
              </div>
            </div>
            """,
            color=obj.color or "-",
            talla=obj.talla or "-",
        )


@admin.register(InventoryMovement)
class InventoryMovementAdmin(admin.ModelAdmin):
    list_select_related = ("product", "variant", "variant__product", "order", "created_by")
    list_per_page = 50
    show_full_result_count = False
    form = InventoryMovementAdminForm
    list_display = (
        "created_at",
        "product",
        "variant",
        "movement_type",
        "quantity_change",
        "stock_before",
        "stock_after",
        "created_by",
    )
    list_filter = ("movement_type", "created_at", "product__categoria")
    search_fields = ("product__nombre", "variant__sku", "variant__color", "variant__talla", "note")
    autocomplete_fields = ("product", "variant", "order", "created_by")
    readonly_fields = ("stock_before", "stock_after", "created_at", "movement_guide")

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path(
                "receive-purchase/",
                self.admin_site.admin_view(self.receive_purchase_view),
                name="tienda_inventorymovement_receive_purchase",
            ),
            path(
                "receive-purchase/<int:variant_id>/",
                self.admin_site.admin_view(self.receive_purchase_view),
                name="tienda_inventorymovement_receive_purchase_variant",
            ),
        ]
        return custom_urls + urls

    def get_readonly_fields(self, request, obj=None):
        if obj:
            return (
                "product",
                "variant",
                "order",
                "movement_type",
                "quantity_change",
                "note",
                "created_by",
                "metadata",
                "stock_before",
                "stock_after",
                "created_at",
                "movement_guide",
            )
        return self.readonly_fields

    fieldsets = (
        ("Movimiento", {
            "fields": ("movement_guide", "product", "variant", "movement_type", "quantity_change", "order", "note", "created_by")
        }),
        ("Resultado", {
            "fields": ("stock_before", "stock_after", "created_at")
        }),
    )

    @admin.display(description="Cómo usar movimientos")
    def movement_guide(self, obj=None):
        return format_html(
            """
            <div style="padding:0.85rem 1rem;border-radius:12px;background:#f8fafc;border:1px solid #e2e8f0;">
              Usa <strong>Movimientos</strong> para dejar historial. Si solo editas el stock de una variante, cambias la existencia pero no el motivo. Aquí registras compras, ventas, ajustes y entradas/salidas manuales.
            </div>
            """
        )

    def has_change_permission(self, request, obj=None):
        if obj:
            return False
        return super().has_change_permission(request, obj)

    def save_model(self, request, obj, form, change):
        if change:
            return

        created = record_inventory_movement(
            product=form.cleaned_data["product"],
            variant=form.cleaned_data.get("variant"),
            order=form.cleaned_data.get("order"),
            movement_type=form.cleaned_data["movement_type"],
            quantity_change=form.cleaned_data["quantity_change"],
            note=form.cleaned_data.get("note", ""),
            created_by=form.cleaned_data.get("created_by") or request.user,
            metadata=obj.metadata,
        )
        obj.pk = created.pk
        obj.stock_before = created.stock_before
        obj.stock_after = created.stock_after
        obj.created_at = created.created_at

    def receive_purchase_view(self, request, variant_id=None):
        variants = list(
            ProductVariant.objects.filter(activo=True, product__disponible=True)
            .select_related("product")
            .order_by("product__nombre", "color", "talla")
        )
        selected_variant = None
        if variant_id:
            selected_variant = next((variant for variant in variants if variant.id == variant_id), None)
        # El formset solo lleva las variantes que se renderizan: si la pantalla está
        # filtrada a una variante, TOTAL_FORMS tiene que coincidir con esa única fila.
        form_variants = [selected_variant] if selected_variant else variants

        class PurchaseReceiptLineForm(forms.Form):
            variant_id = forms.IntegerField(widget=forms.HiddenInput)
            quantity = forms.IntegerField(
                min_value=0,
                required=False,
                initial=0,
                label="Cantidad",
                widget=forms.NumberInput(attrs={
                    "class": "rp-qty",
                    "min": "0",
                    "step": "1",
                    "inputmode": "numeric",
                    "autocomplete": "off",
                    "aria-label": "Cantidad recibida",
                }),
            )
            unit_cost = forms.DecimalField(
                min_value=0,
                decimal_places=2,
                max_digits=10,
                required=False,
                label="Costo producto",
                widget=forms.HiddenInput(attrs={"class": "rp-cost"}),
            )

        PurchaseReceiptFormSet = formset_factory(PurchaseReceiptLineForm, extra=0)

        form_values = {
            "supplier": "",
            "note": "Recepción de compra desde admin.",
            "receipt_date": str(timezone.localdate()),
            "payment_method": "transfer",
        }

        if request.method == "POST":
            formset = PurchaseReceiptFormSet(request.POST, prefix="receipt")
            supplier = request.POST.get("supplier", "").strip()
            note = request.POST.get("note", "").strip() or "Recepción de compra desde admin."
            payment_method = request.POST.get("payment_method", "").strip() or "transfer"
            if payment_method not in PURCHASE_PAYMENT_ACCOUNTS:
                payment_method = "transfer"
            receipt_date = request.POST.get("receipt_date", "").strip() or str(timezone.localdate())
            form_values = {
                "supplier": supplier,
                "note": note,
                "receipt_date": receipt_date,
                "payment_method": payment_method,
            }

            if formset.is_valid():
                variant_map = {variant.id: variant for variant in variants}
                movements = 0
                total_purchase_amount = Decimal("0.00")

                for form in formset:
                    current_variant_id = form.cleaned_data.get("variant_id")
                    quantity = form.cleaned_data.get("quantity") or 0
                    unit_cost = form.cleaned_data.get("unit_cost")
                    variant = variant_map.get(current_variant_id)
                    if variant is None or quantity <= 0:
                        continue

                    if unit_cost is not None and variant.product.costo != unit_cost:
                        variant.product.costo = unit_cost
                        variant.product.save(update_fields=["costo", "fecha_actualizacion"])
                    unit_cost = unit_cost if unit_cost is not None else _product_unit_cost(variant.product)

                    record_inventory_movement(
                        product=variant.product,
                        variant=variant,
                        movement_type="purchase",
                        quantity_change=int(quantity),
                        note=note,
                        created_by=request.user,
                        metadata={
                            "supplier": supplier,
                            "unit_cost": str(unit_cost),
                            "receipt_date": receipt_date,
                        },
                    )
                    total_purchase_amount += Decimal(str(unit_cost)) * Decimal(str(quantity))
                    movements += 1

                entry = None
                if total_purchase_amount > 0:
                    # La compra capitaliza inventario (1100) contra la forma de
                    # pago. Antes iba a gastos (6000), lo que duplicaba el costo
                    # y dejaba 1100 en negativo al vender.
                    entry = _post_purchase_journal_entry(
                        date_value=receipt_date,
                        amount=total_purchase_amount,
                        payment_method=payment_method,
                        supplier=supplier,
                        note=note,
                        reference=f"REC-{receipt_date}",
                        created_by=request.user,
                    )

                if movements:
                    payment_label = dict(PURCHASE_PAYMENT_CHOICES).get(payment_method, payment_method)
                    detalle = f" Póliza #{entry.id}: inventario contra {payment_label.lower()}." if entry else ""
                    self.message_user(
                        request,
                        f"Compra registrada. Se cargaron {movements} variantes y ${total_purchase_amount:.2f} de costo total.{detalle}",
                        level=messages.SUCCESS,
                    )
                else:
                    self.message_user(
                        request,
                        "No capturaste cantidades mayores a cero.",
                        level=messages.INFO,
                    )
                return HttpResponseRedirect(reverse("admin:tienda_inventorymovement_changelist"))

            self.message_user(
                request,
                "Revisa la captura: hay cantidades o costos con valores inválidos. No se registró nada.",
                level=messages.ERROR,
            )
        else:
            initial = [
                {
                    "variant_id": variant.id,
                    "quantity": 0,
                    "unit_cost": variant.product.costo or "",
                }
                for variant in form_variants
            ]
            formset = PurchaseReceiptFormSet(initial=initial, prefix="receipt")

        rows = [
            {"variant": variant, "form": form}
            for variant, form in zip(form_variants, formset.forms)
        ]

        groups = []
        groups_by_product = {}
        for row in rows:
            variant = row["variant"]
            product = variant.product
            group = groups_by_product.get(product.id)
            if group is None:
                group = {
                    "product": product,
                    "rows": [],
                    "unit_cost": product.costo if product.costo is not None else "",
                    "image_url": None,
                    "stock_total": 0,
                    "low_count": 0,
                }
                groups_by_product[product.id] = group
                groups.append(group)
            group["rows"].append(row)
            group["stock_total"] += variant.stock or 0
            if (variant.stock or 0) <= 3:
                group["low_count"] += 1
            if not group["image_url"]:
                group["image_url"] = variant.display_image_url

        filter_colors = sorted({row["variant"].color for row in rows if row["variant"].color})
        filter_tallas = sorted({row["variant"].talla for row in rows if row["variant"].talla})

        context = dict(
            self.admin_site.each_context(request),
            title="Recepción de compra",
            rows=rows,
            groups=groups,
            formset=formset,
            selected_variant=selected_variant,
            form_values=form_values,
            filter_colors=filter_colors,
            filter_tallas=filter_tallas,
            payment_choices=PURCHASE_PAYMENT_CHOICES,
            total_variants=len(rows),
            total_products=len(groups),
            today=str(timezone.localdate()),
            opts=self.model._meta,
        )
        return TemplateResponse(request, "admin/tienda/receive_purchase.html", context)
