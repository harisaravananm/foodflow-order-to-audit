"""GST tax invoice / credit note PDF (reportlab)."""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from core import paise_to_str, rupees_in_words
from services import setting

GREEN = colors.HexColor("#1F4D3A")


def _m(p):
    return f"{int(p) // 100:,}.{int(p) % 100:02d}" if p >= 0 else "-" + _m(-p)


def invoice_pdf(db, inv_id):
    inv = db.execute("SELECT * FROM invoices WHERE id=?", (inv_id,)).fetchone()
    lines = db.execute("SELECT * FROM invoice_lines WHERE invoice_id=? ORDER BY id", (inv_id,)).fetchall()
    cn = db.execute("SELECT * FROM credit_notes WHERE invoice_id=?", (inv_id,)).fetchone()
    S = lambda k, d="": setting(db, k, d)
    ss = getSampleStyleSheet()
    n = ParagraphStyle("n", parent=ss["Normal"], fontSize=8.5, leading=11)
    b = ParagraphStyle("b", parent=n, fontName="Helvetica-Bold")
    big = ParagraphStyle("big", parent=n, fontName="Helvetica-Bold", fontSize=15, leading=18, textColor=GREEN)
    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=12 * mm, bottomMargin=12 * mm,
                            title=inv["invoice_no"])
    intra = inv["supply_type"] == "intra"
    story = []
    head = Table([[Paragraph(S("company_name"), big), Paragraph("<b>TAX INVOICE</b>" + ("<br/><font color='#C8412B'>CANCELLED</font>" if inv["status"] == "cancelled" else ""),
                                                                 ParagraphStyle("r", parent=n, alignment=2, fontSize=12, leading=15))],
                  [Paragraph(f"{S('company_address')}<br/>GSTIN: <b>{S('company_gstin')}</b> &nbsp; State: {S('company_state')} ({S('company_state_code')})<br/>"
                             f"Phone: {S('company_phone')}", n),
                   Paragraph(f"Invoice no: <b>{inv['invoice_no']}</b><br/>Date: <b>{inv['date']}</b><br/>Financial year: {inv['fy']}", ParagraphStyle("r2", parent=n, alignment=2))]],
                 colWidths=[110 * mm, 72 * mm])
    head.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 1), (-1, 1), 1, GREEN), ("BOTTOMPADDING", (0, 1), (-1, 1), 6)]))
    story += [head, Spacer(1, 4 * mm)]
    bill = Table([[Paragraph("<b>Bill to / Ship to</b>", n), Paragraph("<b>Supply details</b>", n)],
                  [Paragraph(f"<b>{inv['cust_name']}</b><br/>{inv['cust_address'] or ''}<br/>GSTIN: {inv['cust_gstin'] or 'Unregistered'}", n),
                   Paragraph(f"Place of supply: <b>{inv['place_of_supply']} ({inv['place_code']})</b><br/>"
                             f"Supply type: {'Intra-state (CGST + SGST)' if intra else 'Inter-state (IGST)'}<br/>Reverse charge: No", n)]],
                 colWidths=[91 * mm, 91 * mm])
    bill.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.grey), ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
                              ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EEF3F0"))]))
    story += [bill, Spacer(1, 4 * mm)]
    hdr = ["#", "Description", "HSN", "Qty (pkt)", "Rate", "Taxable"] + (["CGST", "SGST"] if intra else ["IGST"]) + ["Amount"]
    rows = [hdr]
    for i, l in enumerate(lines, 1):
        tax = l["cgst_paise"] + l["sgst_paise"] + l["igst_paise"]
        r = [i, l["description"], l["hsn"], l["qty_pkts"], _m(l["rate_paise"]), _m(l["taxable_paise"])]
        pct = f"{l['gst_rate_x100'] / 200:g}%" if intra else f"{l['gst_rate_x100'] / 100:g}%"
        r += ([f"{pct}\n{_m(l['cgst_paise'])}", f"{pct}\n{_m(l['sgst_paise'])}"] if intra else [f"{pct}\n{_m(l['igst_paise'])}"])
        r.append(_m(l["taxable_paise"] + tax))
        rows.append(r)
    cw = [8, 52, 18, 16, 16, 22] + ([22, 22] if intra else [26]) + [24]
    t = Table(rows, colWidths=[w * mm * (182 / sum(cw)) for w in cw], repeatRows=1)
    t.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GREEN), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTSIZE", (0, 0), (-1, -1), 8),
                           ("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("ALIGN", (3, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    story += [t, Spacer(1, 3 * mm)]
    tot = [["Taxable value", _m(inv["taxable_paise"])]]
    if intra:
        tot += [["CGST", _m(inv["cgst_paise"])], ["SGST", _m(inv["sgst_paise"])]]
    else:
        tot += [["IGST", _m(inv["igst_paise"])]]
    tot += [["Invoice total (Rs.)", _m(inv["total_paise"])]]
    tt = Table(tot, colWidths=[40 * mm, 30 * mm], hAlign="RIGHT")
    tt.setStyle(TableStyle([("ALIGN", (1, 0), (1, -1), "RIGHT"), ("FONTSIZE", (0, 0), (-1, -1), 9), ("LINEABOVE", (0, -1), (-1, -1), 0.75, GREEN),
                            ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold")]))
    story += [tt, Spacer(1, 3 * mm), Paragraph(f"<b>Amount in words:</b> {rupees_in_words(inv['total_paise'])}", n), Spacer(1, 2 * mm)]
    if cn:
        story.append(Paragraph(f"<font color='#C8412B'><b>Cancelled by credit note {cn['cn_no']} dated {cn['date']}</b> &mdash; {cn['reason']}</font>", n))
    story += [Spacer(1, 8 * mm), Paragraph("Declaration: we declare that this invoice shows the actual price of the goods described and that all particulars are true and correct.", n),
              Spacer(1, 10 * mm), Paragraph(f"For <b>{S('company_name')}</b><br/><br/><br/>Authorised signatory", ParagraphStyle("s", parent=n, alignment=2)),
              Paragraph("<i>Computer-generated invoice.</i>", n)]
    doc.build(story)
    return out.getvalue()
