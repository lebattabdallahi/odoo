# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from datetime import timedelta

from odoo import SUPERUSER_ID, api, fields, models, _
from odoo.exceptions import ValidationError
from odoo.tools import is_html_empty

from odoo.addons.sale.models.sale_order import READONLY_FIELD_STATES


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    sale_order_template_id = fields.Many2one(
        comodel_name='sale.order.template',
        string="Quotation Template",
        compute='_compute_sale_order_template_id',
        store=True, readonly=False, check_company=True, precompute=True,
        states=READONLY_FIELD_STATES,
        domain="['|', ('company_id', '=', False), ('company_id', '=', company_id)]")
    sale_order_option_ids = fields.One2many(
        comodel_name='sale.order.option', inverse_name='order_id',
        string="Optional Products Lines",
        states=READONLY_FIELD_STATES,
        copy=True)

    #=== COMPUTE METHODS ===#

    @api.depends('company_id')
    def _compute_sale_order_template_id(self):
        for order in self:
            if order._origin.id:  # If record has already been saved
                # 1) Do NOT update existing SO's template and dependent fields
                # Especially when installing sale_management in a db
                # already containing SO records
                # 2) Only apply the company default if the company is modified before the record is saved
                # to make sure the lines are not magically reset when the company is modified (internal odoo issue)
                continue
            company_template = order.company_id.sale_order_template_id
            if company_template and order.sale_order_template_id != company_template:
                order.sale_order_template_id = order.company_id.sale_order_template_id.id

    @api.depends('partner_id', 'sale_order_template_id')
    def _compute_note(self):
        super()._compute_note()
        for order in self.filtered('sale_order_template_id'):
            template = order.sale_order_template_id.with_context(lang=order.partner_id.lang)
            order.note = template.note if not is_html_empty(template.note) else order.note

    @api.depends('sale_order_template_id')
    def _compute_require_signature(self):
        super()._compute_require_signature()
        for order in self.filtered('sale_order_template_id'):
            order.require_signature = order.sale_order_template_id.require_signature

    @api.depends('sale_order_template_id')
    def _compute_require_payment(self):
        super()._compute_require_payment()
        for order in self.filtered('sale_order_template_id'):
            order.require_payment = order.sale_order_template_id.require_payment

    @api.depends('sale_order_template_id')
    def _compute_validity_date(self):
        super()._compute_validity_date()
        for order in self.filtered('sale_order_template_id'):
            validity_days = order.sale_order_template_id.number_of_days
            if validity_days > 0:
                order.validity_date = fields.Date.context_today(order) + timedelta(validity_days)

    #=== CONSTRAINT METHODS ===#

    @api.constrains('company_id', 'sale_order_option_ids')
    def _check_optional_product_company_id(self):
        for order in self:
            companies = order.sale_order_option_ids.product_id.company_id
            if companies and companies != order.company_id:
                bad_products = order.sale_order_option_ids.product_id.filtered(lambda p: p.company_id and p.company_id != order.company_id)
                raise ValidationError(_(
                    "Your quotation contains products from company %(product_company)s whereas your quotation belongs to company %(quote_company)s. \n Please change the company of your quotation or remove the products from other companies (%(bad_products)s).",
                    product_company=', '.join(companies.mapped('display_name')),
                    quote_company=order.company_id.display_name,
                    bad_products=', '.join(bad_products.mapped('display_name')),
                ))

    #=== ONCHANGE METHODS ===#

    # TODO convert to compute ???
    @api.onchange('sale_order_template_id')
    def _onchange_sale_order_template_id(self):
        sale_order_template = self.sale_order_template_id.with_context(lang=self.partner_id.lang)

        order_lines_data = [fields.Command.clear()]
        order_lines_data += [
            fields.Command.create(
                self._compute_line_data_for_template_change(line)
            )
            for line in sale_order_template.sale_order_template_line_ids
        ]

        self.order_line = order_lines_data

        option_lines_data = [fields.Command.clear()]
        option_lines_data += [
            fields.Command.create(
                self._compute_option_data_for_template_change(option)
            )
            for option in sale_order_template.sale_order_template_option_ids
        ]

        self.sale_order_option_ids = option_lines_data

    # TODO delegate to sub models (note: overridden in sale_quotation_builder)

    def _compute_line_data_for_template_change(self, line):
        return {
            'sequence': line.sequence,
            'display_type': line.display_type,
            'name': line.name,
            'product_id': line.product_id.id,
            'product_uom_qty': line.product_uom_qty,
            'product_uom': line.product_uom_id.id,
        }

    def _compute_option_data_for_template_change(self, option):
        return {
            'name': option.name,
            'product_id': option.product_id.id,
            'quantity': option.quantity,
            'uom_id': option.uom_id.id,
        }

    #=== ACTION METHODS ===#

    def action_confirm(self):
        res = super().action_confirm()
        if self.env.su:
            self = self.with_user(SUPERUSER_ID)

        for order in self:
            if order.sale_order_template_id and order.sale_order_template_id.mail_template_id:
                order.sale_order_template_id.mail_template_id.send_mail(order.id)
        return res

    def update_prices(self):
        super().update_prices()
        # Special case: we want to overwrite the existing discount on update_prices call
        # i.e. to make sure the discount is correctly reset
        # if pricelist discount_policy is different than when the price was first computed.
        self.sale_order_option_ids.discount = 0.0
        self.sale_order_option_ids._compute_price_unit()
        self.sale_order_option_ids._compute_discount()
