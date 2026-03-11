from odoo import models
from odoo.exceptions import ValidationError

class WarehouseStockService(models.AbstractModel):
    """
    Service that centralizes the "stock by warehouse" logic.

    This is intentionally kept separate from controllers so it can be reused by:
    - HTTP controllers (JSON endpoints),
    - scheduled jobs / server actions,
    - other Python code that needs consistent stock computation.
    """

    _name = "odoo_warehouse_integration.stock.service"
    _description = "Warehouse stock service"

    def get_product_stock_by_warehouse(self, product_id, include_zero=True, strict=True):
        """
        Return per-warehouse quantities for a given product variant.

        Args:
            product_id (int): `product.product` id.
            include_zero (bool): If False, warehouses with 0 qty are filtered out.
            strict (bool): If True uses `free_qty` (unreserved). If False uses `qty_available` (on-hand).

        Returns:
            dict: Payload suitable for JSON responses.
        """
        product = self.env["product.product"].sudo().browse(int(product_id)).exists()
        if not product:
            raise ValidationError("Product not found.")

        warehouses = self.env["stock.warehouse"].sudo().search([])
        results = []

        for warehouse in warehouses:
            # Quantity fields are context-sensitive. Force warehouse + company for consistent results,
            # especially in multi-company setups.
            company = warehouse.company_id or self.env.company
            product_ctx = (
                product.with_company(company)
                .with_context(warehouse=warehouse.id, allowed_company_ids=[company.id])
            )
            qty = product_ctx.free_qty if strict else product_ctx.qty_available
            qty = float(qty or 0.0)

            if not include_zero and qty <= 0:
                continue

            results.append(
                {
                    "warehouse_id": warehouse.id,
                    "warehouse": warehouse.name,
                   "code": warehouse.code,  
                    "available_qty": qty,
                    # Keep a human-readable status for parity with the legacy addon response.
                    "status": "available" if qty > 0 else "unavailable",
                }
            )

        return {
            "product_id": product.id,
            "product_name": product.display_name,
            "strict": bool(strict),
            "warehouses": results,
        }
