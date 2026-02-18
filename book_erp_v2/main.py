from __future__ import annotations
import os
from datetime import date, datetime
from typing import Optional, Dict, Any

from fastapi import FastAPI, Request, Form, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import Session, select
from jinja2 import Environment, FileSystemLoader

from db import engine, init_db
from models import (
    User, Warehouse, Item, Party, PartyPrice, Stock,
    SalesOrder, SalesOrderLine, Challan, ChallanLine,
    Invoice, InvoiceLine, Payment,
    ReturnNote, ReturnLine
)
from auth import hash_pw, verify_pw, create_token, require_roles
from services import (
    get_stock, invoice_totals, apply_party_price,
    build_invoice_pdf, send_email_smtp, send_whatsapp_cloud
)

app = FastAPI(title="BookERP Pro v2")
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Environment(loader=FileSystemLoader("templates"))


def render(name: str, **ctx) -> HTMLResponse:
    tpl = templates.get_template(name)
    return HTMLResponse(tpl.render(**ctx))


@app.on_event("startup")
def _startup():
    init_db()
    # Create default admin on first run
    with Session(engine) as s:
        admin = s.exec(select(User).where(User.username == "admin")).first()
        if not admin:
            s.add(User(username="admin", password_hash=hash_pw("admin123"), role="ADMIN"))
            s.commit()

        # Create default warehouses if none
        if not s.exec(select(Warehouse)).first():
            s.add(Warehouse(name="Noida WH-1", city="Noida", state="Uttar Pradesh"))
            s.add(Warehouse(name="Noida WH-2", city="Noida", state="Uttar Pradesh"))
            s.add(Warehouse(name="Noida WH-3", city="Noida", state="Uttar Pradesh"))
            s.commit()


# ---------------- Login / Logout ----------------
@app.get("/login", response_class=HTMLResponse)
def login_page(msg: str = ""):
    return render("login.html", msg=msg)


@app.post("/login")
def login(username: str = Form(...), password: str = Form(...)):
    with Session(engine) as s:
        u = s.exec(select(User).where(User.username == username, User.is_active == True)).first()
        if not u or not verify_pw(password, u.password_hash):
            return render("login.html", msg="Invalid username or password")
        token = create_token(u.username, u.role)

    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie("token", token, httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("token")
    return resp


# ---------------- Dashboard ----------------
@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, u=Depends(require_roles("ADMIN", "WAREHOUSE", "BILLING", "ACCOUNTS"))):
    with Session(engine) as s:
        items = s.exec(select(Item)).all()
        parties = s.exec(select(Party)).all()
        sos = s.exec(select(SalesOrder)).all()
        invs = s.exec(select(Invoice)).all()
    return render(
        "dashboard.html",
        title="Dashboard",
        user=u,
        msg="",
        items=len(items),
        parties=len(parties),
        sos=len(sos),
        invs=len(invs),
    )


# ---------------- Items ----------------
@app.get("/items", response_class=HTMLResponse)
def items_page(request: Request, u=Depends(require_roles("ADMIN", "WAREHOUSE", "BILLING"))):
    with Session(engine) as s:
        rows = s.exec(select(Item).order_by(Item.id.desc())).all()
    return render("items.html", title="Items", user=u, rows=rows, msg="")


@app.post("/items/create")
def items_create(
    request: Request,
    sku: str = Form(...),
    title: str = Form(...),
    class_name: str = Form(...),
    subject: str = Form(...),
    board: str = Form(...),
    year: int = Form(...),
    edition: str = Form("1st"),
    isbn: str = Form(""),
    hsn: str = Form(""),
    gst_percent: float = Form(0.0),
    mrp: float = Form(0.0),
    sale_price: float = Form(0.0),
    barcode: str = Form(""),
    u=Depends(require_roles("ADMIN", "BILLING")),
):
    with Session(engine) as s:
        if s.exec(select(Item).where(Item.sku == sku)).first():
            raise HTTPException(400, "SKU already exists")
        s.add(
            Item(
                sku=sku,
                title=title,
                class_name=class_name,
                subject=subject,
                board=board,
                year=year,
                edition=edition,
                isbn=isbn or None,
                hsn=hsn or None,
                gst_percent=gst_percent,
                mrp=mrp,
                sale_price=sale_price,
                barcode=barcode or None,
            )
        )
        s.commit()
    return RedirectResponse("/items", status_code=303)


