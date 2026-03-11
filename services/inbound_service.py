import logging

from odoo import _, fields, models
from odoo.exceptions import ValidationError

try:
    from odoo.addons.queue_job.exception import RetryableJobError
except Exception:  # pragma: no cover
    class RetryableJobError(Exception):
        pass


_logger = logging.getLogger(__name__)


class InboundService(models.AbstractModel):
    """
    Business logic used by inbound endpoints (external system -> Odoo).
    """

    _name = "odoo_warehouse_integration.inbound.service"
    _description = "Inbound API service"

   
    UNAVAILABLE_ACTIONS = {"backorder", "adjust"}

    # ------------------------------
    # Generic helpers
    # ------------------------------

    def _to_int(self, value, field_name):
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValidationError(_("%s must be a valid integer.") % field_name)

    def _to_float(self, value, field_name):
        try:
            return float(value)
        except (TypeError, ValueError):
            raise ValidationError(_("%s must be a valid number.") % field_name)


    def _normalize_unavailable_action(self, action):
        normalized = (action or "backorder").strip().lower()
        if normalized not in self.UNAVAILABLE_ACTIONS:
            raise ValidationError(_("`unavailable_action` must be 'backorder' or 'adjust'."))
        return normalized

    def _validate_order_payload(self, payload):
        partner_id = payload.get("partner_id")
        lines = payload.get("lines", [])
        external_reference = (payload.get("external_reference") or "").strip()

        if not partner_id:
            raise ValidationError(_("`partner_id` is required."))
        if not external_reference:
            raise ValidationError(_("`external_reference` is required for idempotency."))
        if not isinstance(lines, list) or not lines:
            raise ValidationError(_("`lines` is required and cannot be empty."))

        return partner_id, lines, external_reference

    def _find_existing_order(self, external_reference):
        return self.env["sale.order"].sudo().search(
            [("client_order_ref", "=", external_reference)],
            order="id desc",
            limit=1,
        )

    def _acquire_idempotency_lock(self, external_reference):
        # Serialize same external_reference inside the DB transaction.
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"odoo_warehouse_integration:{external_reference}",),
        )

    def _build_response(self, order, status, message, code, lines):
        return {
            "status": status,
            "message": message,
            "code": code,
            "odoo_order_id": order.id if order else None,
            "order_name": order.name if order else None,
            "state": order.state if order else None,
            "lines": lines,
        }

    # ------------------------------
    # Warehouse resolution
    # ------------------------------

    def _ensure_company(self, partner, payload):
        company = partner.company_id

        warehouse_id = payload.get("warehouse_id")
        if not company and warehouse_id:
            warehouse = self.env["stock.warehouse"].sudo().browse(
                self._to_int(warehouse_id, "`warehouse_id`")
            ).exists()
            if warehouse:
                company = warehouse.company_id

        if not company:
            company = self.env.company
        if not company:
            company = self.env["res.company"].sudo().search([], limit=1)
        if not company:
            raise ValidationError(_("No company found."))
        return company

    def _with_company_context(self, company):
        # Ensure ORM domains like ('company_id', '=', False) translate correctly
        # (IS NULL) and company-dependent properties resolve consistently.
        return self.with_context(allowed_company_ids=[company.id]).with_company(company)

    def _resolve_warehouse(self, payload, company):
        # 1) use payload warehouse_id
        # 2) fallback to warehouse code "ecom"
        warehouse_id = payload.get("warehouse_id")

        if warehouse_id:
            warehouse = self.env["stock.warehouse"].sudo().browse(
                self._to_int(warehouse_id, "`warehouse_id`")
            ).exists()
            if not warehouse:
                raise ValidationError(_("Warehouse not found."))
            if warehouse.company_id != company:
                raise ValidationError(_("Warehouse company mismatch."))
            return warehouse

        warehouse = self.env["stock.warehouse"].sudo().search(
            [("company_id", "=", company.id), ("code", "=", "ecom") ],
            limit=1,
        )
        if not warehouse:
            raise ValidationError(_("Default eCommerce warehouse with code 'ecom' not found."))
        return warehouse

    # ------------------------------
    # Pricelist / currency
    # ------------------------------

    def _ensure_partner_pricelist_rate(self, partner):
        pricelist = partner.property_product_pricelist
        if not pricelist:
            raise ValidationError(_("Customer has no pricelist configured."))

        company = partner.company_id or self.env.company
        currency = pricelist.currency_id
        if currency == company.currency_id:
            return

        today = fields.Date.context_today(self)
        rate = self.env["res.currency.rate"].sudo().search(
            [
                ("currency_id", "=", currency.id),
                ("name", "<=", today),
                "|",
                ("company_id", "=", None),
                ("company_id", "=", company.id),
            ],
            order="name desc",
            limit=1,
        )
        if not rate:
            raise ValidationError(_("Missing exchange rate for currency %s.") % currency.display_name)

    def _compute_price(self, pricelist, product, qty, partner):
        # Use named args to stay compatible with Odoo signatures where the 3rd
        # positional parameter can be UoM instead of partner.
        try:
            # Prefer rule-based computation since it correctly incorporates
            # variant extra prices and pricelist rules (including Public Pricelist).
            if hasattr(pricelist, "_compute_price_rule"):
                date = fields.Date.context_today(self)
                rules = pricelist._compute_price_rule(
                    [(product, qty, partner)],
                    date=date,
                    uom_id=product.uom_id.id,
                )
                price, _rule_id = rules.get(product.id, (False, False))
            else:
                price = False

            if price is False or price is None:
                price = pricelist._get_product_price(product, qty, partner=partner)
        except Exception as exc:
            # Some databases/customizations end up with SQL errors around
            # company_id comparisons (e.g. integer vs boolean). Keep the API
            # usable by falling back to the public list price.
            _logger.exception("Pricelist price computation failed, falling back to list price: %s", exc)
            return product.lst_price

        if price is False or price is None:
            raise ValidationError(_("Could not compute price for product %s.") % product.display_name)
        return price

    # ------------------------------
    # Stock / lines helpers
    # ------------------------------

    def _get_available_qty(self, product, warehouse, strict=True):
        if product.type != "product":
            return 10**9
        # Quantity fields are context-sensitive; force warehouse + company so
        # multi-company setups don't accidentally read "0" stock.
        company = warehouse.company_id
        product_wh = (
            product.with_company(company)
            .with_context(warehouse=warehouse.id, allowed_company_ids=[company.id])
        )

        # strict=True uses unreserved stock; strict=False uses on-hand stock.
        qty = product_wh.free_qty if strict else product_wh.qty_available
        return max(qty or 0.0, 0.0)

    def _prepare_line_inputs(self, lines, pricelist, partner, warehouse, strict_stock=True):
        prepared = []
        for line in lines:
            product_id = self._to_int(line.get("product_id"), "`product_id`")
            requested_qty = self._to_float(line.get("qty"), "`qty`")
            if requested_qty <= 0:
                raise ValidationError(_("`qty` must be greater than 0."))

            product = self.env["product.product"].sudo().browse(product_id).exists()
            if not product:
                raise ValidationError(_("Invalid `product_id` in order line."))

            price_unit = self._compute_price(pricelist, product, requested_qty, partner)
            available_qty = min(
                requested_qty,
                self._get_available_qty(product, warehouse, strict=strict_stock),
            )
            if available_qty <= 0:
                status = "unavailable"
            elif available_qty < requested_qty:
                status = "backorder"
            else:
                status = "reserved"

            prepared.append(
                {
                    "product": product,
                    "name": product.display_name,
                    "requested_qty": requested_qty,
                    "available_qty": available_qty,
                    "status": status,
                    "price_unit": price_unit,
                }
            )
        return prepared

    def _build_order_lines(self, prepared_lines, adjust_to_available=False):
        commands = []
        for line in prepared_lines:
            qty = line["available_qty"] if adjust_to_available else line["requested_qty"]
            if qty <= 0:
                continue

            commands.append(
                (
                    0,
                    0,
                    {
                        "product_id": line["product"].id,
                        "product_uom_qty": qty,
                        "price_unit": line["price_unit"],
                        "name": line["name"],
                    },
                )
            )
        return commands

    def _format_line_statuses(self, prepared_lines):
        return [
            {
                "product_id": line["product"].id,
                "name": line["name"],
                "requested_qty": line["requested_qty"],
                "available_qty": line["available_qty"],
                "status": line["status"],
            }
            for line in prepared_lines
        ]

    def _serialize_existing_order_lines(self, order):
        result = []
        for line in order.order_line.filtered(lambda l: not l.display_type and l.product_id):
            result.append(
                {
                    "product_id": line.product_id.id,
                    "name": line.product_id.display_name,
                    "requested_qty": line.product_uom_qty,
                    "available_qty": line.product_uom_qty,
                    "status": "reserved" if order.state in ("sale", "done") else "backorder",
                }
            )
        return result

    # ------------------------------
    # Order creation worker
    # ------------------------------

    def _create_order_record(self, partner, company, pricelist, warehouse, external_reference, order_lines):
        if not order_lines:
            return None
        return (
            self.env["sale.order"]
            .sudo()
            .with_company(company)
            .create(
                {
                    "partner_id": partner.id,
                    "company_id": company.id,
                    "pricelist_id": pricelist.id,
                    "warehouse_id": warehouse.id,
                    "client_order_ref": external_reference,
                    "order_line": order_lines,
                }
            )
        )

    def  _process_order(self, payload, partner, company, pricelist, warehouse, external_reference):
        unavailable_action = self._normalize_unavailable_action(payload.get("unavailable_action"))
        prepared_lines = self._prepare_line_inputs(
            payload["lines"], pricelist, partner, warehouse, strict_stock=True
        )
        line_statuses = self._format_line_statuses(prepared_lines)

        adjust_to_available = unavailable_action == "adjust"
        order_lines = self._build_order_lines(prepared_lines, adjust_to_available=adjust_to_available)
        if not order_lines:
            return {
                "status": "failed",
                "message": "No product is currently available for immediate payment order.",
                "code": "OUT_OF_STOCK",
                "odoo_order_id": None,
                "order_name": None,
                "state": None,
                "lines": line_statuses,
            }

        order = self._create_order_record(
            partner, company, pricelist, warehouse, external_reference, order_lines
        )
        order.action_confirm()

        pickings = order.picking_ids.filtered(
            lambda p: p.state in ("confirmed", "waiting", "assigned")
        )
        if pickings:
            pickings.action_assign()

        has_shortage = any(line["status"] != "reserved" for line in line_statuses)
        if has_shortage and unavailable_action == "backorder":
            return self._build_response(
                order,
                "success",
                "Order confirmed with partial availability. Missing products remain pending/backorder.",
                "BACKORDER",
                line_statuses,
            )
        if has_shortage and unavailable_action == "adjust":
            return self._build_response(
                order,
                "success",
                "Order confirmed with adjusted quantities based on available stock.",
                "OUT_OF_STOCK",
                line_statuses,
            )

        return self._build_response(
            order,
            "success",
            "Order created and fully reserved.",
            "ORDER_CREATED",
            line_statuses,
        )

   
    def create_order_from_frontend(self, payload):
        partner_id, lines, external_reference = self._validate_order_payload(payload)
        payload = dict(payload or {})
        payload["lines"] = lines
        self._acquire_idempotency_lock(external_reference)

        existing = self._find_existing_order(external_reference)
        if existing:
            return self._build_response(
                existing,
                "success",
                "Order already exists",
                "ORDER_ALREADY_EXISTS",
                self._serialize_existing_order_lines(existing),
            )

        partner = self.env["res.partner"].sudo().browse(
            self._to_int(partner_id, "`partner_id`")
        ).exists()
        if not partner:
            raise ValidationError(_("Customer not found."))

        company = self._ensure_company(partner, payload)
        self = self._with_company_context(company)
        partner = partner.with_company(company)

        self._ensure_partner_pricelist_rate(partner)
        pricelist = partner.property_product_pricelist
        if not pricelist:
            raise ValidationError(_("Customer has no pricelist configured."))

        warehouse = self._resolve_warehouse(payload, company)

        return self._process_order(
    payload, partner, company, pricelist, warehouse, external_reference
)
