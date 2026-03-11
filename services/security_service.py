from odoo import _, models
from odoo.exceptions import ValidationError


class SecurityService(models.AbstractModel):
    """
    Shared-token helper used by inbound endpoints.
    """

    _name = "odoo_warehouse_integration.security.service"
    _description = "Shared token security helper"

    def get_shared_token(self):
        params = self.env["ir.config_parameter"].sudo()
        token = params.get_param("odoo_warehouse_integration.shared_api_token")
        if not token:
            raise ValidationError(
                _(
                    "Missing configuration: set `odoo_warehouse_integration.shared_api_token` "
                    "in Settings."
                )
            )
        return token

    def verify_inbound_token(self, token):
        if not token:
            return False
        return token == self.get_shared_token()