# ---------------- Parties + Price Slab + Credit ----------------
@app.get("/parties", response_class=HTMLResponse)
def parties_page(request: Request, u=Depends(require_roles("ADMIN", "BILLING", "ACCOUNTS"))):
    with Session(engine) as s:
        rows = s.exec(select(Party).order_by(Party.id.desc())).all()
        items = s.exec(select(Item)).all()
    return render("parties.html", title="Parties", user=u, rows=rows, items=items, msg="")


@app.post("/parties/create")
def parties_create(
    name: str = Form(...),
    type: str = Form("Distributor"),
    phone: str = Form(""),
    email: str = Form(""),
    gstin: str = Form(""),
    billing_address: str = Form(""),
    shipping_address: str = Form(""),
    state: str = Form(""),
    credit_limit: float = Form(0.0),
    payment_terms_days: int = Form(0),
    u=Depends(require_roles("ADMIN", "BILLING")),
):
    with Session(engine) as s:
        s.add(
            Party(
                name=name,
                type=type,
                phone=phone or None,
                email=email or None,
                gstin=gstin or None,
                billing_address=billing_address or None,
                shipping_address=shipping_address or None,
                state=state or None,
                credit_limit=credit_limit,
                payment_terms_days=payment_terms_days,
            )
        )
        s.commit()
    return RedirectResponse("/parties", status_code=303)


@app.post("/parties/price")
def set_party_price(
    party_id: int = Form(...),
    item_id: int = Form(...),
    discount_percent: float = Form(0.0),
    u=Depends(require_roles("ADMIN", "BILLING")),
):
    with Session(engine) as s:
        existing = s.exec(
            select(PartyPrice).where(PartyPrice.party_id == party_id, PartyPrice.item_id == item_id)
        ).first()
        if existing:
            existing.discount_percent = discount_percent
            s.add(existing)
        else:
            s.add(PartyPrice(party_id=party_id, item_id=item_id, discount_percent=discount_percent))
        s.commit()
    return RedirectResponse("/parties", status_code=303)


# ---------------- Stock adjust (Warehouse role) ----------------
@app.post("/stock/adjust")
def stock_adjust(
    warehouse_id: int = Form(...),
    item_id: int = Form(...),
    delta: int = Form(...),
    u=Depends(require_roles("ADMIN", "WAREHOUSE")),
):
    with Session(engine) as s:
        st = get_stock(s, warehouse_id, item_id)
        st.qty += int(delta)
        if st.qty < 0:
            raise HTTPException(400, "Stock cannot go negative")
        s.add(st)
        s.commit()
    return RedirectResponse("/sales-orders", status_code=303)


# ---------------- Helpers for numbering ----------------
def next_no(prefix: str, session: Session) -> str:
    p = f"{prefix}-{datetime.now().strftime('%Y%m')}-"

    if prefix == "SO":
        existing = session.exec(select(SalesOrder.so_no)).all()
    elif prefix == "DC":
        existing = session.exec(select(Challan.dc_no)).all()
    elif prefix == "RN":
        existing = session.exec(select(ReturnNote.rn_no)).all()
    else:  # INV
        existing = session.exec(select(Invoice.invoice_no)).all()

    nums = []
    for x in existing:
        if x and x.startswith(p):
            try:
                nums.append(int(x.replace(p, "")))
            except Exception:
                pass
    n = (max(nums) + 1) if nums else 1
    return p + str(n).zfill(4)


