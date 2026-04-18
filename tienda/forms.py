from django import forms
from .models import Reseña
from .models import ShippingAddress
from django.contrib.auth.models import User


class CustomDesignUploadForm(forms.Form):
    name = forms.CharField(
        max_length=120,
        label="Nombre del diseño",
        widget=forms.TextInput(
            attrs={
                "placeholder": "Ej. dragon-rojo",
                "class": "shipping-form__control",
            }
        ),
    )
    image = forms.ImageField(
        label="Archivo base",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": "image/png,image/jpeg,image/webp",
                "class": "shipping-form__control",
            }
        ),
        required=False,
    )
    edited_image = forms.CharField(widget=forms.HiddenInput(), required=False)

class ShippingAddressForm(forms.ModelForm):
    base_input_class = "shipping-form__control"

    class Meta:
        model = ShippingAddress
        fields = ['phone', 'address_line1', 'address_line2', 'city', 'state', 'postal_code', 'country']
        widgets = {
            "phone": forms.TextInput(attrs={"placeholder": "Teléfono de contacto", "autocomplete": "tel"}),
            "address_line1": forms.TextInput(attrs={"placeholder": "Calle y número", "autocomplete": "address-line1"}),
            "address_line2": forms.TextInput(attrs={"placeholder": "Interior, referencia o colonia", "autocomplete": "address-line2"}),
            "city": forms.TextInput(attrs={"placeholder": "Ciudad", "autocomplete": "address-level2"}),
            "state": forms.TextInput(attrs={"placeholder": "Estado", "autocomplete": "address-level1"}),
            "postal_code": forms.TextInput(attrs={"placeholder": "Código postal", "autocomplete": "postal-code"}),
            "country": forms.TextInput(attrs={"placeholder": "País", "autocomplete": "country-name"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            existing_classes = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing_classes} {self.base_input_class}".strip()
        self.fields["postal_code"].widget.attrs["inputmode"] = "numeric"
        self.fields["phone"].widget.attrs["inputmode"] = "tel"


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
