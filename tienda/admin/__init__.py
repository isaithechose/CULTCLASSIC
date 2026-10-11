"""Admin de la tienda, dividido por area.

Importar cada modulo registra sus ModelAdmin en admin.site.
"""

from . import catalogo, contabilidad, gastos, pedidos, sitio  # noqa: F401

# Usados fuera del paquete (context_processors, signals, tests).
from .comun import (  # noqa: F401
    ADMIN_OVERVIEW_CACHE_KEY,
    _admin_overview_context,
    _post_expense_journal_entry,
    _post_purchase_journal_entry,
)
from .gastos import _ocurrencias_pendientes  # noqa: F401