def calc_party_summary(session: Session, party: Party) -> Dict[str, float]:
    invs = session.exec(select(Invoice).where(Invoice.party_id == party.id)).all()
    total_out = 0.0
    overdue = 0.0
    today = date.today()

    for iv in invs:
        t = invoice_totals(session, iv.id)
        bal = float(t["balance"])
        if bal <= 0:
            continue
        total_out += bal

        terms = party.payment_terms_days or 0
        if terms > 0:
            due_ordinal = iv.invoice_date.toordinal() + terms
            if today.toordinal() > due_ordinal:
                overdue += bal

    return {"outstanding": round(total_out, 2), "overdue": round(overdue, 2)}


# ---------------- Sales Orders ----------------
@app.get("/sales-orders", response_class=HTMLResponse)
def so_page(request: Request, u=Depends(require_roles("ADMIN", "BILLING"))):
    with Session(engine) as s:
        sos = s.exec(select(SalesOrder).order_by(SalesOrder.id.desc())).all()
        parties = s.exec(select(Party)).all()
        whs = s.exec(select(Warehouse)).all()
        items = s.exec(select(Item)).all()
    return render(
        "sales_orders.html",
        title="Sales Orders",
        user=u,
        sos=sos,
        parties=parties,
        whs=whs,
        items=items,
        msg="",
    )


@app.post("/sales-orders/create")
def so_create(
    party_id: int = Form(...),
    warehouse_id: int = Form(...),
    so_date: str = Form(date.today().isoformat()),
    notes: str = Form(""),
    u=Depends(require_roles("ADMIN", "BILLING")),
):
    with Session(engine) as s:
        so = SalesOrder(
            so_no=next_no("SO", s),
            party_id=party_id,
            warehouse_id=warehouse_id,
            so_date=date.fromisoformat(so_date),
            notes=notes or None,
        )
        s.add(so)
        s.commit()
        s.refresh(so)
    return RedirectResponse(f"/sales-orders/{so.id}", status_code=303)


@app.get("/sales-orders/{so_id}", response_class=HTMLResponse)
def so_view(so_id: int, request: Request, u=Depends(require_roles("ADMIN", "BILLING", "WAREHOUSE"))):
    with Session(engine) as s:
        so = s.get(SalesOrder, so_id)
        if not so:
            raise HTTPException(404, "SO not found")
        party = s.get(Party, so.party_id)
        wh = s.get(Warehouse, so.warehouse_id)
        items = s.exec(select(Item)).all()
        lines = s.exec(select(SalesOrderLine).where(SalesOrderLine.so_id == so_id)).all()
    return render("sales_orders.html", title="Sales Orders", user=u, so=so, party=party, wh=wh, items=items, lines=lines, msg="")


@app.post("/sales-orders/{so_id}/add-line")
def so_add_line(so_id: int, item_id: int = Form(...), qty: int = Form(...), u=Depends(require_roles("ADMIN", "BILLING"))):
    with Session(engine) as s:
        so = s.get(SalesOrder, so_id)
        if not so:
            raise HTTPException(404, "SO not found")
        item = s.get(Item, item_id)
        rate, disc = apply_party_price(s, so.party_id, item)
        s.add(
            SalesOrderLine(
                so_id=so_id,
                item_id=item_id,
                qty=qty,
                rate=rate,
                gst_percent=item.gst_percent,
                discount_percent=disc,
            )
        )
        s.commit()
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


@app.post("/sales-orders/{so_id}/approve")
def so_approve(so_id: int, u=Depends(require_roles("ADMIN", "BILLING"))):
    with Session(engine) as s:
        so = s.get(SalesOrder, so_id)
        if not so:
            raise HTTPException(404, "SO not found")
        so.status = "APPROVED"
        s.add(so)
        s.commit()
    return RedirectResponse(f"/sales-orders/{so_id}", status_code=303)


