from django import forms
from .models import Reseña
from .models import ShippingAddress
from django.contrib.auth.models import User

class ShippingAddressForm(forms.ModelForm):
    class Meta:
        model = ShippingAddress
        fields = ['phone', 'address_line1', 'address_line2', 'city', 'state', 'postal_code', 'country']
        widgets = {
            "phone": forms.TextInput(attrs={"placeholder": "Teléfono de contacto"}),
            "address_line1": forms.TextInput(attrs={"placeholder": "Calle y número"}),
            "address_line2": forms.TextInput(attrs={"placeholder": "Interior, referencia o colonia"}),
            "city": forms.TextInput(attrs={"placeholder": "Ciudad"}),
            "state": forms.TextInput(attrs={"placeholder": "Estado"}),
            "postal_code": forms.TextInput(attrs={"placeholder": "Código postal"}),
            "country": forms.TextInput(attrs={"placeholder": "País"}),
        }


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


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]
        widgets = {
            "first_name": forms.TextInput(attrs={"placeholder": "Nombre"}),
            "last_name": forms.TextInput(attrs={"placeholder": "Apellido"}),
            "email": forms.EmailInput(attrs={"placeholder": "correo@ejemplo.com"}),
        }
