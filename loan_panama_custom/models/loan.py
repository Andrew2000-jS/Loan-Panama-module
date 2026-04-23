from datetime import date, datetime
from typing import List, Optional, Tuple

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class Loan(models.Model):
    _name = "loan.loan"
    _description = "General Info and Balance"

    name = fields.Char(
        string="Referencia de préstamo",
        compute="_compute_name",
        store=True,
        readonly=True,
    )
    partner_id = fields.Many2one("res.partner", string="Customer", required=True)

    principal_amount = fields.Float(
        string="Principal Amount", required=True, digits=(16, 2)
    )
    current_balance = fields.Float(
        string="Current Balance", compute="_compute_current_balance", digits=(16, 2)
    )
    annual_interest_rate = fields.Float(
        string="Annual Interest Rate", required=True, default=0.19, digits=(16, 4)
    )
    annual_feci_rate = fields.Float(
        string="Annual FECI Rate", default=0.01, digits=(16, 4)
    )
    feci_threshold = fields.Float(
        string="FECI Threshold",
        default=5000.0,
        help="Minimum balance to apply FECI",
        digits=(16, 2),
    )

    disbursement_date = fields.Date(
        string="Disbursement Date", required=True, default=fields.Date.context_today
    )
    next_due_date = fields.Date(string="Next Due Date")
    credit_officer = fields.Char(string="Credit Officer")
    account = fields.Char(string="Account")
    income_source = fields.Char(string="Income Source(s)")
    term_months = fields.Integer(string="Term (months)")

    monthly_installment = fields.Float(
        string="Monthly Installment",
        compute="_compute_monthly_installment",
        store=True,
        digits=(16, 2),
    )

    collateral = fields.Char(string="Collateral")
    dealer = fields.Char(string="Dealer")
    notes = fields.Text(string="Notes")

    loan_type = fields.Selection(
        [
            ("individual", "Individual"),
            ("corporate", "Corporate"),
            ("personal", "Personal"),
            ("auto", "Auto"),
            ("mortgage", "Mortgage"),
        ],
        string="Loan Type",
    )
    payment_frequency = fields.Selection(
        [
            ("monthly", "Monthly"),
            ("biweekly", "Biweekly"),
            ("weekly", "Weekly"),
            ("daily", "Daily"),
        ],
        string="Payment Frequency",
        default="monthly",
    )
    status = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("closed", "Closed"),
            ("defaulter", "Defaulter"),
        ],
        string="Status",
        default="draft",
        compute="_compute_status",
    )
    feci_exempt = fields.Boolean(
        string="FECI Exempt",
        default=False,
        help="Indicates if this loan is exempt from FECI calculation.",
    )

    loan_line_ids = fields.One2many("loan.line", "loan_id", string="Payment Lines")
    other_charge_ids = fields.One2many(
        "loan.other.charge", "loan_id", string="Other Charges"
    )

    @api.depends("create_date")
    def _compute_name(self):
        current_year = datetime.now().year

        for record in self:
            if record.id:
                year = record.create_date.year if record.create_date else current_year
                record.name = f"PREST-{year}-{str(record.id).zfill(4)}"
            else:
                record.name = f"PREST-{current_year}-XXXX"

    @api.depends("principal_amount", "term_months", "annual_interest_rate")
    def _compute_monthly_installment(self):
        for rec in self:
            principal = rec.principal_amount
            term = rec.term_months
            annual_rate = rec.annual_interest_rate

            if not principal or not term:
                rec.monthly_installment = 0.0
                continue

            if annual_rate > 0:
                monthly_rate = annual_rate / 12.0
                rec.monthly_installment = round(
                    (
                        principal
                        * (monthly_rate * (1 + monthly_rate) ** term)
                        / ((1 + monthly_rate) ** term - 1)
                    ),
                    2,
                )
            else:
                rec.monthly_installment = round(principal / term, 2)

    @api.constrains("term_months")
    def _check_term_months(self):
        for loan in self:
            if loan.term_months > 120 or loan.term_months < 1:
                raise ValidationError(_("The selected term is not valid."))

    @api.constrains("principal_amount")
    def _check_principal_amount(self):
        for loan in self:
            if loan.principal_amount is None or loan.principal_amount <= 0:
                raise ValidationError(_("Principal amount must be greater than zero."))

    @api.constrains("annual_interest_rate")
    def _check_annual_interest_rate(self):
        for loan in self:
            if loan.annual_interest_rate is None or loan.annual_interest_rate < 0:
                raise ValidationError(_("Annual interest rate cannot be negative."))
            if loan.annual_interest_rate > 2.0:
                raise ValidationError(
                    _("Annual interest rate exceeds allowed limit (200%).")
                )

    @api.constrains("annual_feci_rate")
    def _check_annual_feci_rate(self):
        for loan in self:
            if loan.annual_feci_rate is not None and loan.annual_feci_rate < 0:
                raise ValidationError(_("FECI rate cannot be negative."))

    @api.constrains("feci_threshold")
    def _check_feci_threshold(self):
        for loan in self:
            if loan.feci_threshold is not None and loan.feci_threshold < 0:
                raise ValidationError(_("FECI threshold cannot be negative."))

    @api.constrains("next_due_date", "disbursement_date")
    def _check_due_dates(self):
        for loan in self:
            if loan.next_due_date and loan.disbursement_date:
                if loan.next_due_date < loan.disbursement_date:
                    raise ValidationError(
                        _("Next due date cannot be before the disbursement date.")
                    )

    @api.constrains("payment_frequency")
    def _check_payment_frequency(self):
        valid = ["monthly", "biweekly", "weekly", "daily"]
        for loan in self:
            if loan.payment_frequency not in valid:
                raise ValidationError(_("The selected payment frequency is not valid."))

    @api.constrains("current_balance")
    def _check_current_balance(self):
        for loan in self:
            if loan.current_balance < 0:
                raise ValidationError(_("Balance cannot be negative."))

    @api.depends("loan_line_ids.capital_payment")
    def _compute_current_balance(self):
        for loan in self:
            try:
                total_paid = sum(
                    (line.capital_payment or 0.0) for line in loan.loan_line_ids
                )
                loan.current_balance = round(loan.principal_amount - total_paid, 2)
            except Exception as e:
                raise UserError(_("Error calculating current balance: %s") % str(e))

    @api.depends("current_balance", "next_due_date")
    def _compute_status(self):
        today = date.today()
        for loan in self:
            try:
                if loan.current_balance <= 0:
                    loan.status = "closed"
                elif loan.next_due_date and loan.next_due_date < today:
                    loan.status = "defaulter"
                else:
                    loan.status = "active"
            except Exception as e:
                raise UserError(_("Error calculating loan status: %s") % str(e))

    def action_loan_payment_wizard(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": "loan.payment.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_loan_id": self.id},
        }

    def action_register_payment(
        self, paid_amount: float, payment_date: Optional[date] = None, notes: str = ""
    ) -> None:
        self.ensure_one()

        try:
            payment_date = payment_date or date.today()

            if paid_amount is None:
                raise ValidationError(_("You must specify a payment amount."))

            if paid_amount <= 0:
                raise ValidationError(_("Payment amount must be greater than zero."))

            if payment_date < self.disbursement_date:
                raise ValidationError(
                    _("Payment date cannot be before the disbursement date.")
                )

            principal_balance = self._calculate_principal_balance()
            days_elapsed = self._calculate_days_since_last_payment(payment_date)

            remaining_amount = paid_amount
            applied_charge_ids, remaining_amount = self._apply_other_charges(
                remaining_amount
            )
            feci_payment, remaining_amount = self._calculate_feci(
                principal_balance, days_elapsed, remaining_amount
            )
            interest_payment, remaining_amount = self._calculate_interest(
                principal_balance, days_elapsed, remaining_amount
            )

            capital_payment = min(max(remaining_amount, 0), principal_balance)

            line = self._create_payment_line(
                payment_date=payment_date,
                paid_amount=paid_amount,
                capital_payment=capital_payment,
                feci_payment=feci_payment,
                interest_payment=interest_payment,
                principal_balance=round(principal_balance - capital_payment, 2),
                other_charge_ids=applied_charge_ids,
                notes=notes,
            )

            self._update_next_due_date(payment_date, line)
            self._compute_current_balance()
            self._compute_status()

        except ValidationError:
            raise
        except UserError:
            raise
        except Exception as e:
            raise UserError(
                _("An unexpected error occurred while registering the payment: %s")
                % str(e)
            )

    def _calculate_principal_balance(self) -> float:
        try:
            return round(
                self.principal_amount
                - sum((line.capital_payment or 0.0) for line in self.loan_line_ids),
                2,
            )
        except Exception as e:
            raise UserError(_("Error calculating principal balance: %s") % str(e))

    def _calculate_days_since_last_payment(self, payment_date: date) -> int:
        try:
            last_line = self.loan_line_ids.sorted(
                key=lambda l: l.movement_date, reverse=True
            )
            last_payment_date = (
                last_line[0].movement_date if last_line else self.disbursement_date
            )
            return max((payment_date - last_payment_date).days, 0)
        except Exception as e:
            raise UserError(_("Error calculating days since last payment: %s") % str(e))

    def _apply_other_charges(self, remaining_amount: float) -> Tuple[List[int], float]:
        try:
            applied_charges = []
            for charge in self.other_charge_ids.filtered(
                lambda c: c.pending_balance > 0
            ):
                if remaining_amount <= 0:
                    break
                apply_amt = min(remaining_amount, charge.pending_balance)
                charge.amount_paid += apply_amt
                remaining_amount -= apply_amt
                applied_charges.append(charge.id)
            return applied_charges, round(remaining_amount, 2)
        except Exception as e:
            raise UserError(_("Error applying other charges: %s") % str(e))

    def _calculate_feci(
        self, principal_balance: float, days: int, remaining_amount: float
    ) -> Tuple[float, float]:
        try:
            if principal_balance <= self.feci_threshold or self.feci_exempt:
                return 0.0, remaining_amount

            base = principal_balance

            total_feci = base * self.annual_feci_rate * days / 360
            feci_payment = min(remaining_amount, total_feci)

            return round(feci_payment, 2), round(remaining_amount - feci_payment, 2)
        except Exception as e:
            raise UserError(_("Error calculating FECI: %s") % str(e))

    def _calculate_interest(
        self, principal_balance: float, days: int, remaining_amount: float
    ) -> Tuple[float, float]:
        try:
            total_interest = principal_balance * self.annual_interest_rate * days / 360
            interest_payment = min(remaining_amount, total_interest)

            return round(interest_payment, 2), round(
                remaining_amount - interest_payment, 2
            )
        except Exception as e:
            raise UserError(_("Error calculating interest: %s") % str(e))

    def _create_payment_line(
        self,
        payment_date,
        paid_amount,
        capital_payment,
        feci_payment,
        interest_payment,
        principal_balance,
        other_charge_ids,
        notes,
    ):
        try:
            return self.env["loan.line"].create(
                {
                    "loan_id": self.id,
                    "movement_date": payment_date,
                    "paid_amount": paid_amount,
                    "interest": interest_payment,
                    "feci": feci_payment,
                    "capital_payment": capital_payment,
                    "principal_balance": principal_balance,
                    "other_charge_ids": [(6, 0, other_charge_ids)],
                    "notes": notes,
                }
            )
        except Exception as e:
            raise UserError(_("Error creating payment line: %s") % str(e))

    def _update_next_due_date(self, payment_date: date, line) -> None:
        try:
            delta_mapping = {
                "monthly": relativedelta(months=1),
                "biweekly": relativedelta(days=15),
                "weekly": relativedelta(weeks=1),
                "daily": relativedelta(days=1),
            }

            delta = delta_mapping.get(self.payment_frequency, relativedelta(months=1))

            base_date = self.next_due_date or self.disbursement_date
            self.next_due_date = base_date + delta

        except Exception as e:
            raise UserError(_("Error updating next due date: %s") % str(e))