# ---------------- Challan (dispatch) ----------------
@app.get("/challans", response_class=HTMLResponse)
def challans_page(request: Request, u=Depends(require_roles("ADMIN", "WAREHOUSE", "BILLING"))):
    with Session(engine) as s:
        dcs = s.exec(select(Challan).order_by(Challan.id.desc())).all()
        sos = s.exec(select(SalesOrder).where(SalesOrder.status == "APPROVED")).all()
    return render("challans.html", title="Challans", user=u, dcs=dcs, sos=sos, msg="")


@app.post("/challans/create")
def challan_create(so_id: int = Form(...), transporter: str = Form(""), lr_no: str = Form(""), u=Depends(require_roles("ADMIN", "WAREHOUSE"))):
    with Session(engine) as s:
        so = s.get(SalesOrder, so_id)
        if not so or so.status != "APPROVED":
            raise HTTPException(400, "SO not approved")

        dc = Challan(dc_no=next_no("DC", s), so_id=so_id, transporter=transporter or None, lr_no=lr_no or None)
        s.add(dc)
        s.commit()
        s.refresh(dc)

        # Copy lines from SO -> DC and reduce stock now (dispatch)
        so_lines = s.exec(select(SalesOrderLine).where(SalesOrderLine.so_id == so_id)).all()
        for ln in so_lines:
            st = get_stock(s, so.warehouse_id, ln.item_id)
            if st.qty < ln.qty:
                raise HTTPException(400, f"Insufficient stock for item_id={ln.item_id}. Available={st.qty}")
            st.qty -= ln.qty
            s.add(st)
            s.add(ChallanLine(dc_id=dc.id, item_id=ln.item_id, qty=ln.qty))

        so.status = "DISPATCHED"
        s.add(so)
        s.commit()

    return RedirectResponse("/challans", status_code=303)


# ---------------- Invoices ----------------
@app.get("/invoices", response_class=HTMLResponse)
def invoices_page(request: Request, u=Depends(require_roles("ADMIN", "BILLING", "ACCOUNTS"))):
    with Session(engine) as s:
        invs = s.exec(select(Invoice).order_by(Invoice.id.desc())).all()
        dcs = s.exec(select(Challan).where(Challan.status == "OPEN")).all()
    return render("invoices.html", title="Invoices", user=u, invs=invs, dcs=dcs, msg="")


@app.post("/invoices/create")
def invoice_create(dc_id: int = Form(...), invoice_date: str = Form(date.today().isoformat()), u=Depends(require_roles("ADMIN", "BILLING"))):
    with Session(engine) as s:
        dc = s.get(Challan, dc_id)
        if not dc or dc.status != "OPEN":
            raise HTTPException(400, "Challan not available")

        so = s.get(SalesOrder, dc.so_id)
        if not so:
            raise HTTPException(400, "SO not found for challan")

        party = s.get(Party, so.party_id)
        if not party:
            raise HTTPException(400, "Party not found")

        # ---- CREDIT LIMIT + OVERDUE BLOCK ----
        summary = calc_party_summary(s, party)
        if party.is_blocked:
            raise HTTPException(400, "Party is BLOCKED")
        if summary["overdue"] > 0:
            raise HTTPException(400, f"Overdue pending: {summary['overdue']} (clear overdue to continue)")

        # projected invoice amount (for credit-limit check)
        dc_lines = s.exec(select(ChallanLine).where(ChallanLine.dc_id == dc_id)).all()
        projected_sub = 0.0
        projected_gst = 0.0
        for dln in dc_lines:
            it = s.get(Item, dln.item_id)
            if not it:
                continue
            rate, disc = apply_party_price(s, party.id, it)
            amt = dln.qty * rate
            disc_amt = amt * (disc / 100.0)
            taxable = amt - disc_amt
            projected_sub += taxable
            projected_gst += taxable * (it.gst_percent / 100.0)
        projected_total = projected_sub + projected_gst

        if party.credit_limit and (summary["outstanding"] + projected_total) > party.credit_limit:
            raise HTTPException(
                400,
                f"Credit limit exceeded. Limit={party.credit_limit}, "
                f"Outstanding={summary['outstanding']}, NewInvoice={round(projected_total,2)}",
            )

        inv = Invoice(
            invoice_no=next_no("INV", s),
            dc_id=dc_id,
            party_id=party.id,
            warehouse_id=so.warehouse_id,
            invoice_date=date.fromisoformat(invoice_date),
            place_of_supply_state=party.state,
        )
        s.add(inv)
        s.commit()
        s.refresh(inv)

        # Copy DC lines -> invoice lines
        for dln in dc_lines:
            it = s.get(Item, dln.item_id)
            if not it:
                continue
            rate, disc = apply_party_price(s, party.id, it)
            s.add(
                InvoiceLine(
                    invoice_id=inv.id,
                    item_id=it.id,
                    qty=dln.qty,
                    rate=rate,
                    gst_percent=it.gst_percent,
                    discount_percent=disc,
                )
            )

        dc.status = "INVOICED"
        s.add(dc)
        s.commit()

    return RedirectResponse(f"/invoices/{inv.id}", status_code=303)


