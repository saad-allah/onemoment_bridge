from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    odoo_warehouse_integration_shared_api_token = fields.Char(
        string="Shared API Token",
        config_parameter="odoo_warehouse_integration.shared_api_token",
        help="Shared token expected in the X-Odoo-Token header for API calls.",
    )
