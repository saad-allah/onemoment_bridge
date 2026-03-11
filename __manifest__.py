{
    "name": "Odoo Warehouse Integration",
    "version": "14.0.1.0.0",
    "category": "Integration",
    "summary": "Unified bridge between Odoo and a warehouse stock API",
    "author": "khawla",
    "license": "LGPL-3",
    "depends": ["base", "product", "sale", "stock"],
    "data": [
        "views/res_config_settings_views.xml",
        "views/product_export_views.xml",
    ],
    "installable": True,
    "application": False,
}