@app.get("/invoices/{inv_id}", response_class=HTMLResponse)
def invoice_view(inv_id: int, request: Request, u=Depends(require_roles("ADMIN", "BILLING", "ACCOUNTS"))):
    with Session(engine) as s:
        inv = s.get(Invoice, inv_id)
        if not inv:
            raise HTTPException(404, "Invoice not found")
        party = s.get(Party, inv.party_id)
        lines = s.exec(select(InvoiceLine).where(InvoiceLine.invoice_id == inv_id)).all()
        totals = invoice_totals(s, inv_id)
    return render("invoice_view.html", title=f"Invoice {inv.invoice_no}", user=u, inv=inv, party=party, lines=lines, totals=totals, msg="")


def _generate_invoice_pdf(session: Session, inv_id: int) -> str:
    inv = session.get(Invoice, inv_id)
    if not inv:
        raise HTTPException(404, "Invoice not found")
    party = session.get(Party, inv.party_id)
    wh = session.get(Warehouse, inv.warehouse_id)
    lines = session.exec(select(InvoiceLine).where(InvoiceLine.invoice_id == inv_id)).all()
    totals = invoice_totals(session, inv_id)

    pdf_dir = "generated"
    os.makedirs(pdf_dir, exist_ok=True)
    pdf_path = os.path.join(pdf_dir, f"{inv.invoice_no}.pdf")

    line_payload = []
    for ln in lines:
        it = session.get(Item, ln.item_id)
        amt = ln.qty * ln.rate
        disc_amt = amt * (ln.discount_percent / 100.0)
        taxable = amt - disc_amt
        gst = taxable * (ln.gst_percent / 100.0)
        line_payload.append(
            {
                "sku": it.sku if it else "",
                "title": it.title if it else "",
                "qty": ln.qty,
                "rate": ln.rate,
                "gst_percent": ln.gst_percent,
                "line_total": taxable + gst,
            }
        )

    header = {
        "invoice_no": inv.invoice_no,
        "invoice_date": str(inv.invoice_date),
        "party_name": party.name if party else "",
        "party_gstin": (party.gstin or "") if party else "",
        "place_of_supply": inv.place_of_supply_state or "",
        "warehouse": wh.name if wh else "",
    }
    build_invoice_pdf(pdf_path, header, line_payload, totals)
    return pdf_path


@app.get("/invoices/{inv_id}/pdf")
def invoice_pdf(inv_id: int, request: Request, u=Depends(require_roles("ADMIN", "BILLING", "ACCOUNTS"))):
    with Session(engine) as s:
        pdf_path = _generate_invoice_pdf(s, inv_id)
        inv = s.get(Invoice, inv_id)
    return FileResponse(pdf_path, media_type="application/pdf", filename=f"{inv.invoice_no}.pdf")


