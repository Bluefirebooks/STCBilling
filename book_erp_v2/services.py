from __future__ import annotations
import os
import smtplib
from email.message import EmailMessage
from typing import Dict, Tuple, Optional

import requests
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from sqlmodel import Session, select

from models import Stock, InvoiceLine, Payment, Party, PartyPrice, Item


def get_stock(session: Session, warehouse_id: int, item_id: int) -> Stock:
    st = session.exec(select(Stock).where(Stock.warehouse_id == warehouse_id, Stock.item_id == item_id)).first()
    if not st:
        st = Stock(warehouse_id=warehouse_id, item_id=item_id, qty=0)
        session.add(st)
        session.commit()
        session.refresh(st)
    return st


def apply_party_price(session: Session, party_id: int, item: Item) -> Tuple[float, float]:
    pp = session.exec(select(PartyPrice).where(PartyPrice.party_id == party_id, PartyPrice.item_id == item.id)).first()
    if not pp:
        return item.sale_price, 0.0
    disc = max(0.0, min(100.0, float(pp.discount_percent)))
    rate = round(item.sale_price * (1 - disc / 100.0), 2)
    return rate, disc


def invoice_totals(session: Session, invoice_id: int) -> Dict[str, float]:
    lines = session.exec(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id)).all()
    subtotal = 0.0
    gst = 0.0
    for ln in lines:
        amt = ln.qty * ln.rate
        disc_amt = amt * (ln.discount_percent / 100.0)
        taxable = amt - disc_amt
        subtotal += taxable
        gst += taxable * (ln.gst_percent / 100.0)
    total = subtotal + gst
    pays = session.exec(select(Payment).where(Payment.invoice_id == invoice_id)).all()
    paid = sum(p.amount for p in pays)
    balance = total - paid
    return {
        "subtotal": round(subtotal, 2),
        "gst": round(gst, 2),
        "total": round(total, 2),
        "paid": round(paid, 2),
        "balance": round(balance, 2),
    }


def build_invoice_pdf(pdf_path: str, header: dict, lines: list, totals: dict):
    c = canvas.Canvas(pdf_path, pagesize=A4)
    w, h = A4
    y = h - 40

    c.setFont("Helvetica-Bold", 16)
    c.drawString(40, y, "TAX INVOICE")
    y -= 22

    c.setFont("Helvetica", 10)
    for k in ["invoice_no", "invoice_date", "party_name", "party_gstin", "place_of_supply", "warehouse"]:
        c.drawString(40, y, f"{k.replace('_', ' ').title()}: {header.get(k, '')}")
        y -= 14

    y -= 8
    c.setFont("Helvetica-Bold", 10)
    c.drawString(40, y, "SKU")
    c.drawString(170, y, "Title")
    c.drawString(360, y, "Qty")
    c.drawString(400, y, "Rate")
    c.drawString(450, y, "GST%")
    c.drawString(500, y, "Total")
    y -= 12
    c.line(40, y, 550, y)
    y -= 14

    c.setFont("Helvetica", 9)
    for ln in lines:
        if y < 80:
            c.showPage()
            y = h - 40
        c.drawString(40, y, ln["sku"][:18])
        c.drawString(170, y, ln["title"][:30])
        c.drawRightString(380, y, str(ln["qty"]))
        c.drawRightString(435, y, f"{ln['rate']:.2f}")
        c.drawRightString(485, y, f"{ln['gst_percent']:.2f}")
        c.drawRightString(550, y, f"{ln['line_total']:.2f}")
        y -= 14

    y -= 10
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(550, y, f"Subtotal: {totals['subtotal']:.2f}")
    y -= 14
    c.drawRightString(550, y, f"GST: {totals['gst']:.2f}")
    y -= 14
    c.drawRightString(550, y, f"Total: {totals['total']:.2f}")
    y -= 14
    c.drawRightString(550, y, f"Paid: {totals['paid']:.2f}")
    y -= 14
    c.drawRightString(550, y, f"Balance: {totals['balance']:.2f}")

    c.save()


def send_email_smtp(to_email: str, subject: str, body: str, attachment_path: Optional[str] = None):
    host = os.getenv("SMTP_HOST")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER")
    pwd = os.getenv("SMTP_PASS")
    if not (host and user and pwd):
        raise RuntimeError("SMTP not configured")

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)

    if attachment_path:
        with open(attachment_path, "rb") as f:
            data = f.read()
        msg.add_attachment(data, maintype="application", subtype="pdf", filename=os.path.basename(attachment_path))

    with smtplib.SMTP(host, port) as s:
        s.starttls()
        s.login(user, pwd)
        s.send_message(msg)


def send_whatsapp_cloud(to_phone: str, text: str):
    token = os.getenv("WA_TOKEN")
    phone_id = os.getenv("WA_PHONE_ID")
    if not (token and phone_id):
        raise RuntimeError("WhatsApp API not configured")

    url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": to_phone, "type": "text", "text": {"body": text}}
    r = requests.post(url, headers=headers, json=payload, timeout=20)
    if r.status_code >= 300:
        raise RuntimeError(f"WhatsApp error: {r.status_code} {r.text}")