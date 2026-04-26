from django.test import TestCase, Client
from django.urls import reverse
from .models import Categoria, Producto

class CarritoTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.categoria = Categoria.objects.create(nombre="Test")
        self.producto = Producto.objects.create(
            nombre="Camiseta Test",
            precio=300,
            stock=10,
            slug_imagen="camiseta_test",
            categoria=self.categoria,
        )

    def test_agregar_al_carrito_ok(self):
        url = reverse('tienda:agregar_al_carrito', args=[self.producto.id])
        response = self.client.post(url, {
            'talla': 'M',
            'color': 'negro',
            'diseño_pecho': 'dragon_200.png',
            'diseño_espalda': ''
        })

        self.assertRedirects(response, reverse('tienda:carrito'))
        carrito = self.client.session['carrito']
        key = f"{self.producto.id}-M-negro-dragon_200.png-"

        self.assertIn(key, carrito)
        self.assertEqual(carrito[key]['cantidad'], 1)
        self.assertAlmostEqual(carrito[key]['precio'], 500)  # 300 base + 200 diseño pecho

    def test_falta_talla_color(self):
        url = reverse('tienda:agregar_al_carrito', args=[self.producto.id])
        response = self.client.post(url, {
            'talla': '',
            'color': '',
        })
        self.assertRedirects(response, reverse('tienda:detalle_producto', args=[self.producto.id]))
        self.assertNotIn('carrito', self.client.session)

    def test_agregar_al_carrito_no_descuenta_stock(self):
        stock_before = self.producto.stock
        url = reverse('tienda:agregar_al_carrito', args=[self.producto.id])
        self.client.post(url, {
            'talla': 'L',
            'color': 'blanco',
            'diseño_pecho': '',
            'diseño_espalda': ''
        })
        producto = Producto.objects.get(id=self.producto.id)
        self.assertEqual(producto.stock, stock_before)