@app.post("/invoices/{inv_id}/send-email")
def invoice_send_email(inv_id: int, to_email: str = Form(...), u=Depends(require_roles("ADMIN", "ACCOUNTS", "BILLING"))):
    with Session(engine) as s:
        pdf_path = _generate_invoice_pdf(s, inv_id)
        inv = s.get(Invoice, inv_id)
        subject = f"Invoice {inv.invoice_no}"
    send_email_smtp(to_email, subject, "Please find attached invoice PDF.", pdf_path)
    return RedirectResponse(f"/invoices/{inv_id}", status_code=303)


@app.post("/invoices/{inv_id}/send-whatsapp")
def invoice_send_wa(inv_id: int, to_phone: str = Form(...), u=Depends(require_roles("ADMIN", "ACCOUNTS", "BILLING"))):
    # note: for full production we can send PDF link after hosting; currently text-only
    send_whatsapp_cloud(to_phone, f"Invoice ready. Invoice ID: {inv_id}. Please login to download PDF.")
    return RedirectResponse(f"/invoices/{inv_id}", status_code=303)


@app.post("/payments/add")
def add_payment(
    inv_id: int = Form(...),
    amount: float = Form(...),
    mode: str = Form("UPI"),
    ref: str = Form(""),
    u=Depends(require_roles("ADMIN", "ACCOUNTS")),
):
    with Session(engine) as s:
        inv = s.get(Invoice, inv_id)
        if not inv:
            raise HTTPException(404, "Invoice not found")
        s.add(Payment(party_id=inv.party_id, invoice_id=inv_id, amount=amount, mode=mode, ref=ref or None))
        s.commit()
    return RedirectResponse(f"/invoices/{inv_id}", status_code=303)


# ---------------- Statements (month/year wise) ----------------
@app.get("/statements", response_class=HTMLResponse)
def statements_home(request: Request, u=Depends(require_roles("ADMIN", "ACCOUNTS", "BILLING"))):
    with Session(engine) as s:
        parties = s.exec(select(Party)).all()
    # template supports view=False
    return render("statement.html", title="Statements", user=u, parties=parties, view=False, msg="")


@app.get("/statements/view", response_class=HTMLResponse)
def statements_view(
    request: Request,
    party_id: int,
    from_date: str = "",
    to_date: str = "",
    u=Depends(require_roles("ADMIN", "ACCOUNTS", "BILLING")),
):
    with Session(engine) as s:
        party = s.get(Party, party_id)
        if not party:
            raise HTTPException(404, "Party not found")

        invs = s.exec(select(Invoice).where(Invoice.party_id == party_id).order_by(Invoice.invoice_date.desc())).all()

        fd = date.fromisoformat(from_date) if from_date else None
        td = date.fromisoformat(to_date) if to_date else None

        rows = []
        outstanding = 0.0
        overdue = 0.0
        today = date.today()

        for iv in invs:
            if fd and iv.invoice_date < fd:
                continue
            if td and iv.invoice_date > td:
                continue

            t = invoice_totals(s, iv.id)
            bal = float(t["balance"])

            if bal > 0:
                outstanding += bal
                terms = party.payment_terms_days or 0
                if terms > 0 and today.toordinal() > (iv.invoice_date.toordinal() + terms):
                    overdue += bal

            rows.append(
                {
                    "id": iv.id,
                    "invoice_no": iv.invoice_no,
                    "invoice_date": str(iv.invoice_date),
                    "total": t["total"],
                    "paid": t["paid"],
                    "balance": t["balance"],
                    "month_key": iv.invoice_date.strftime("%b %Y"),
                }
            )

        buckets: Dict[str, list] = {}
        for r in rows:
            buckets.setdefault(r["month_key"], []).append(r)

    return render(
        "statement.html",
        title="Statements",
        user=u,
        parties=[],
        view=True,
        party=party,
        buckets=buckets,
        summary={"outstanding": round(outstanding, 2), "overdue": round(overdue, 2)},
        msg="",
    )


