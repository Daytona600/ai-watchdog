from typing import Optional
from pydantic import BaseModel, Field


class ItemBase(BaseModel):
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    unit: str = "each"
    location: Optional[str] = None
    par_level: float = 0
    expiry_date: Optional[str] = None  # 'YYYY-MM-DD'


class ItemCreate(ItemBase):
    barcode: Optional[str] = None
    quantity: float = 0


class ItemUpdate(BaseModel):
    name: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    quantity: Optional[float] = None
    unit: Optional[str] = None
    location: Optional[str] = None
    par_level: Optional[float] = None
    expiry_date: Optional[str] = None
    barcode: Optional[str] = None


class ItemOut(ItemBase):
    id: int
    barcode: Optional[str] = None
    quantity: float
    created_at: str
    updated_at: str


class ScanInRequest(BaseModel):
    barcode: str = Field(..., min_length=1)
    quantity: float = 1
    unit: Optional[str] = None
    location: Optional[str] = None
    expiry_date: Optional[str] = None
    note: Optional[str] = None


class ScanOutRequest(BaseModel):
    barcode: str = Field(..., min_length=1)
    quantity: float = 1
    note: Optional[str] = None
    allow_negative: bool = False


class ScanResult(BaseModel):
    item: ItemOut
    quantity_applied: float
    lookup_source: Optional[str] = None
    clamped: bool = False


class BarcodeLookupResult(BaseModel):
    barcode: str
    found: bool
    name: Optional[str] = None
    brand: Optional[str] = None
    category: Optional[str] = None
    source: str  # "cache" | "open_food_facts"


class ExpiringItemOut(ItemOut):
    days_until_expiry: int


class ShoppingListEntry(BaseModel):
    item_id: int
    barcode: Optional[str] = None
    name: str
    brand: Optional[str] = None
    category: Optional[str] = None
    quantity: float
    unit: str
    par_level: float
    deficit: float
    location: Optional[str] = None


class HealthOut(BaseModel):
    status: str
