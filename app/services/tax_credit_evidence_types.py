from enum import StrEnum


class TaxCreditEvidenceType(StrEnum):
    """
    Durable legal evidence that may support Ukrainian INPUT VAT
    tax-credit recognition.

    This enum identifies the evidence category only.

    Whether a concrete evidence row creates available INPUT VAT
    credit is determined later by the tax-credit recognition
    service and must remain fail-closed.
    """

    REGISTERED_TAX_INVOICE = "registered_tax_invoice"
    REGISTERED_ADJUSTMENT = "registered_adjustment"
    CUSTOMS_DECLARATION = "customs_declaration"
    ARTICLE_201_11_DOCUMENT = "article_201_11_document"
    NONRESIDENT_SELF_INVOICE = "nonresident_self_invoice"
