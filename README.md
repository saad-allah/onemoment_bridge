# Odoo Warehouse Integration (Odoo 14)

A lightweight Odoo 14 addon that exposes a clean API for stock checks and order creation, plus a Strapi-friendly CSV export. All endpoints are secured with a shared token.

---

## Features

- Stock by warehouse (read-only JSON API)
- Create sales orders from external systems
- Resolve template to variant IDs for stock calls
- Strapi CSV export from product variants list view
- Shared token security (`X-Odoo-Token`)

---

## Requirements

- Odoo 14
- Dependencies: `base`, `product`, `sale`, `stock`

---

## Installation

1. Place this addon inside your addons path.
2. Restart Odoo.
3. Apps → Update Apps List.
4. Install **Odoo Warehouse Integration**.

---

## Configuration

1. Settings → General Settings → Warehouse Integration.
2. Set **Shared API Token**.

All API calls must include:

```
X-Odoo-Token: <shared_token>
```

---

## Usage

Base URL: `http://<odoo-host>` (local dev example: `http://localhost:8069`)

### 1) Stock by product (per warehouse)

**Route**

- `GET /api/v1/warehouse/stock/<product_id>`

**Query params (optional)**

- `strict` (default `true`): `free_qty` (unreserved) if `true`, `qty_available` (on-hand) if `false`
- `include_zero` (default `true`): include warehouses with 0 qty

**Example request**

```bash
curl -X GET \
  -H "X-Odoo-Token: <shared_token>" \
  "http://<odoo-host>/api/v1/warehouse/stock/13?strict=true&include_zero=true"
```

**Example response**

```json
{
  "product_id": 13,
  "product_name": "Demo Product",
  "strict": true,
  "warehouses": [
    {
      "warehouse_id": 1,
      "warehouse": "Main Warehouse",
      "code": "WH",
      "available_qty": 10,
      "status": "available"
    }
  ]
}
```

**If you only have a template ID**

Resolve template → variant IDs, then call the stock endpoint with the variant ID.

```bash
# resolve template -> variant IDs (template id = 15)
curl -X GET \
  -H "X-Odoo-Token: <shared_token>" \
  "http://<odoo-host>/api/v1/products/resolve/15"
```

Example response:

```json
{
  "template_id": 15,
  "template_name": "Customizable Desk",
  "variant_ids": [13],
  "default_variant_id": 13
}
```

Now call stock by variant id:

```bash
curl -X GET \
  -H "X-Odoo-Token: <shared_token>" \
  "http://<odoo-host>/api/v1/warehouse/stock/13?strict=true&include_zero=true"
```

Example response:

```json
{
  "product_id": 13,
  "product_name": "Demo Product",
  "strict": true,
  "warehouses": [
    {
      "warehouse_id": 1,
      "warehouse": "Main Warehouse",
      "code": "WH",
      "available_qty": 10,
      "status": "available"
    }
  ]
}
```

---

### 2) Create sales order

**Route**

- `POST /api/v1/orders`

**Body**

```json
{
  "partner_id": 6,
  "external_reference": "WEB-29",
  "warehouse_id": 2,
  "unavailable_action": "backorder",
  "lines": [
    {"product_id": 12, "qty": 2},
    {"product_id": 14, "qty": 1}
  ]
}
```

**Required fields**

- `partner_id`: Odoo customer (`res.partner`) ID
- `external_reference`: unique idempotency key from your system
- `lines`: array of `{product_id, qty}` (qty must be > 0)

**Optional fields**

- `warehouse_id`: warehouse to fulfill from (defaults to warehouse with code `ecom` in the company)
- `unavailable_action`:
  - `backorder` (default): keep missing items as backorder
  - `adjust`: reduce ordered quantities to available stock

**Example request**

```bash
curl -X POST \
  -H "Content-Type: application/json" \
  -H "X-Odoo-Token: <shared_token>" \
  -d '{
    "partner_id": 6,
    "external_reference": "WEB-29",
    "warehouse_id": 2,
    "unavailable_action": "backorder",
    "lines": [
      {"product_id": 12, "qty": 2},
      {"product_id": 14, "qty": 1}
    ]
  }' \
  http://<odoo-host>/api/v1/orders
```

Example response:

```json
{
  "status": "success",
  "message": "Order created and fully reserved.",
  "code": "ORDER_CREATED",
  "odoo_order_id": 1203,
  "order_name": "SO1203",
  "state": "sale",
  "lines": [
    {
      "product_id": 12,
      "name": "Product A",
      "requested_qty": 2,
      "available_qty": 2,
      "status": "reserved"
    }
  ]
}
```

---

### 3) Strapi CSV Export

From **Products → Products (list view)**:

1. Select one or more variants.
2. Click **Action → Export for Strapi**.

CSV headers:

```
ID Variante,ID Modèle,Nom Produit,Attribut,Valeur,SKU,Prix,Qté,Image Principale,Images Galerie,Description Courte,Description Longue,Catégories,Conseils d’Utilisation,Ingrédients,Marque,Groupe 1,Valeur 1,Groupe 2,Valeur 2,Groupe 3,Valeur 3
```

Only the first 8 columns are filled by default. The rest are included but empty.

---

## Contributing

Issues and pull requests are welcome.

---

## License

LGPL-3
