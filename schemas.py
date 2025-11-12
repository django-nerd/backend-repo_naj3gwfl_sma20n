"""
Database Schemas for the Business Ops Dashboard

Each Pydantic model maps to a MongoDB collection using the lowercase of the
class name as the collection name, e.g. Customer -> "customer".

These schemas are used for validation at the API boundary.
"""

from __future__ import annotations
from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List, Literal
from datetime import date

# -----------------------------
# Base config
# -----------------------------

class Model(BaseModel):
    model_config = {
        "arbitrary_types_allowed": True,
        "populate_by_name": True,
        "extra": "ignore",
    }

# -----------------------------
# Core Entities
# -----------------------------

class Customer(Model):
    name: str = Field(..., description="Customer Name")
    contact_person: Optional[str] = Field(None, description="Primary contact person")
    email: Optional[EmailStr] = Field(None, description="Contact email")
    phone: Optional[str] = Field(None, description="Phone number")
    industry: Optional[str] = Field(None, description="Industry vertical")
    tax_id: Optional[str] = Field(None, description="PAN/GST or equivalent")
    address: Optional[str] = Field(None, description="Registered address")
    notes: Optional[str] = Field(None, description="Internal notes")

    # Upload URLs
    company_profile_url: Optional[str] = Field(None, description="Company profile / intro doc")
    kyc_url: Optional[str] = Field(None, description="KYC document URL")
    master_service_agreement_url: Optional[str] = Field(None, description="MSA document URL")


class Po(Model):
    po_number: str = Field(..., description="PO Number")
    customer_id: str = Field(..., description="Linked Customer _id as string")
    po_date: Optional[date] = Field(None, description="PO date")
    amount: float = Field(..., ge=0, description="PO total amount")
    description: Optional[str] = Field(None, description="PO description")
    validity: Optional[str] = Field(None, description="Validity terms / period")
    status: Literal["Active", "Closed", "Partially Billed"] = Field("Active")

    # Upload URLs
    po_pdf_url: Optional[str] = Field(None, description="PO PDF copy URL")
    # Use empty list by default to avoid Optional[List] + None typing issues on some runtimes
    related_docs_url: List[str] = Field(default_factory=list, description="Other related document URLs")


class Invoice(Model):
    invoice_number: str = Field(..., description="Invoice Number")
    po_id: str = Field(..., description="Linked PO _id as string")
    customer_id: str = Field(..., description="Linked Customer _id as string")
    invoice_date: Optional[date] = Field(None)
    due_date: Optional[date] = Field(None)
    amount: float = Field(..., ge=0, description="Invoice amount")
    amount_received: float = Field(0.0, ge=0, description="Total amount received so far")
    payment_status: Literal["Pending", "Partial", "Paid"] = Field("Pending")
    mode_of_payment: Optional[str] = Field(None)
    payment_timeline: Optional[str] = Field(None, description="Milestones / timeline details")

    # Upload URLs
    invoice_pdf_url: Optional[str] = Field(None, description="Invoice PDF URL")
    proof_of_payment_url: Optional[str] = Field(None, description="Proof of payment URL (UTR/Slip)")


class Agreement(Model):
    name: str = Field(..., description="Agreement name")
    type: Literal["Agreement", "NDA"] = Field("Agreement")
    customer_id: str = Field(..., description="Linked Customer _id as string")
    start_date: Optional[date] = Field(None)
    end_date: Optional[date] = Field(None)
    terms_summary: Optional[str] = Field(None)
    renewal_status: Literal["Active", "Due", "Expired"] = Field("Active")

    # Computed on backend: renewal_due = end_date - 30 days
    renewal_due: Optional[date] = Field(None, description="Auto: 30 days before end_date")

    # Upload URLs
    signed_copy_url: Optional[str] = Field(None)
    supporting_docs_url: List[str] = Field(default_factory=list)


class Payment(Model):
    payment_id: str = Field(..., description="Internal/Bank reference")
    invoice_id: str = Field(..., description="Linked Invoice _id as string")
    customer_id: str = Field(..., description="Linked Customer _id as string")
    date: Optional[date] = Field(None)
    amount: float = Field(..., ge=0)
    mode: Optional[str] = Field(None)
    remarks: Optional[str] = Field(None)
