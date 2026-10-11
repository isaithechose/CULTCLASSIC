import re

from django import forms
from django.core.exceptions import ValidationError

from .models import Reseña, ShippingAddress
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

class RastreoPedidoForm(forms.Form):
    """Rastreo publico: numero de pedido + correo con el que se compro."""

    order_id = forms.IntegerField(
        min_value=1,
        label="Número de pedido",
        widget=forms.NumberInput(attrs={"class": "shipping-form__control", "placeholder": "Ej. 2585"}),
    )
    email = forms.EmailField(
        label="Correo de la compra",
        widget=forms.EmailInput(attrs={"class": "shipping-form__control", "placeholder": "tu@correo.com"}),
    )

    def clean_email(self):
        return self.cleaned_data["email"].strip().lower()


class ShippingAddressForm(forms.ModelForm):
    base_input_class = "shipping-form__control"

    class Meta:
        model = ShippingAddress
        fields = ['phone', 'address_line1', 'address_line2', 'city', 'state', 'postal_code', 'country']
        widgets = {
            "phone": forms.TextInput(attrs={
                "placeholder": "656 123 4567", "autocomplete": "tel",
                "type": "tel", "spellcheck": "false",
            }),
            "address_line1": forms.TextInput(attrs={
                "placeholder": "Av. Tecnológico 1234", "autocomplete": "address-line1",
            }),
            "address_line2": forms.TextInput(attrs={
                "placeholder": "Int. 4, Col. Centro", "autocomplete": "address-line2",
            }),
            "city": forms.TextInput(attrs={
                "placeholder": "Ciudad Juárez", "autocomplete": "address-level2",
            }),
            "state": forms.TextInput(attrs={
                "placeholder": "Chihuahua", "autocomplete": "address-level1",
            }),
            "postal_code": forms.TextInput(attrs={
                "placeholder": "32000", "autocomplete": "postal-code", "spellcheck": "false",
            }),
            "country": forms.TextInput(attrs={
                "placeholder": "México", "autocomplete": "country-name",
            }),
        }

    def __init__(self, *args, require_contact=False, **kwargs):
        super().__init__(*args, **kwargs)
        # Sin sesion no hay cuenta de donde sacar correo y nombre: se piden aqui.
        if require_contact:
            self.fields["contact_email"] = forms.EmailField(
                label="Correo de contacto",
                widget=forms.EmailInput(attrs={
                    "placeholder": "tu@correo.com", "autocomplete": "email", "spellcheck": "false",
                }),
                error_messages={"required": "Necesitamos tu correo para mandarte la confirmación."},
            )
            self.fields["contact_name"] = forms.CharField(
                label="Nombre de quien recibe",
                max_length=120,
                widget=forms.TextInput(attrs={"placeholder": "Nombre y apellido", "autocomplete": "name"}),
                error_messages={"required": "La paquetería necesita un nombre para la guía."},
            )
        for name, field in self.fields.items():
            existing_classes = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"{existing_classes} {self.base_input_class}".strip()
        self.fields["postal_code"].widget.attrs["inputmode"] = "numeric"
        self.fields["phone"].widget.attrs["inputmode"] = "tel"
        self.fields["country"].initial = "México"

    def clean_postal_code(self):
        value = (self.cleaned_data.get("postal_code") or "").strip()
        if not re.fullmatch(r"\d{5}", value):
            raise ValidationError("El código postal debe tener exactamente 5 dígitos.")
        return value

    def clean_phone(self):
        value = (self.cleaned_data.get("phone") or "").strip()
        digits = re.sub(r"[\s\-\(\)\+]", "", value)
        if digits and not re.fullmatch(r"\d{10,15}", digits):
            raise ValidationError("Ingresa un número de teléfono válido (10 a 15 dígitos).")
        return value

    def clean_contact_email(self):
        return (self.cleaned_data.get("contact_email") or "").strip().lower()

    def clean_contact_name(self):
        return (self.cleaned_data.get("contact_name") or "").strip()

    def address_data(self):
        """Solo los campos de ShippingAddress, sin los de contacto."""
        return {name: self.cleaned_data[name] for name in self._meta.fields}


class ReseñaForm(forms.ModelForm):
    class Meta:
        model = Reseña
        fields = ['comentario', 'calificacion']

class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email"]
        widgets = {
            "first_name": forms.TextInput(attrs={"placeholder": "Nombre"}),
            "last_name": forms.TextInput(attrs={"placeholder": "Apellido"}),
            "email": forms.EmailInput(attrs={"placeholder": "correo@ejemplo.com"}),
        }
