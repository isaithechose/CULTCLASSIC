"""Personalizacion del sitio de admin (indice con resumen)."""

from django.contrib import admin
from django.contrib.admin.sites import AdminSite
import types

from .comun import _admin_overview_context


def _enhanced_admin_index(self, request, extra_context=None):
    context = extra_context or {}
    context.update(_admin_overview_context())
    return AdminSite.index(self, request, extra_context=context)


admin.site.index_template = "admin/index.html"
admin.site.index = types.MethodType(_enhanced_admin_index, admin.site)
