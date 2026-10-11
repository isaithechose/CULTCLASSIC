# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Run development server (uses local.py settings)
python manage.py runserver --settings=CULTCALLE.settings.local

# Run all tests
python manage.py test tienda --settings=CULTCALLE.settings.local

# Run a single test class or method
python manage.py test tienda.tests.CartTests --settings=CULTCALLE.settings.local
python manage.py test tienda.tests.CartTests.test_add_to_cart --settings=CULTCALLE.settings.local

# Apply migrations
python manage.py migrate --settings=CULTCALLE.settings.local

# Create migrations after model changes
python manage.py makemigrations --settings=CULTCALLE.settings.local

# Collect static files (needed for production)
python manage.py collectstatic --settings=CULTCALLE.settings.prod
```

Settings module must always be specified explicitly; the project uses `CULTCALLE.settings.local` for development and `CULTCALLE.settings.prod` for production.

## Architecture Overview

**Stack:** Django 4.2 backend with server-side rendered templates, vanilla JavaScript, SQLite (dev) / configurable DB (prod). No frontend build step.

**Settings split:** `CULTCALLE/settings/base.py` → shared config; `local.py` → DEBUG=True, SQLite, console email; `prod.py` → HTTPS, SMTP, production DB/Stripe/Skydrop keys. All secrets via `python-decouple` from a `.env` file.

**Single Django app:** All business logic lives in `tienda/`. The main entry points are:
- `tienda/models.py` (722 lines, 20+ models) — see model groups below
- `tienda/views.py` (1,316 lines) — all views for e-commerce, checkout, design creator, admin actions
- `tienda/admin.py` — heavily customized Django admin with POS, cash register, inventory matrix, bank reconciliation

**Model groups in `tienda/models.py`:**
- **E-commerce:** `Categoria`, `Subcategoria`, `Producto`, `ProductVariant`, `Order`, `OrderItem`, `Carrito`
- **Shipping:** `ShippingAddress`, `ShippingUpdate` (Skydrop integration)
- **Inventory:** `InventoryMovement`
- **Reviews:** `Reseña`
- **Accounting:** `ExpenseCategory`, `Expense`, `BusinessPayment`, `AccountingAccount`, `JournalEntry`, `JournalEntryLine`, `AccountingPeriodClose`, `MoneyAccount`, `BankMovement`, `CreditCardAccount`, `CreditCardStatement`
- **POS:** `CashRegisterClosure`

**Key external integrations:**
- **Stripe** — payments + webhook at `/stripe/webhook/`
- **Skydrop** (`tienda/skydrop.py`) — shipping/logistics + webhook
- **django-allauth** — Google OAuth + email auth
- **Meta Pixel** — injected via template context processor

**Admin UI:** Uses `django-jazzmin`. The admin has significant custom views for POS, purchase receipts, pending orders, accounting dashboard, and bank reconciliation with auto-match logic. Custom admin templates live in `templates/admin/`.

**Design Creator:** `templates/tienda/design_creator.html` (77KB) is a large self-contained canvas-based design tool (v3) with layers, shapes, effects, alignment, PNG export, and multi-select. Its supporting views are in `tienda/views.py` (design_creator, delete_design, catalogo_diseños, etc.) and user designs are stored under `media/diseños_propios/`.

**URL structure:**
- `CULTCALLE/urls.py` — root router, includes `tienda.urls` and allauth
- `tienda/urls.py` — all app routes (store, cart, checkout, orders, design creator, webhooks)

**Signals:** `tienda/signals.py` handles shipping status change email notifications. **Template tags:** `tienda/templatetags/` provides custom filters used in templates.
