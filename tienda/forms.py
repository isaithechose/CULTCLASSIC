from django import forms
from .models import Reseña
from .models import ShippingAddress

class ShippingAddressForm(forms.ModelForm):
    class Meta:
        model = ShippingAddress
        fields = ['address_line1', 'address_line2', 'city', 'state', 'postal_code', 'country']


class ReseñaForm(forms.ModelForm):
    class Meta:
        model = Reseña
        fields = ['comentario', 'calificacion']

class SeleccionarTallaColorForm(forms.Form):
    talla = forms.ChoiceField(choices=[])  # Las opciones se cargarán dinámicamente
    color = forms.ChoiceField(choices=[])

    def __init__(self, *args, **kwargs):
        tallas = kwargs.pop('tallas', [])
        colores = kwargs.pop('colores', [])
        super().__init__(*args, **kwargs)
        self.fields['talla'].choices = [(talla, talla) for talla in tallas]
        self.fields['color'].choices = [(color, color) for color in colores]
