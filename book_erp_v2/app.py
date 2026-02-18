from __future__ import annotations

from datetime import date, datetime
from typing import Optional, List

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from sqlmodel import SQLModel, Field, Session, create_engine, select, col
from jinja2 import Template


# -----------------------------
# Database
# -----------------------------
DB_URL = "sqlite:///./book_erp.db"   # Change to postgres later if needed
engine = create_engine(DB_URL, echo=False)


# -----------------------------
# Models
# -----------------------------
class Warehouse(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    city: str = "Noida"
    state: str = "Uttar Pradesh"


class Item(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sku: str = Field(index=True, unique=True)  # e.g. 10-MATH-CBSE-2026-ED1-ISBN
    title: str
    class_name: str
    subject: str
    board: str
    year: int
    edition: str = "1st"
    isbn: Optional[str] = None
    hsn: Optional[str] = None
    gst_percent: float = 0.0
    mrp: float = 0.0
    sale_price: float = 0.0


class Party(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    type: str = "Distributor"  # Distributor/School/Retailer
    phone: Optional[str] = None
    email: Optional[str] = None
    gstin: Optional[str] = None
    billing_address: Optional[str] = None
    credit_limit: float = 0.0


class Stock(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    warehouse_id: int = Field(foreign_key="warehouse.id", index=True)
    item_id: int = Field(foreign_key="item.id", index=True)
    qty: int = 0

    __table_args__ = (
        # unique per warehouse+item (handled via app logic)
        {},
    )


class Invoice(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    invoice_no: str = Field(index=True, unique=True)
    party_id: int = Field(foreign_key="party.id", index=True)
    warehouse_id: int = Field(foreign_key="warehouse.id", index=True)
    invoice_date: date = Field(default_factory=date.today)
    status: str = "OPEN"  # OPEN/PAID/PARTIAL/CANCELLED
    notes: Optional[str] = None


class InvoiceLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    invoice_id: int = Field(foreign_key="invoice.id", index=True)
    item_id: int = Field(foreign_key="item.id", index=True)
    qty: int
    rate: float
    gst_percent: float = 0.0


class Payment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    party_id: int = Field(foreign_key="party.id", index=True)
    invoice_id: Optional[int] = Field(default=None, foreign_key="invoice.id", index=True)
    pay_date: date = Field(default_factory=date.today)
    amount: float = 0.0
    mode: str = "UPI"  # UPI/NEFT/CASH/CHEQUE
    ref: Optional[str] = None


class ReturnNote(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    party_id: int = Field(foreign_key="party.id", index=True)
    warehouse_id: int = Field(foreign_key="warehouse.id", index=True)
    return_date: date = Field(default_factory=date.today)
    reason: str = "Unsold"
    notes: Optional[str] = None


class ReturnLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    return_id: int = Field(foreign_key="returnnote.id", index=True)
    item_id: int = Field(foreign_key="item.id", index=True)
    qty: int


# -----------------------------
# Helpers
# -----------------------------
def init_db():
    SQLModel.metadata.create_all(engine)


def get_stock(session: Session, warehouse_id: int, item_id: int) -> Stock:
    st = session.exec(
        select(Stock).where(Stock.warehouse_id == warehouse_id, Stock.item_id == item_id)
    ).first()
    if not st:
        st = Stock(warehouse_id=warehouse_id, item_id=item_id, qty=0)
        session.add(st)
        session.commit()
        session.refresh(st)
    return st


def invoice_totals(session: Session, invoice_id: int) -> dict:
    lines = session.exec(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id)).all()
    subtotal = 0.0
    gst = 0.0
    for ln in lines:
        line_amount = ln.qty * ln.rate
        subtotal += line_amount
        gst += line_amount * (ln.gst_percent / 100.0)
    total = subtotal + gst
    paid = session.exec(
        select(Payment).where(Payment.invoice_id == invoice_id)
    ).all()
    paid_amt = sum(p.amount for p in paid)
    balance = total - paid_amt
    return {"subtotal": round(subtotal, 2), "gst": round(gst, 2), "total": round(total, 2),
            "paid": round(paid_amt, 2), "balance": round(balance, 2)}


def update_invoice_status(session: Session, invoice_id: int):
    inv = session.get(Invoice, invoice_id)
    if not inv:
        return
    t = invoice_totals(session, invoice_id)
    if t["total"] <= 0:
        inv.status = "OPEN"
    elif t["balance"] <= 0.001:
        inv.status = "PAID"
    elif t["paid"] > 0:
        inv.status = "PARTIAL"
    else:
        inv.status = "OPEN"
    session.add(inv)
    session.commit()


def next_invoice_no(session: Session) -> str:
    # Simple: INV-YYYYMM-0001
    prefix = f"INV-{datetime.now().strftime('%Y%m')}-"
    existing = session.exec(select(Invoice.invoice_no).where(Invoice.invoice_no.startswith(prefix))).all()
    if not existing:
        return prefix + "0001"
    nums = []
    for x in existing:
        try:
            nums.append(int(x.replace(prefix, "")))
        except:
            pass
    n = (max(nums) + 1) if nums else 1
    return prefix + str(n).zfill(4)


# -----------------------------
# App + Minimal UI (HTML)
# -----------------------------
app = FastAPI(title="Book ERP - Inventory + Billing + Statements")


BASE = Template("""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{title}}</title>
  <style>
    body{font-family:system-ui,Segoe UI,Arial;margin:20px;max-width:1100px}
    a{color:#0a58ca;text-decoration:none}
    .top{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:16px}
    .card{border:1px solid #ddd;border-radius:12px;padding:14px;margin:12px 0}
    table{border-collapse:collapse;width:100%}
    th,td{border-bottom:1px solid #eee;padding:8px;text-align:left;font-size:14px}
    input,select,textarea{padding:8px;border:1px solid #ccc;border-radius:10px;width:100%}
    .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
    .btn{display:inline-block;padding:10px 14px;border-radius:10px;border:1px solid #0a58ca;background:#0a58ca;color:white}
    .btn2{display:inline-block;padding:10px 14px;border-radius:10px;border:1px solid #666;background:white;color:#111}
    .row{display:flex;gap:10px;flex-wrap:wrap}
    .pill{padding:4px 10px;border-radius:999px;border:1px solid #ddd;font-size:12px}
  </style>
</head>
<body>
  <div class="top">
    <a class="btn2" href="/">Dashboard</a>
    <a class="btn2" href="/warehouses">Warehouses</a>
    <a class="btn2" href="/items">Items</a>
    <a class="btn2" href="/parties">Parties</a>
    <a class="btn2" href="/stock">Stock</a>
    <a class="btn2" href="/invoices">Invoices</a>
    <a class="btn2" href="/payments">Payments</a>
    <a class="btn2" href="/returns">Returns</a>
    <a class="btn2" href="/statements">Party Statement</a>
    <a class="btn2" href="/api/docs">API Docs</a>
  </div>
  <h2>{{title}}</h2>
  {{body}}
</body>
</html>
""")


def page(title: str, body_html: str) -> HTMLResponse:
    return HTMLResponse(BASE.render(title=title, body=body_html))


@app.on_event("startup")
def _startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def dashboard():
    with Session(engine) as s:
        w = s.exec(select(Warehouse)).all()
        items = s.exec(select(Item)).all()
        parties = s.exec(select(Party)).all()
        invs = s.exec(select(Invoice)).all()
    body = f"""
    <div class="card">
      <div class="row">
        <div class="pill">Warehouses: <b>{len(w)}</b></div>
        <div class="pill">Items: <b>{len(items)}</b></div>
        <div class="pill">Parties: <b>{len(parties)}</b></div>
        <div class="pill">Invoices: <b>{len(invs)}</b></div>
      </div>
      <p style="margin-top:10px;color:#444">MVP ready. Start by adding Warehouses → Items → Parties → Opening Stock → Invoices → Payments → Statements.</p>
    </div>
    """
    return page("Dashboard", body)


# -----------------------------
# Warehouses
# -----------------------------
@app.get("/warehouses", response_class=HTMLResponse)
def warehouses():
    with Session(engine) as s:
        rows = s.exec(select(Warehouse)).all()
    trows = "".join([f"<tr><td>{r.id}</td><td>{r.name}</td><td>{r.city}</td><td>{r.state}</td></tr>" for r in rows])
    body = f"""
    <div class="card">
      <form method="post" action="/warehouses/create">
        <div class="grid">
          <div><label>Name</label><input name="name" required></div>
          <div><label>City</label><input name="city" value="Noida"></div>
          <div><label>State</label><input name="state" value="Uttar Pradesh"></div>
        </div>
        <p><button class="btn" type="submit">Add Warehouse</button></p>
      </form>
    </div>
    <div class="card">
      <table><thead><tr><th>ID</th><th>Name</th><th>City</th><th>State</th></tr></thead><tbody>
      {trows or "<tr><td colspan='4'>No warehouses</td></tr>"}
      </tbody></table>
    </div>
    """
    return page("Warehouses", body)


@app.post("/warehouses/create")
def warehouses_create(name: str = Form(...), city: str = Form("Noida"), state: str = Form("Uttar Pradesh")):
    with Session(engine) as s:
        s.add(Warehouse(name=name, city=city, state=state))
        s.commit()
    return RedirectResponse("/warehouses", status_code=303)


# -----------------------------
# Items
# -----------------------------
@app.get("/items", response_class=HTMLResponse)
def items():
    with Session(engine) as s:
        rows = s.exec(select(Item).order_by(Item.id.desc())).all()
    trows = "".join([
        f"<tr><td>{r.id}</td><td><b>{r.sku}</b><br>{r.title}</td><td>{r.class_name}</td><td>{r.subject}</td><td>{r.board}</td><td>{r.year}</td><td>{r.edition}</td><td>{r.sale_price}</td></tr>"
        for r in rows
    ])
    body = f"""
    <div class="card">
      <form method="post" action="/items/create">
        <div class="grid">
          <div><label>SKU (unique)</label><input name="sku" placeholder="10-MATH-CBSE-2026-ED1-978..." required></div>
          <div><label>Title</label><input name="title" required></div>
          <div><label>Class</label><input name="class_name" placeholder="10" required></div>
          <div><label>Subject</label><input name="subject" placeholder="Maths" required></div>
          <div><label>Board</label><input name="board" placeholder="CBSE" required></div>
          <div><label>Year</label><input name="year" type="number" value="{date.today().year}" required></div>
          <div><label>Edition</label><input name="edition" value="1st"></div>
          <div><label>ISBN</label><input name="isbn"></div>
          <div><label>HSN</label><input name="hsn"></div>
          <div><label>GST %</label><input name="gst_percent" type="number" step="0.01" value="0"></div>
          <div><label>MRP</label><input name="mrp" type="number" step="0.01" value="0"></div>
          <div><label>Sale Price</label><input name="sale_price" type="number" step="0.01" value="0"></div>
        </div>
        <p><button class="btn" type="submit">Add Item</button></p>
      </form>
    </div>
    <div class="card">
      <table><thead><tr><th>ID</th><th>SKU/Title</th><th>Class</th><th>Subject</th><th>Board</th><th>Year</th><th>Edition</th><th>Sale Price</th></tr></thead><tbody>
      {trows or "<tr><td colspan='8'>No items</td></tr>"}
      </tbody></table>
    </div>
    """
    return page("Items", body)


@app.post("/items/create")
def items_create(
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
):
    with Session(engine) as s:
        exists = s.exec(select(Item).where(Item.sku == sku)).first()
        if exists:
            raise HTTPException(400, "SKU already exists")
        s.add(Item(
            sku=sku, title=title, class_name=class_name, subject=subject,
            board=board, year=year, edition=edition, isbn=isbn or None,
            hsn=hsn or None, gst_percent=gst_percent, mrp=mrp, sale_price=sale_price
        ))
        s.commit()
    return RedirectResponse("/items", status_code=303)


# -----------------------------
# Parties
# -----------------------------
@app.get("/parties", response_class=HTMLResponse)
def parties():
    with Session(engine) as s:
        rows = s.exec(select(Party).order_by(Party.id.desc())).all()
    trows = "".join([
        f"<tr><td>{r.id}</td><td><b>{r.name}</b><br>{r.type}</td><td>{r.phone or ''}</td><td>{r.gstin or ''}</td><td>{r.credit_limit}</td></tr>"
        for r in rows
    ])
    body = f"""
    <div class="card">
      <form method="post" action="/parties/create">
        <div class="grid">
          <div><label>Name</label><input name="name" required></div>
          <div><label>Type</label>
            <select name="type">
              <option>Distributor</option>
              <option>School</option>
              <option>Retailer</option>
            </select>
          </div>
          <div><label>Phone</label><input name="phone"></div>
          <div><label>Email</label><input name="email"></div>
          <div><label>GSTIN</label><input name="gstin"></div>
          <div><label>Credit Limit</label><input name="credit_limit" type="number" step="0.01" value="0"></div>
          <div style="grid-column:1 / -1"><label>Billing Address</label><textarea name="billing_address" rows="2"></textarea></div>
        </div>
        <p><button class="btn" type="submit">Add Party</button></p>
      </form>
    </div>
    <div class="card">
      <table><thead><tr><th>ID</th><th>Party</th><th>Phone</th><th>GSTIN</th><th>Credit Limit</th></tr></thead><tbody>
      {trows or "<tr><td colspan='5'>No parties</td></tr>"}
      </tbody></table>
    </div>
    """
    return page("Parties", body)


@app.post("/parties/create")
def parties_create(
    name: str = Form(...),
    type: str = Form("Distributor"),
    phone: str = Form(""),
    email: str = Form(""),
    gstin: str = Form(""),
    billing_address: str = Form(""),
    credit_limit: float = Form(0.0),
):
    with Session(engine) as s:
        s.add(Party(
            name=name, type=type, phone=phone or None, email=email or None,
            gstin=gstin or None, billing_address=billing_address or None,
            credit_limit=credit_limit
        ))
        s.commit()
    return RedirectResponse("/parties", status_code=303)


# -----------------------------
# Stock (Opening + Adjust)
# -----------------------------
@app.get("/stock", response_class=HTMLResponse)
def stock():
    with Session(engine) as s:
        whs = s.exec(select(Warehouse)).all()
        items = s.exec(select(Item)).all()
        stocks = s.exec(select(Stock)).all()

    wh_opts = "".join([f"<option value='{w.id}'>{w.name}</option>" for w in whs])
    item_opts = "".join([f"<option value='{i.id}'>{i.sku} - {i.title}</option>" for i in items])

    # show table
    rows = []
    with Session(engine) as s:
        for st in stocks:
            w = s.get(Warehouse, st.warehouse_id)
            it = s.get(Item, st.item_id)
            rows.append((w.name if w else st.warehouse_id, it.sku if it else st.item_id, st.qty))
    trows = "".join([f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td></tr>" for r in rows])

    body = f"""
    <div class="card">
      <form method="post" action="/stock/adjust">
        <div class="grid">
          <div><label>Warehouse</label><select name="warehouse_id" required>{wh_opts}</select></div>
          <div><label>Item</label><select name="item_id" required>{item_opts}</select></div>
          <div><label>Qty Change (+ add / - reduce)</label><input name="delta" type="number" required></div>
        </div>
        <p><button class="btn" type="submit">Adjust Stock</button></p>
        <p style="color:#555;font-size:13px">Opening stock add karne ke liye positive qty dijiye.</p>
      </form>
    </div>
    <div class="card">
      <table><thead><tr><th>Warehouse</th><th>Item SKU</th><th>Qty</th></tr></thead><tbody>
      {trows or "<tr><td colspan='3'>No stock records</td></tr>"}
      </tbody></table>
    </div>
    """
    return page("Stock", body)


@app.post("/stock/adjust")
def stock_adjust(warehouse_id: int = Form(...), item_id: int = Form(...), delta: int = Form(...)):
    with Session(engine) as s:
        st = get_stock(s, warehouse_id, item_id)
        st.qty += int(delta)
        if st.qty < 0:
            raise HTTPException(400, "Stock cannot go negative")
        s.add(st)
        s.commit()
    return RedirectResponse("/stock", status_code=303)


# -----------------------------
# Invoices
# -----------------------------
@app.get("/invoices", response_class=HTMLResponse)
def invoices():
    with Session(engine) as s:
        invs = s.exec(select(Invoice).order_by(Invoice.id.desc())).all()
        parties = s.exec(select(Party)).all()
        whs = s.exec(select(Warehouse)).all()

    party_opts = "".join([f"<option value='{p.id}'>{p.name}</option>" for p in parties])
    wh_opts = "".join([f"<option value='{w.id}'>{w.name}</option>" for w in whs])

    trows = ""
    with Session(engine) as s:
        for inv in invs:
            p = s.get(Party, inv.party_id)
            t = invoice_totals(s, inv.id)
            trows += f"""
            <tr>
              <td><a href="/invoices/{inv.id}"><b>{inv.invoice_no}</b></a><br>{inv.invoice_date}</td>
              <td>{p.name if p else inv.party_id}</td>
              <td>{inv.status}</td>
              <td>{t["total"]}</td>
              <td>{t["paid"]}</td>
              <td>{t["balance"]}</td>
            </tr>
            """

    body = f"""
    <div class="card">
      <form method="post" action="/invoices/create">
        <div class="grid">
          <div><label>Party</label><select name="party_id" required>{party_opts}</select></div>
          <div><label>Warehouse</label><select name="warehouse_id" required>{wh_opts}</select></div>
          <div><label>Invoice Date</label><input name="invoice_date" type="date" value="{date.today().isoformat()}"></div>
          <div><label>Notes</label><input name="notes"></div>
        </div>
        <p><button class="btn" type="submit">Create Invoice</button></p>
      </form>
    </div>
    <div class="card">
      <table><thead><tr><th>Invoice</th><th>Party</th><th>Status</th><th>Total</th><th>Paid</th><th>Balance</th></tr></thead><tbody>
      {trows or "<tr><td colspan='6'>No invoices</td></tr>"}
      </tbody></table>
    </div>
    """
    return page("Invoices", body)


@app.post("/invoices/create")
def invoices_create(
    party_id: int = Form(...),
    warehouse_id: int = Form(...),
    invoice_date: str = Form(date.today().isoformat()),
    notes: str = Form(""),
):
    with Session(engine) as s:
        inv_no = next_invoice_no(s)
        inv = Invoice(
            invoice_no=inv_no,
            party_id=party_id,
            warehouse_id=warehouse_id,
            invoice_date=date.fromisoformat(invoice_date),
            notes=notes or None
        )
        s.add(inv)
        s.commit()
        s.refresh(inv)
    return RedirectResponse(f"/invoices/{inv.id}", status_code=303)


@app.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def invoice_view(invoice_id: int):
    with Session(engine) as s:
        inv = s.get(Invoice, invoice_id)
        if not inv:
            raise HTTPException(404, "Invoice not found")
        p = s.get(Party, inv.party_id)
        w = s.get(Warehouse, inv.warehouse_id)
        items = s.exec(select(Item)).all()
        lines = s.exec(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id)).all()
        totals = invoice_totals(s, invoice_id)

    item_opts = "".join([f"<option value='{i.id}'>{i.sku} - {i.title}</option>" for i in items])

    line_rows = ""
    with Session(engine) as s:
        for ln in lines:
            it = s.get(Item, ln.item_id)
            amt = ln.qty * ln.rate
            gst = amt * (ln.gst_percent/100.0)
            line_rows += f"<tr><td>{it.sku if it else ln.item_id}</td><td>{ln.qty}</td><td>{ln.rate}</td><td>{ln.gst_percent}</td><td>{round(amt+gst,2)}</td></tr>"

    body = f"""
    <div class="card">
      <div class="row">
        <div class="pill">Invoice: <b>{inv.invoice_no}</b></div>
        <div class="pill">Date: <b>{inv.invoice_date}</b></div>
        <div class="pill">Party: <b>{p.name if p else inv.party_id}</b></div>
        <div class="pill">Warehouse: <b>{w.name if w else inv.warehouse_id}</b></div>
        <div class="pill">Status: <b>{inv.status}</b></div>
      </div>
      <p style="color:#444;margin-top:10px">Totals: <b>Total {totals["total"]}</b> | Paid {totals["paid"]} | Balance {totals["balance"]}</p>
      <div class="row">
        <a class="btn2" href="/invoices/{invoice_id}/print" target="_blank">Print/Share Invoice (PDF-like)</a>
      </div>
    </div>

    <div class="card">
      <h3>Add Line (Auto stock reduce from warehouse)</h3>
      <form method="post" action="/invoices/{invoice_id}/add_line">
        <div class="grid">
          <div><label>Item</label><select name="item_id" required>{item_opts}</select></div>
          <div><label>Qty</label><input name="qty" type="number" required></div>
          <div><label>Rate</label><input name="rate" type="number" step="0.01" required></div>
          <div><label>GST %</label><input name="gst_percent" type="number" step="0.01" value="0"></div>
        </div>
        <p><button class="btn" type="submit">Add Line</button></p>
      </form>
      <p style="color:#666;font-size:13px">Note: Stock negative nahi hone dega.</p>
    </div>

    <div class="card">
      <h3>Lines</h3>
      <table><thead><tr><th>Item</th><th>Qty</th><th>Rate</th><th>GST%</th><th>Total</th></tr></thead><tbody>
      {line_rows or "<tr><td colspan='5'>No lines</td></tr>"}
      </tbody></table>
    </div>

    <div class="card">
      <h3>Add Payment</h3>
      <form method="post" action="/payments/create">
        <input type="hidden" name="party_id" value="{inv.party_id}">
        <input type="hidden" name="invoice_id" value="{invoice_id}">
        <div class="grid">
          <div><label>Date</label><input name="pay_date" type="date" value="{date.today().isoformat()}"></div>
          <div><label>Amount</label><input name="amount" type="number" step="0.01" required></div>
          <div><label>Mode</label>
            <select name="mode"><option>UPI</option><option>NEFT</option><option>CASH</option><option>CHEQUE</option></select>
          </div>
          <div><label>Ref/UTR</label><input name="ref"></div>
        </div>
        <p><button class="btn" type="submit">Save Payment</button></p>
      </form>
    </div>
    """
    return page("Invoice Detail", body)


@app.post("/invoices/{invoice_id}/add_line")
def invoice_add_line(
    invoice_id: int,
    item_id: int = Form(...),
    qty: int = Form(...),
    rate: float = Form(...),
    gst_percent: float = Form(0.0),
):
    with Session(engine) as s:
        inv = s.get(Invoice, invoice_id)
        if not inv:
            raise HTTPException(404, "Invoice not found")

        # reduce stock
        st = get_stock(s, inv.warehouse_id, item_id)
        if st.qty < qty:
            raise HTTPException(400, f"Insufficient stock. Available: {st.qty}")
        st.qty -= qty
        s.add(st)

        s.add(InvoiceLine(
            invoice_id=invoice_id, item_id=item_id, qty=qty, rate=rate, gst_percent=gst_percent
        ))
        s.commit()

        update_invoice_status(s, invoice_id)

    return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)


@app.get("/invoices/{invoice_id}/print", response_class=HTMLResponse)
def invoice_print(invoice_id: int):
    with Session(engine) as s:
        inv = s.get(Invoice, invoice_id)
        if not inv:
            raise HTTPException(404, "Invoice not found")
        p = s.get(Party, inv.party_id)
        w = s.get(Warehouse, inv.warehouse_id)
        lines = s.exec(select(InvoiceLine).where(InvoiceLine.invoice_id == invoice_id)).all()
        totals = invoice_totals(s, invoice_id)

        rows = ""
        for ln in lines:
            it = s.get(Item, ln.item_id)
            amt = ln.qty * ln.rate
            gst = amt * (ln.gst_percent/100.0)
            rows += f"<tr><td>{it.sku if it else ln.item_id}</td><td>{it.title if it else ''}</td><td>{ln.qty}</td><td>{ln.rate}</td><td>{ln.gst_percent}</td><td>{round(amt+gst,2)}</td></tr>"

    html = f"""
    <html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
    <style>
      body{{font-family:Arial;margin:18px}}
      table{{border-collapse:collapse;width:100%}}
      th,td{{border:1px solid #ddd;padding:8px;font-size:13px}}
      .h{{display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap}}
    </style>
    </head><body>
      <div class="h">
        <div>
          <h2>Tax Invoice</h2>
          <div><b>Invoice No:</b> {inv.invoice_no}</div>
          <div><b>Date:</b> {inv.invoice_date}</div>
          <div><b>Warehouse:</b> {w.name if w else inv.warehouse_id}</div>
        </div>
        <div>
          <h3>Bill To</h3>
          <div><b>{p.name if p else inv.party_id}</b></div>
          <div>{p.billing_address or ""}</div>
          <div><b>GSTIN:</b> {p.gstin or ""}</div>
          <div><b>Phone:</b> {p.phone or ""}</div>
        </div>
      </div>
      <hr>
      <table>
        <thead><tr><th>SKU</th><th>Title</th><th>Qty</th><th>Rate</th><th>GST%</th><th>Line Total</th></tr></thead>
        <tbody>{rows or "<tr><td colspan='6'>No lines</td></tr>"}</tbody>
      </table>
      <h3 style="text-align:right">Subtotal: {totals["subtotal"]} | GST: {totals["gst"]} | Total: {totals["total"]}</h3>
      <p style="text-align:right">Paid: {totals["paid"]} | Balance: {totals["balance"]}</p>
      <p><i>Notes:</i> {inv.notes or ""}</p>
      <script>window.print()</script>
    </body></html>
    """
    return HTMLResponse(html)


# -----------------------------
# Payments
# -----------------------------
@app.get("/payments", response_class=HTMLResponse)
def payments():
    with Session(engine) as s:
        pays = s.exec(select(Payment).order_by(Payment.id.desc())).all()

    trows = ""
    with Session(engine) as s:
        for pmt in pays:
            party = s.get(Party, pmt.party_id)
            inv = s.get(Invoice, pmt.invoice_id) if pmt.invoice_id else None
            trows += f"<tr><td>{pmt.pay_date}</td><td>{party.name if party else pmt.party_id}</td><td>{inv.invoice_no if inv else ''}</td><td>{pmt.amount}</td><td>{pmt.mode}</td><td>{pmt.ref or ''}</td></tr>"

    body = f"""
    <div class="card">
      <p style="color:#555">Payments invoice page se bhi add ho jaate hain. Yahan list hai.</p>
      <table><thead><tr><th>Date</th><th>Party</th><th>Invoice</th><th>Amount</th><th>Mode</th><th>Ref</th></tr></thead><tbody>
      {trows or "<tr><td colspan='6'>No payments</td></tr>"}
      </tbody></table>
    </div>
    """
    return page("Payments", body)


@app.post("/payments/create")
def payments_create(
    party_id: int = Form(...),
    invoice_id: int = Form(...),
    pay_date: str = Form(date.today().isoformat()),
    amount: float = Form(...),
    mode: str = Form("UPI"),
    ref: str = Form(""),
):
    with Session(engine) as s:
        s.add(Payment(
            party_id=party_id,
            invoice_id=invoice_id,
            pay_date=date.fromisoformat(pay_date),
            amount=amount,
            mode=mode,
            ref=ref or None
        ))
        s.commit()
        update_invoice_status(s, int(invoice_id))
    return RedirectResponse(f"/invoices/{invoice_id}", status_code=303)


# -----------------------------
# Returns
# -----------------------------
@app.get("/returns", response_class=HTMLResponse)
def returns_page():
    with Session(engine) as s:
        parties = s.exec(select(Party)).all()
        whs = s.exec(select(Warehouse)).all()
        items = s.exec(select(Item)).all()
        returns = s.exec(select(ReturnNote).order_by(ReturnNote.id.desc())).all()

    party_opts = "".join([f"<option value='{p.id}'>{p.name}</option>" for p in parties])
    wh_opts = "".join([f"<option value='{w.id}'>{w.name}</option>" for w in whs])
    item_opts = "".join([f"<option value='{i.id}'>{i.sku} - {i.title}</option>" for i in items])

    trows = ""
    with Session(engine) as s:
        for r in returns:
            p = s.get(Party, r.party_id)
            w = s.get(Warehouse, r.warehouse_id)
            trows += f"<tr><td><a href='/returns/{r.id}'><b>RET-{r.id}</b></a><br>{r.return_date}</td><td>{p.name if p else r.party_id}</td><td>{w.name if w else r.warehouse_id}</td><td>{r.reason}</td></tr>"

    body = f"""
    <div class="card">
      <form method="post" action="/returns/create">
        <div class="grid">
          <div><label>Party</label><select name="party_id" required>{party_opts}</select></div>
          <div><label>Warehouse (stock will add here)</label><select name="warehouse_id" required>{wh_opts}</select></div>
          <div><label>Return Date</label><input name="return_date" type="date" value="{date.today().isoformat()}"></div>
          <div><label>Reason</label><input name="reason" value="Unsold"></div>
          <div style="grid-column:1 / -1"><label>Notes</label><input name="notes"></div>
        </div>
        <p><button class="btn" type="submit">Create Return Note</button></p>
      </form>
    </div>

    <div class="card">
      <table><thead><tr><th>Return</th><th>Party</th><th>Warehouse</th><th>Reason</th></tr></thead><tbody>
      {trows or "<tr><td colspan='4'>No returns</td></tr>"}
      </tbody></table>
    </div>
    """
    return page("Returns", body)


@app.post("/returns/create")
def returns_create(
    party_id: int = Form(...),
    warehouse_id: int = Form(...),
    return_date: str = Form(date.today().isoformat()),
    reason: str = Form("Unsold"),
    notes: str = Form(""),
):
    with Session(engine) as s:
        rn = ReturnNote(
            party_id=party_id,
            warehouse_id=warehouse_id,
            return_date=date.fromisoformat(return_date),
            reason=reason,
            notes=notes or None
        )
        s.add(rn)
        s.commit()
        s.refresh(rn)
    return RedirectResponse(f"/returns/{rn.id}", status_code=303)


@app.get("/returns/{return_id}", response_class=HTMLResponse)
def returns_view(return_id: int):
    with Session(engine) as s:
        rn = s.get(ReturnNote, return_id)
        if not rn:
            raise HTTPException(404, "Return not found")
        p = s.get(Party, rn.party_id)
        w = s.get(Warehouse, rn.warehouse_id)
        items = s.exec(select(Item)).all()
        lines = s.exec(select(ReturnLine).where(ReturnLine.return_id == return_id)).all()

    item_opts = "".join([f"<option value='{i.id}'>{i.sku} - {i.title}</option>" for i in items])
    line_rows = ""
    with Session(engine) as s:
        for ln in lines:
            it = s.get(Item, ln.item_id)
            line_rows += f"<tr><td>{it.sku if it else ln.item_id}</td><td>{ln.qty}</td></tr>"

    body = f"""
    <div class="card">
      <div class="row">
        <div class="pill">Return: <b>RET-{rn.id}</b></div>
        <div class="pill">Date: <b>{rn.return_date}</b></div>
        <div class="pill">Party: <b>{p.name if p else rn.party_id}</b></div>
        <div class="pill">Warehouse: <b>{w.name if w else rn.warehouse_id}</b></div>
        <div class="pill">Reason: <b>{rn.reason}</b></div>
      </div>
    </div>

    <div class="card">
      <h3>Add Return Line (Auto stock add to warehouse)</h3>
      <form method="post" action="/returns/{return_id}/add_line">
        <div class="grid">
          <div><label>Item</label><select name="item_id" required>{item_opts}</select></div>
          <div><label>Qty</label><input name="qty" type="number" required></div>
        </div>
        <p><button class="btn" type="submit">Add Return Line</button></p>
      </form>
    </div>

    <div class="card">
      <h3>Return Lines</h3>
      <table><thead><tr><th>Item</th><th>Qty</th></tr></thead><tbody>
      {line_rows or "<tr><td colspan='2'>No lines</td></tr>"}
      </tbody></table>
    </div>
    """
    return page("Return Detail", body)


@app.post("/returns/{return_id}/add_line")
def return_add_line(return_id: int, item_id: int = Form(...), qty: int = Form(...)):
    with Session(engine) as s:
        rn = s.get(ReturnNote, return_id)
        if not rn:
            raise HTTPException(404, "Return not found")

        st = get_stock(s, rn.warehouse_id, item_id)
        st.qty += int(qty)
        s.add(st)

        s.add(ReturnLine(return_id=return_id, item_id=item_id, qty=qty))
        s.commit()

    return RedirectResponse(f"/returns/{return_id}", status_code=303)


# -----------------------------
# Statements (Date/Month/Year wise)
# -----------------------------
@app.get("/statements", response_class=HTMLResponse)
def statements():
    with Session(engine) as s:
        parties = s.exec(select(Party)).all()
    party_opts = "".join([f"<option value='{p.id}'>{p.name}</option>" for p in parties])
    body = f"""
    <div class="card">
      <form method="get" action="/statements/view">
        <div class="grid">
          <div><label>Party</label><select name="party_id" required>{party_opts}</select></div>
          <div><label>From</label><input name="from_date" type="date" value="{date.today().replace(day=1).isoformat()}"></div>
          <div><label>To</label><input name="to_date" type="date" value="{date.today().isoformat()}"></div>
          <div><label>Group By</label>
            <select name="group_by">
              <option value="day">Day</option>
              <option value="month" selected>Month</option>
              <option value="year">Year</option>
            </select>
          </div>
        </div>
        <p><button class="btn" type="submit">Generate Statement</button></p>
      </form>
      <p style="color:#666;font-size:13px">Statement: invoices + payments + returns count. Outstanding = Total - Paid.</p>
    </div>
    """
    return page("Party Statement", body)


@app.get("/statements/view", response_class=HTMLResponse)
def statements_view(party_id: int, from_date: str, to_date: str, group_by: str = "month"):
    fd = date.fromisoformat(from_date)
    td = date.fromisoformat(to_date)

    with Session(engine) as s:
        party = s.get(Party, party_id)
        if not party:
            raise HTTPException(404, "Party not found")

        invs = s.exec(
            select(Invoice).where(Invoice.party_id == party_id, Invoice.invoice_date >= fd, Invoice.invoice_date <= td)
        ).all()

        # compute totals
        inv_rows = []
        total_total = 0.0
        total_paid = 0.0
        for inv in invs:
            t = invoice_totals(s, inv.id)
            inv_rows.append((inv.invoice_date, inv.invoice_no, t["total"], t["paid"], t["balance"], inv.status))
            total_total += t["total"]
            total_paid += t["paid"]

        # payments in range (party wise)
        pays = s.exec(
            select(Payment).where(Payment.party_id == party_id, Payment.pay_date >= fd, Payment.pay_date <= td)
        ).all()
        pay_sum = sum(p.amount for p in pays)

        # returns count in range (party wise)
        rns = s.exec(
            select(ReturnNote).where(ReturnNote.party_id == party_id, ReturnNote.return_date >= fd, ReturnNote.return_date <= td)
        ).all()

    inv_table = "".join([
        f"<tr><td>{d}</td><td>{no}</td><td>{tot}</td><td>{paid}</td><td>{bal}</td><td>{st}</td></tr>"
        for (d, no, tot, paid, bal, st) in inv_rows
    ])

    balance = round(total_total - total_paid, 2)

    body = f"""
    <div class="card">
      <div class="row">
        <div class="pill">Party: <b>{party.name}</b></div>
        <div class="pill">From: <b>{fd}</b></div>
        <div class="pill">To: <b>{td}</b></div>
        <div class="pill">Invoices: <b>{len(inv_rows)}</b></div>
        <div class="pill">Payments entries: <b>{len(pays)}</b></div>
        <div class="pill">Returns notes: <b>{len(rns)}</b></div>
      </div>
      <h3 style="margin-top:10px">Summary</h3>
      <p>Total Billing: <b>{round(total_total,2)}</b> | Total Paid: <b>{round(total_paid,2)}</b> | Outstanding: <b>{balance}</b></p>
    </div>

    <div class="card">
      <h3>Invoices (with paid/balance)</h3>
      <table>
        <thead><tr><th>Date</th><th>Invoice</th><th>Total</th><th>Paid</th><th>Balance</th><th>Status</th></tr></thead>
        <tbody>{inv_table or "<tr><td colspan='6'>No invoices in range</td></tr>"}</tbody>
      </table>
    </div>
    """
    return page("Statement View", body)


# -----------------------------
# Simple API endpoints (optional)
# -----------------------------
@app.get("/api/party/{party_id}/outstanding")
def api_party_outstanding(party_id: int):
    with Session(engine) as s:
        invs = s.exec(select(Invoice).where(Invoice.party_id == party_id)).all()
        total = 0.0
        paid = 0.0
        for inv in invs:
            t = invoice_totals(s, inv.id)
            total += t["total"]
            paid += t["paid"]
        return {"party_id": party_id, "total_billing": round(total,2), "total_paid": round(paid,2), "outstanding": round(total-paid,2)}