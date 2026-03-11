import json
import logging

from odoo import http
from odoo.exceptions import UserError, ValidationError
from odoo.http import request

_logger = logging.getLogger(__name__)


class BaseApiController(http.Controller):
    """
    Shared HTTP helpers used by this module's controllers.

    Keeping these helpers local avoids dependencies on other custom addons.
    """

    def _is_json_request(self):
        return getattr(request, "_request_type", "http") == "json"

    def _json(self, payload, status=200):
        # Support both `type="json"` and `type="http"` routes consistently.
        if self._is_json_request():
            if isinstance(payload, dict):
                payload.setdefault("http_status", status)
            return payload
        body = json.dumps(payload)
        headers = [("Content-Type", "application/json")]
        response = request.make_response(body, headers=headers)
        response.status_code = status
        return response

    def _error(self, message, code, status=400):
        return self._json({"status": "error", "message": message, "code": code}, status=status)

    def _parse_json_body(self):
        # Odoo JSON routes can wrap params under {"params": {...}} depending on the client.
        if self._is_json_request():
            payload = request.jsonrequest or {}
            if isinstance(payload, dict) and "params" in payload and isinstance(payload["params"], dict):
                payload = payload["params"]
            if not isinstance(payload, dict):
                raise ValidationError("Invalid JSON body.")
            return b"", payload

        raw_body = request.httprequest.data or b"{}"
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except ValueError:
            raise ValidationError("Invalid JSON body.")
        return raw_body, payload

    def _verify_shared_token(self):
        token = request.httprequest.headers.get("X-Odoo-Token")
        verifier = request.env["odoo_warehouse_integration.security.service"].sudo()
        return verifier.verify_inbound_token(token)

    def _parse_bool(self, value, default=True):
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "y", "on"):
            return True
        if text in ("0", "false", "no", "n", "off"):
            return False
        return default


class OdooWarehouseIntegrationController(BaseApiController):
    """
    Consolidated controller layer that exposes a unified API surface.

    This controller intentionally delegates the business logic to services:
    - `odoo_warehouse_integration.inbound.service` for inbound order/product operations,
    - `odoo_warehouse_integration.stock.service` for warehouse stock reads.
    """

    # -------------------------------------------------------------------------
    # Inbound API (external system -> Odoo)
    # -------------------------------------------------------------------------

    @http.route(
        "/api/v1/products/resolve/<int:template_id>",
        type="http",
        auth="none",
        methods=["GET"],
        csrf=False,
    )
    def resolve_product_template(self, template_id, **_kwargs):
        """
        Resolve a product.template ID to its product.product variant IDs.

        Security: requires `X-Odoo-Token` header (shared token configured in settings).
        """
        try:
            if not self._verify_shared_token():
                return self._error("Invalid token", "INVALID_TOKEN", status=401)

            template = request.env["product.template"].sudo().browse(int(template_id)).exists()
            if not template:
                return self._error("Product template not found.", "VALIDATION_ERROR", status=400)

            variants = template.product_variant_ids
            variant_ids = variants.ids
            default_variant = template.product_variant_id

            return self._json(
                {
                    "template_id": template.id,
                    "template_name": template.name,
                    "variant_ids": variant_ids,
                    "default_variant_id": default_variant.id if default_variant else None,
                },
                status=200,
            )
        except (ValidationError, UserError) as exc:
            return self._error(str(exc), "VALIDATION_ERROR", status=400)
        except Exception as exc:
            _logger.exception("Unhandled error in resolve_product_template")
            return self._error(f"Internal server error: {str(exc)}", "INTERNAL_SERVER_ERROR", status=500)

    @http.route(
        "/api/v1/orders",
        type="json",
        auth="none",
        methods=["POST"],
        csrf=False,
    )
    def inbound_order_create(self):
        """
        Create a sales order from an external system payload.

        Security: requires `X-Odoo-Token` header (shared token configured in settings).
        """
        try:
            _raw_body, payload = self._parse_json_body()
            if not self._verify_shared_token():
                return self._error("Invalid token", "INVALID_TOKEN", status=401)
            result = (
                request.env["odoo_warehouse_integration.inbound.service"]
                .sudo()
                .create_order_from_frontend(payload)
            )
            return self._json(result, status=200)
        except (ValidationError, UserError) as exc:
            message = str(exc)
            code = "VALIDATION_ERROR"
            if "Stock reservation failed" in message:
                code = "STOCK_UNAVAILABLE"
            return self._error(message, code, status=400)
        except Exception as exc:
            _logger.exception("Unhandled error in inbound_order_create")
            return self._error(f"Internal server error: {str(exc)}", "INTERNAL_SERVER_ERROR", status=500)

    # -------------------------------------------------------------------------
    # Warehouse stock API (external system -> Odoo, read-only)
    # -------------------------------------------------------------------------

    @http.route(
        "/api/v1/warehouse/stock/<int:product_id>",
        type="http",
        auth="none",
        methods=["GET"],
        csrf=False,
    )
    def warehouse_stock(self, product_id, **_kwargs):
        """
        Return product stock quantities by warehouse.

        Query params (optional):
            - strict: bool (default True) -> uses free_qty instead of qty_available
            - include_zero: bool (default True) -> include warehouses with 0 qty

        Security: requires `X-Odoo-Token` header (shared token configured in settings).
        """
        try:
            if not self._verify_shared_token():
                return self._error("Invalid token", "INVALID_TOKEN", status=401)

            strict = self._parse_bool(request.params.get("strict"), default=True)
            include_zero = self._parse_bool(request.params.get("include_zero"), default=True)

            result = (
                request.env["odoo_warehouse_integration.stock.service"]
                .sudo()
                .get_product_stock_by_warehouse(
                    product_id=product_id,
                    include_zero=include_zero,
                    strict=strict,
                )
            )
            return self._json(result, status=200)
        except (ValidationError, UserError) as exc:
            return self._error(str(exc), "VALIDATION_ERROR", status=400)
        except Exception as exc:
            _logger.exception("Unhandled error in warehouse_stock")
            return self._error(f"Internal server error: {str(exc)}", "INTERNAL_SERVER_ERROR", status=500)
