import os
from datetime import datetime, timedelta, timezone, date
from typing import List, Optional, Dict, Any

from fastapi import FastAPI, UploadFile, File, Form, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from database import db
from schemas import Customer, Po, Invoice, Agreement, Payment

app = FastAPI(title="Business Ops Dashboard API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -----------------------------
# Helpers
# -----------------------------

def oid_str(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return doc
    d = dict(doc)
    if "_id" in d:
        d["id"] = str(d.pop("_id"))
    # Convert datetimes to isoformat
    for k, v in list(d.items()):
        if isinstance(v, (datetime, date)):
            d[k] = v.isoformat()
    return d


def ensure_upload_dir() -> str:
    up = os.path.join(os.getcwd(), "uploads")
    os.makedirs(up, exist_ok=True)
    return up


def save_upload(entity: str, entity_id: str, field: str, file: UploadFile) -> str:
    folder = ensure_upload_dir()
    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    safe_name = f"{entity}_{entity_id}_{field}_{ts}_{file.filename}"
    path = os.path.join(folder, safe_name)
    with open(path, "wb") as f:
        f.write(file.file.read())
    # Expose via static path (served by FastAPI below)
    return f"/uploads/{safe_name}"


@app.get("/uploads/{filename}")
async def serve_upload(filename: str):
    from fastapi.responses import FileResponse
    path = os.path.join(ensure_upload_dir(), filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(path)


# -----------------------------
# Health and DB test
# -----------------------------

@app.get("/")
def root():
    return {"message": "Business Ops Dashboard API running"}


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }
    try:
        if db is not None:
            response["database"] = "✅ Connected & Working"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            response["collections"] = db.list_collection_names()[:10]
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"
    return response


# -----------------------------
# Customers
# -----------------------------

@app.post("/api/customers")
def create_customer(payload: Customer):
    data = payload.model_dump()
    data["created_at"] = datetime.now(timezone.utc)
    data["updated_at"] = datetime.now(timezone.utc)
    res = db["customer"].insert_one(data)
    return {"id": str(res.inserted_id)}


@app.get("/api/customers")
def list_customers():
    docs = list(db["customer"].find().sort("created_at", -1))
    return [oid_str(d) for d in docs]


# -----------------------------
# Purchase Orders
# -----------------------------

@app.post("/api/pos")
def create_po(payload: Po):
    # Ensure customer exists
    from bson import ObjectId
    try:
        cust = db["customer"].find_one({"_id": ObjectId(payload.customer_id)})
        if not cust:
            raise HTTPException(status_code=404, detail="Customer not found")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid customer_id")

    data = payload.model_dump()
    data["created_at"] = datetime.now(timezone.utc)
    data["updated_at"] = datetime.now(timezone.utc)
    res = db["po"].insert_one(data)
    return {"id": str(res.inserted_id)}


@app.get("/api/pos")
def list_pos(customer_id: Optional[str] = None):
    q: Dict[str, Any] = {}
    if customer_id:
        from bson import ObjectId
        try:
            q["customer_id"] = customer_id
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid customer_id")
    docs = list(db["po"].find(q).sort("created_at", -1))
    # add computed po_balance = po.amount - sum(invoice.amount)
    for d in docs:
        po_id = str(d.get("_id"))
        invs = list(db["invoice"].find({"po_id": po_id}))
        billed = sum(i.get("amount", 0) for i in invs)
        d["billed_amount"] = billed
        d["po_balance"] = max(0.0, float(d.get("amount", 0)) - billed)
    return [oid_str(d) for d in docs]


# -----------------------------
# Invoices
# -----------------------------

@app.post("/api/invoices")
def create_invoice(payload: Invoice):
    from bson import ObjectId
    # Verify references
    try:
        po = db["po"].find_one({"_id": ObjectId(payload.po_id)})
        if not po:
            raise HTTPException(status_code=404, detail="PO not found")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid po_id")

    try:
        cust = db["customer"].find_one({"_id": ObjectId(payload.customer_id)})
        if not cust:
            raise HTTPException(status_code=404, detail="Customer not found")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid customer_id")

    data = payload.model_dump()
    # normalize payment status by current amounts
    amt = data.get("amount", 0.0) or 0.0
    rec = data.get("amount_received", 0.0) or 0.0
    if rec <= 0:
        data["payment_status"] = "Pending"
    elif rec < amt:
        data["payment_status"] = "Partial"
    else:
        data["payment_status"] = "Paid"
    data["created_at"] = datetime.now(timezone.utc)
    data["updated_at"] = datetime.now(timezone.utc)
    res = db["invoice"].insert_one(data)
    return {"id": str(res.inserted_id)}


@app.get("/api/invoices")
def list_invoices(po_id: Optional[str] = None, customer_id: Optional[str] = None):
    q: Dict[str, Any] = {}
    if po_id:
        q["po_id"] = po_id
    if customer_id:
        q["customer_id"] = customer_id
    docs = list(db["invoice"].find(q).sort("created_at", -1))
    # compute balance per invoice
    for d in docs:
        amt = float(d.get("amount", 0))
        rec = float(d.get("amount_received", 0))
        d["balance_amount"] = max(0.0, amt - rec)
        if rec <= 0:
            d["payment_status"] = "Pending"
        elif rec < amt:
            d["payment_status"] = "Partial"
        else:
            d["payment_status"] = "Paid"
    return [oid_str(d) for d in docs]


# -----------------------------
# Payments
# -----------------------------

@app.post("/api/payments")
def create_payment(payload: Payment):
    from bson import ObjectId
    # Verify invoice and customer
    try:
        inv = db["invoice"].find_one({"_id": ObjectId(payload.invoice_id)})
        if not inv:
            raise HTTPException(status_code=404, detail="Invoice not found")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid invoice_id")

    try:
        cust = db["customer"].find_one({"_id": ObjectId(payload.customer_id)})
        if not cust:
            raise HTTPException(status_code=404, detail="Customer not found")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid customer_id")

    data = payload.model_dump()
    data["created_at"] = datetime.now(timezone.utc)
    data["updated_at"] = datetime.now(timezone.utc)
    res = db["payment"].insert_one(data)

    # Update invoice totals
    total_received = sum(p.get("amount", 0) for p in db["payment"].find({"invoice_id": payload.invoice_id}))
    amt = float(inv.get("amount", 0))
    status = "Paid" if total_received >= amt else ("Partial" if total_received > 0 else "Pending")
    db["invoice"].update_one({"_id": inv["_id"]}, {"$set": {"amount_received": float(total_received), "payment_status": status, "updated_at": datetime.now(timezone.utc)}})

    return {"id": str(res.inserted_id), "invoice_amount_received": float(total_received), "invoice_status": status}


@app.get("/api/payments")
def list_payments(invoice_id: Optional[str] = None):
    q: Dict[str, Any] = {}
    if invoice_id:
        q["invoice_id"] = invoice_id
    docs = list(db["payment"].find(q).sort("created_at", -1))
    return [oid_str(d) for d in docs]


# -----------------------------
# Agreements & NDAs
# -----------------------------

@app.post("/api/agreements")
def create_agreement(payload: Agreement, background_tasks: BackgroundTasks):
    from bson import ObjectId
    # verify customer
    try:
        cust = db["customer"].find_one({"_id": ObjectId(payload.customer_id)})
        if not cust:
            raise HTTPException(status_code=404, detail="Customer not found")
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid customer_id")

    data = payload.model_dump()
    # auto compute renewal_due
    end_date = data.get("end_date")
    if end_date:
        if isinstance(end_date, str):
            end_date = date.fromisoformat(end_date)
        data["renewal_due"] = end_date - timedelta(days=30)
        # compute renewal_status
        today = date.today()
        if end_date < today:
            data["renewal_status"] = "Expired"
        elif data["renewal_due"] <= today <= end_date:
            data["renewal_status"] = "Due"
        else:
            data["renewal_status"] = "Active"

    data["created_at"] = datetime.now(timezone.utc)
    data["updated_at"] = datetime.now(timezone.utc)
    res = db["agreement"].insert_one(data)

    background_tasks.add_task(check_and_notify_agreement, str(res.inserted_id))

    return {"id": str(res.inserted_id)}


@app.get("/api/agreements")
def list_agreements(customer_id: Optional[str] = None, due_within_days: Optional[int] = None):
    q: Dict[str, Any] = {}
    if customer_id:
        q["customer_id"] = customer_id
    docs = list(db["agreement"].find(q).sort("created_at", -1))
    today = date.today()
    result = []
    for d in docs:
        # recalc renewal fields
        end_date = d.get("end_date")
        if isinstance(end_date, str):
            try:
                end_date = date.fromisoformat(end_date)
            except Exception:
                end_date = None
        if end_date:
            renewal_due = end_date - timedelta(days=30)
            d["renewal_due"] = renewal_due
            if end_date < today:
                d["renewal_status"] = "Expired"
            elif renewal_due <= today <= end_date:
                d["renewal_status"] = "Due"
            else:
                d["renewal_status"] = "Active"
        if due_within_days is not None and end_date:
            if not (today <= end_date <= today + timedelta(days=due_within_days)):
                continue
        result.append(oid_str(d))
    return result


def send_email_stub(to_emails: List[str], subject: str, body: str):
    # This is a stub. In a real deployment, configure SMTP creds and send.
    print("EMAIL -> ", {"to": to_emails, "subject": subject, "body": body[:180] + ("..." if len(body) > 180 else "")})


def check_and_notify_agreement(agreement_id: str):
    from bson import ObjectId
    ag = db["agreement"].find_one({"_id": ObjectId(agreement_id)})
    if not ag:
        return
    # compute if within 30 days from renewal_due (i.e., due window)
    end_date = ag.get("end_date")
    if isinstance(end_date, str):
        try:
            end_date = date.fromisoformat(end_date)
        except Exception:
            end_date = None
    if not end_date:
        return
    today = date.today()
    renewal_due = end_date - timedelta(days=30)
    if renewal_due <= today <= end_date:
        # notify admin / optional roles
        subject = f"Renewal due for {ag.get('name')} ({ag.get('type')})"
        body = f"Agreement {ag.get('name')} for customer {ag.get('customer_id')} is due on {end_date}.\nPlease review and renew."
        admin_emails = [os.getenv("ADMIN_EMAIL", "admin@example.com")]
        send_email_stub(admin_emails, subject, body)


@app.post("/api/agreements/check-renewals")
def manual_check_renewals(background_tasks: BackgroundTasks):
    # Trigger notifications for all agreements in due window
    docs = list(db["agreement"].find({}))
    for d in docs:
        background_tasks.add_task(check_and_notify_agreement, str(d.get("_id")))
    return {"checked": len(docs)}


# -----------------------------
# Uploads per entity
# -----------------------------

@app.post("/api/upload/{entity}/{entity_id}/{field}")
async def upload_document(entity: str, entity_id: str, field: str, file: UploadFile = File(...)):
    entity = entity.lower()
    if entity not in {"customer", "po", "invoice", "agreement"}:
        raise HTTPException(status_code=400, detail="Unsupported entity")
    url = save_upload(entity, entity_id, field, file)
    from bson import ObjectId
    try:
        col = db[entity]
        update = {"$set": {field: url, "updated_at": datetime.now(timezone.utc)}}
        col.update_one({"_id": ObjectId(entity_id)}, update)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid entity id or field")
    return {"url": url}


# -----------------------------
# Dashboard Summary
# -----------------------------

@app.get("/api/dashboard-summary")
def dashboard_summary():
    total_pos = db["po"].count_documents({})
    total_invoices = db["invoice"].count_documents({})
    # Paid vs Pending
    paid = db["invoice"].count_documents({"payment_status": "Paid"})
    partial = db["invoice"].count_documents({"payment_status": "Partial"})
    pending = db["invoice"].count_documents({"payment_status": "Pending"})

    # Total outstanding = sum(balance of invoices)
    invoices = list(db["invoice"].find({}))
    outstanding = 0.0
    for inv in invoices:
        amt = float(inv.get("amount", 0))
        rec = float(inv.get("amount_received", 0))
        outstanding += max(0.0, amt - rec)

    # Upcoming renewals within 60/30/7 days
    today = date.today()
    def due_within(days: int) -> int:
        upper = today + timedelta(days=days)
        cnt = 0
        for ag in db["agreement"].find({}):
            end_date = ag.get("end_date")
            if isinstance(end_date, str):
                try:
                    end_date = date.fromisoformat(end_date)
                except Exception:
                    continue
            if not end_date:
                continue
            if today <= end_date <= upper:
                cnt += 1
        return cnt

    upcoming = {
        "60": due_within(60),
        "30": due_within(30),
        "7": due_within(7)
    }

    # Upcoming invoices suggestion: POs with remaining balance
    pos = list(db["po"].find({}))
    upcoming_invoices = 0
    for p in pos:
        po_id = str(p.get("_id"))
        billed = sum(i.get("amount", 0) for i in db["invoice"].find({"po_id": po_id}))
        if float(p.get("amount", 0)) - billed > 0:
            upcoming_invoices += 1

    return {
        "totals": {
            "purchase_orders": total_pos,
            "invoices": total_invoices,
            "paid_invoices": paid,
            "partial_invoices": partial,
            "pending_invoices": pending,
            "outstanding_amount": round(outstanding, 2)
        },
        "upcoming": upcoming,
        "upcoming_invoices_pos": upcoming_invoices
    }