# ---------------- Returns (Party return -> stock add back) ----------------
@app.get("/returns", response_class=HTMLResponse)
def returns_home(request: Request, u=Depends(require_roles("ADMIN", "WAREHOUSE", "BILLING"))):
    with Session(engine) as s:
        parties = s.exec(select(Party)).all()
        whs = s.exec(select(Warehouse)).all()
        rns = s.exec(select(ReturnNote).order_by(ReturnNote.id.desc())).all()
    return render("returns.html", title="Returns", user=u, parties=parties, whs=whs, rns=rns, rn=None, msg="")


@app.post("/returns/create")
def returns_create(
    party_id: int = Form(...),
    warehouse_id: int = Form(...),
    return_date: str = Form(""),
    reason: str = Form("Unsold"),
    notes: str = Form(""),
    u=Depends(require_roles("ADMIN", "WAREHOUSE", "BILLING")),
):
    with Session(engine) as s:
        rn_no = next_no("RN", s)
        rn = ReturnNote(
            rn_no=rn_no,
            party_id=party_id,
            warehouse_id=warehouse_id,
            return_date=date.fromisoformat(return_date) if return_date else date.today(),
            reason=reason or "Unsold",
            notes=notes or None,
        )
        s.add(rn)
        s.commit()
        s.refresh(rn)
    return RedirectResponse(f"/returns/{rn.id}", status_code=303)


@app.get("/returns/{rn_id}", response_class=HTMLResponse)
def returns_view(rn_id: int, request: Request, u=Depends(require_roles("ADMIN", "WAREHOUSE", "BILLING"))):
    with Session(engine) as s:
        rn = s.get(ReturnNote, rn_id)
        if not rn:
            raise HTTPException(404, "Return note not found")
        parties = s.exec(select(Party)).all()
        whs = s.exec(select(Warehouse)).all()
        items = s.exec(select(Item)).all()
        lines = s.exec(select(ReturnLine).where(ReturnLine.return_id == rn_id)).all()
    return render(
        "returns.html",
        title="Returns",
        user=u,
        parties=parties,
        whs=whs,
        items=items,
        rn=rn,
        lines=lines,
        rns=[],
        msg="",
    )


@app.post("/returns/{rn_id}/add-line")
def returns_add_line(rn_id: int, item_id: int = Form(...), qty: int = Form(...), u=Depends(require_roles("ADMIN", "WAREHOUSE", "BILLING"))):
    with Session(engine) as s:
        rn = s.get(ReturnNote, rn_id)
        if not rn or rn.status != "OPEN":
            raise HTTPException(400, "Return note not open")
        s.add(ReturnLine(return_id=rn_id, item_id=item_id, qty=int(qty)))
        s.commit()
    return RedirectResponse(f"/returns/{rn_id}", status_code=303)


@app.post("/returns/{rn_id}/post")
def returns_post(rn_id: int, u=Depends(require_roles("ADMIN", "WAREHOUSE"))):
    with Session(engine) as s:
        rn = s.get(ReturnNote, rn_id)
        if not rn or rn.status != "OPEN":
            raise HTTPException(400, "Return note not open")

        lines = s.exec(select(ReturnLine).where(ReturnLine.return_id == rn_id)).all()
        for ln in lines:
            st = get_stock(s, rn.warehouse_id, ln.item_id)
            st.qty += int(ln.qty)
            s.add(st)

        rn.status = "POSTED"
        s.add(rn)
        s.commit()

    return RedirectResponse("/returns", status_code=303)


# ---------------- Barcode Scan ----------------
@app.get("/barcode", response_class=HTMLResponse)
def barcode_page(request: Request, u=Depends(require_roles("ADMIN", "WAREHOUSE", "BILLING"))):
    return render("barcode_scan.html", title="Scan", user=u, msg="")