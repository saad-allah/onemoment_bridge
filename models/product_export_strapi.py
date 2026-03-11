import base64
import csv
import io
from datetime import datetime

from odoo import api, fields, models


class ProductProduct(models.Model):
    _inherit = "product.product"

    def _get_first_existing_field_value(self, record, field_names):
        for field_name in field_names:
            if field_name in record._fields:
                value = record[field_name]
                return value or ""
        return ""

    def _get_public_pricelist(self):
        """Return the standard 'Public Pricelist' when available."""
        try:
            pricelist = self.env.ref("product.list0")
        except Exception:
            pricelist = self.env["product.pricelist"].sudo().search(
                [("name", "ilike", "Public Pricelist")],
                limit=1,
            )
        return pricelist.exists()

    def _template_has_variants(self, product):
        template = product.product_tmpl_id
        if "product_variant_count" in template._fields:
            return (template.product_variant_count or 0) > 1
        return len(template.product_variant_ids) > 1

    def _get_base_export_price(self, product):
        """
        Base export price (no pricelist rules):
        - If template has variants: template list_price + variant price_extra
        - If template has no variants: template list_price
        """
        template_price = product.product_tmpl_id.list_price or 0.0
        if not self._template_has_variants(product):
            return template_price
        extra = product.price_extra if "price_extra" in product._fields else 0.0
        return template_price + extra

    def _get_public_pricelist_price(self, product, qty=1.0, partner=None, date=None):
        """
        Compute product price using the Public Pricelist rules (Odoo 14).
        Returns None when no rule is applicable or computation fails.
        """
        pricelist = self._get_public_pricelist()
        if not pricelist or not pricelist.item_ids:
            return None

        partner = partner or self.env.user.partner_id
        date = date or fields.Date.context_today(self)
        uom_id = product.uom_id.id if "uom_id" in product._fields and product.uom_id else False

        try:
            prices = pricelist._compute_price_rule(
                [(product, qty, partner)],
                date=date,
                uom_id=uom_id,
            )
        except Exception:
            return None

        price_rule = prices.get(product.id)
        if not price_rule:
            return None
        price = price_rule[0]
        return None if price is None or price is False else price

    def _get_export_price(self, product, qty=1.0):
        """
        Export price = public pricelist price when available, otherwise base export price.
        """
        price = self._get_public_pricelist_price(product, qty=qty)
        return self._get_base_export_price(product) if price is None else price

    attributes_name = fields.Char(
        string="Attributes Name",
        compute="_compute_variant_attributes_display",
        store=False,
    )
    attributes_value = fields.Char(
        string="Attributes Value",
        compute="_compute_variant_attributes_display",
        store=False,
    )

    @api.depends(
        "product_template_attribute_value_ids",
        "product_template_attribute_value_ids.attribute_id.name",
        "product_template_attribute_value_ids.product_attribute_value_id.name",
        "product_template_attribute_value_ids.name",
    )
    def _compute_variant_attributes_display(self):
        for variant in self:
            attributes = variant.product_template_attribute_value_ids.sorted(
                key=lambda value: (
                    value.attribute_id.sequence,
                    value.attribute_id.id,
                    value.id,
                )
            )
            variant.attributes_name = ",".join(
                attributes.mapped("attribute_id.name")
            )
            variant.attributes_value = ",".join(
                [
                    value.product_attribute_value_id.name or value.name or ""
                    for value in attributes
                ]
            )

    def action_export_for_strapi(self):
        fieldnames = [
            "ID Variante",
            "ID Modèle",
            "Nom Produit",
            "Attribut",
            "Valeur",
            "SKU",
            "Prix",
            "Qté",
            "Image Principale",
            "Images Galerie",
            "Description Courte",
            "Description Longue",
            "Catégories",
            "Conseils d’Utilisation",
            "Ingrédients",
            "Marque",
            "Groupe 1",
            "Valeur 1",
            "Groupe 2",
            "Valeur 2",
            "Groupe 3",
            "Valeur 3",
        ]
        rows = []

        for variant in self:
            template = variant.product_tmpl_id
            price = self._get_export_price(variant, qty=1.0)

            rows.append(
                {
                    "ID Variante": variant.id,
                    "ID Modèle": template.id,
                    "Nom Produit": template.name or "",
                    "Attribut": variant.attributes_name or "",
                    "Valeur": variant.attributes_value or "",
                    "SKU": variant.default_code or "",
                    "Prix": price,
                    "Qté": variant.qty_available,
                    "Image Principale": "",
                    "Images Galerie": "",
                    "Description Courte": "",
                    "Description Longue": "",
                    "Catégories": "",
                    "Conseils d’Utilisation": "",
                    "Ingrédients": "",
                    "Marque": "",
                    "Groupe 1": "",
                    "Valeur 1": "",
                    "Groupe 2": "",
                    "Valeur 2": "",
                    "Groupe 3": "",
                    "Valeur 3": "",
                }
            )

        output = io.StringIO()
        writer = csv.DictWriter(
            output,
            fieldnames=fieldnames,
        )
        writer.writeheader()
        writer.writerows(rows)

        filename = "strapi_products_%s.csv" % datetime.now().strftime("%Y%m%d_%H%M%S")
        attachment = self.env["ir.attachment"].create(
            {
                "name": filename,
                "type": "binary",
                "mimetype": "text/csv",
                "datas": base64.b64encode(output.getvalue().encode("utf-8")),
                "res_model": "product.product",
                "res_id": self[0].id if self else False,
            }
        )

        return {
            "type": "ir.actions.act_url",
            "url": "/web/content/%s?download=true" % attachment.id,
            "target": "self",
        }
