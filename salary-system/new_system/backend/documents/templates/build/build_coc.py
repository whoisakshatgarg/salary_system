"""coc.docx  <-  "COC-PO-02940-EI-122.docx".

Provenance: the certificate of conformity, a native .docx with no tables -
just a tab-aligned label/value ladder.  Only the *value* text after the tabs
is tokenised, so every tab stop and the exact run of spaces the reference
uses survive; the customer name inside the certifying sentence and the
``{{customer_short}} P.O. NUMBER`` label prefix are tokenised in place.
"""

from __future__ import annotations

from docx import Document

from . import common, docxutil as dx


def build():
    src = common.sample("COC-PO-02940-EI-122.docx")
    doc = Document(str(src))
    body = doc.element.body
    paras = dx.paragraphs(body)

    def find(prefix):
        for p in paras:
            if dx.para_text(p).startswith(prefix):
                return p
        raise AssertionError(f"paragraph starting {prefix!r} not found")

    # certifying sentence - the customer's name in caps
    dx.tokenize_para(find("Apex Thermocon Pvt. Ltd hereby certifies"),
                     "customer_caps", old="SELCO PRODUCTS COMPANY")

    # "{{customer_short}} P.O. NUMBER ... : <po>"
    p = find("SELCO P.O. NUMBER")
    dx.tokenize_para(p, "customer_short", old="SELCO")
    dx.tokenize_para(p, "po", old="PO02940")

    p = find("Apex Thermocon INVOICE NUMBER")
    dx.tokenize_para(p, "invoice_no", old="AT/EI/25-26/122")
    dx.tokenize_para(p, "invoice_date", old="30th Jun’ 2025")   # 'Dtd. ' stays static

    dx.tokenize_para(find("PART NO. / DESCRIPTION"), "part_desc", old="TWS061000")
    dx.tokenize_para(find("MATERIAL"), "material", old="ASTM A276 304L")
    dx.tokenize_para(find("PLATING PROCESS"), "plating", old="NA")
    dx.tokenize_para(find("FINISHING PROCESS"), "finishing", old="NA")
    dx.tokenize_para(find("QUANTITY SHIPPED"), "qty_shipped", old="100")
    dx.tokenize_para(find("AUTHENTICATOR’S NAME"), "authenticator", old="Q.A. MANAGER")
    dx.tokenize_para(find("DATE SHIPPED"), "date_shipped", old="30th Jun’ 2025")

    dst = common.out("coc.docx")
    doc.save(str(dst))
    return dst


if __name__ == "__main__":
    print(build())
