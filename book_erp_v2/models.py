from __future__ import annotations
from datetime import date
from typing import Optional
from sqlmodel import SQLModel, Field


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    password_hash: str
    role: str = "WAREHOUSE"  # ADMIN, WAREHOUSE, BILLING, ACCOUNTS
    is_active: bool = True


class Warehouse(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    city: str = "Noida"
    state: str = "Uttar Pradesh"


class Item(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    sku: str = Field(index=True, unique=True)
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
    barcode: Optional[str] = None


class Party(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    type: str = "Distributor"
    phone: Optional[str] = None
    email: Optional[str] = None
    gstin: Optional[str] = None
    billing_address: Optional[str] = None
    shipping_address: Optional[str] = None
    state: Optional[str] = None  # place of supply
    credit_limit: float = 0.0
    payment_terms_days: int = 0
    is_blocked: bool = False


class PartyPrice(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    party_id: int = Field(foreign_key="party.id", index=True)
    item_id: int = Field(foreign_key="item.id", index=True)
    discount_percent: float = 0.0


class Stock(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    warehouse_id: int = Field(foreign_key="warehouse.id", index=True)
    item_id: int = Field(foreign_key="item.id", index=True)
    qty: int = 0


class SalesOrder(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    so_no: str = Field(index=True, unique=True)
    party_id: int = Field(foreign_key="party.id", index=True)
    warehouse_id: int = Field(foreign_key="warehouse.id", index=True)
    so_date: date = Field(default_factory=date.today)
    status: str = "OPEN"  # OPEN, APPROVED, DISPATCHED, CANCELLED
    notes: Optional[str] = None


class SalesOrderLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    so_id: int = Field(foreign_key="salesorder.id", index=True)
    item_id: int = Field(foreign_key="item.id", index=True)
    qty: int
    rate: float
    gst_percent: float = 0.0
    discount_percent: float = 0.0


class Challan(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    dc_no: str = Field(index=True, unique=True)
    so_id: int = Field(foreign_key="salesorder.id", index=True)
    dc_date: date = Field(default_factory=date.today)
    transporter: Optional[str] = None
    lr_no: Optional[str] = None
    status: str = "OPEN"  # OPEN, INVOICED


class ChallanLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    dc_id: int = Field(foreign_key="challan.id", index=True)
    item_id: int = Field(foreign_key="item.id", index=True)
    qty: int


class Invoice(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    invoice_no: str = Field(index=True, unique=True)
    dc_id: int = Field(foreign_key="challan.id", index=True)
    party_id: int = Field(foreign_key="party.id", index=True)
    warehouse_id: int = Field(foreign_key="warehouse.id", index=True)
    invoice_date: date = Field(default_factory=date.today)
    place_of_supply_state: Optional[str] = None
    status: str = "OPEN"  # OPEN/PAID/PARTIAL/CANCELLED
    notes: Optional[str] = None
    irn: Optional[str] = None  # e-invoice IRN optional


class InvoiceLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    invoice_id: int = Field(foreign_key="invoice.id", index=True)
    item_id: int = Field(foreign_key="item.id", index=True)
    qty: int
    rate: float
    gst_percent: float = 0.0
    discount_percent: float = 0.0


class Payment(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    party_id: int = Field(foreign_key="party.id", index=True)
    invoice_id: Optional[int] = Field(default=None, foreign_key="invoice.id", index=True)
    pay_date: date = Field(default_factory=date.today)
    amount: float = 0.0
    mode: str = "UPI"
    ref: Optional[str] = None

from datetime import date
from typing import Optional
from sqlmodel import SQLModel, Field

class ReturnNote(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    rn_no: str = Field(index=True, unique=True)
    party_id: int = Field(foreign_key="party.id", index=True)
    warehouse_id: int = Field(foreign_key="warehouse.id", index=True)
    return_date: date = Field(default_factory=date.today)
    reason: str = "Unsold"
    notes: Optional[str] = None
    status: str = "OPEN"  # OPEN, POSTED

class ReturnLine(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    return_id: int = Field(foreign_key="returnnote.id", index=True)
    item_id: int = Field(foreign_key="item.id", index=True)
    qty: int
