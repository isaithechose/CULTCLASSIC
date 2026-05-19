from django.urls import path
from . import views

app_name = "mercadolibre"

urlpatterns = [
    path("connect/", views.connect, name="connect"),
    path("callback/", views.callback, name="callback"),
    path("sync/", views.sync_now, name="sync"),
]
