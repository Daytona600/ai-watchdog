import os
from datetime import date, datetime
from typing import Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import db
import off_client
from models import (
    BarcodeLookupResult,
    ExpiringItemOut,
    HealthOut,
    ItemCreate,
    ItemOut,
    ItemUpdate,
    ScanInRequest,
    ScanOutRequest,
    ScanResult,
    ShoppingListEntry,
)

app = FastAPI(title="Inventory Service", version="0.1.0")

HA_BASE_URL = os.environ.get("HA_BASE_URL", "http://10.0.0.30:8123")
HA_TOKEN = os.environ.get("HA_TOKEN", "")
SCAN_MODE_ENTITY = "input_select.inventory_scan_mode"
SCAN_SPEAKERS_ENTITY = "input_select.inventory_scan_speakers"
SCAN_MODE_OPTIONS = ("Scan In", "Scan Out")
SCAN_SPEAKERS_OPTIONS = ("Both", "Living Room Only", "Bedroom Only", "Headset Only", "None")


async def _ha_get_state(entity_id: str) -> str:
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.get(
            f"{HA_BASE_URL}/api/states/{entity_id}",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
        )
        r.raise_for_status()
        return r.json()["state"]


async def _ha_select_option(entity_id: str, option: str) -> None:
    async with httpx.AsyncClient(timeout=5.0) as client:
        r = await client.post(
            f"{HA_BASE_URL}/api/services/input_select/select_option",
            headers={"Authorization": f"Bearer {HA_TOKEN}"},
            json={"entity_id": entity_id, "option": option},
        )
        r.raise_for_status()


class SettingUpdate(BaseModel):
    value: str


@app.get("/settings/scan-mode")
async def get_scan_mode_setting():
    try:
        return {"value": await _ha_get_state(SCAN_MODE_ENTITY)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Home Assistant: {e}")


@app.post("/settings/scan-mode")
async def set_scan_mode_setting(req: SettingUpdate):
    if req.value not in SCAN_MODE_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid value")
    try:
        await _ha_select_option(SCAN_MODE_ENTITY, req.value)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Home Assistant: {e}")


@app.get("/settings/scan-speakers")
async def get_scan_speakers_setting():
    try:
        return {"value": await _ha_get_state(SCAN_SPEAKERS_ENTITY)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Home Assistant: {e}")


@app.post("/settings/scan-speakers")
async def set_scan_speakers_setting(req: SettingUpdate):
    if req.value not in SCAN_SPEAKERS_OPTIONS:
        raise HTTPException(status_code=400, detail="Invalid value")
    try:
        await _ha_select_option(SCAN_SPEAKERS_ENTITY, req.value)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach Home Assistant: {e}")


@app.middleware("http")
async def no_store_cache(request, call_next):
    # Everything this service returns is live, mutable data -- never let a
    # browser, proxy, or intermediate cache serve a stale copy after a write.
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.on_event("startup")
def _startup() -> None:
    db.init_db()


def _row_to_item_out(row) -> ItemOut:
    return ItemOut(**dict(row))


@app.get("/health", response_model=HealthOut)
def health() -> HealthOut:
    return HealthOut(status="ok")


@app.post("/scan-in", response_model=ScanResult, status_code=200)
def scan_in(req: ScanInRequest) -> ScanResult:
    conn = db.get_conn()
    try:
        existing = db.get_item_by_barcode(conn, req.barcode)

        if existing:
            new_qty = existing["quantity"] + req.quantity
            fields = {"quantity": new_qty}
            if req.location:
                fields["location"] = req.location
            if req.expiry_date:
                fields["expiry_date"] = req.expiry_date
            updated = db.update_item(conn, existing["id"], **fields)
            db.log_event(
                conn, item_id=updated["id"], barcode=req.barcode, item_name=updated["name"],
                event_type="scan_in", quantity_delta=req.quantity, quantity_after=new_qty,
                source="api", note=req.note,
            )
            return ScanResult(
                item=_row_to_item_out(updated),
                quantity_applied=req.quantity,
                lookup_source="existing_item",
            )

        # New barcode — resolve via cache/Open Food Facts
        lookup = off_client.lookup_barcode(conn, req.barcode)

        if lookup.found:
            name = lookup.name or f"Unknown item {req.barcode}"
        else:
            name = f"Unknown item {req.barcode}"

        new_item = db.create_item(
            conn,
            barcode=req.barcode,
            name=name,
            brand=lookup.brand,
            category=lookup.category,
            quantity=req.quantity,
            unit=req.unit or "each",
            location=req.location,
            par_level=0,
            expiry_date=req.expiry_date,
        )
        db.log_event(
            conn, item_id=new_item["id"], barcode=req.barcode, item_name=new_item["name"],
            event_type="scan_in", quantity_delta=req.quantity, quantity_after=req.quantity,
            source="api", note=req.note,
        )

        if lookup.found:
            lookup_source = lookup.source  # "open_food_facts" or "cache"
        elif lookup.source == "unreachable":
            lookup_source = "stub_unreachable"
        else:
            lookup_source = "stub_not_found"

        return ScanResult(
            item=_row_to_item_out(new_item),
            quantity_applied=req.quantity,
            lookup_source=lookup_source,
        )
    finally:
        conn.close()


@app.post("/scan-out", response_model=ScanResult)
def scan_out(req: ScanOutRequest) -> ScanResult:
    conn = db.get_conn()
    try:
        existing = db.get_item_by_barcode(conn, req.barcode)
        if not existing:
            raise HTTPException(
                status_code=404,
                detail=f"No item found for barcode {req.barcode} — scan it in first.",
            )

        requested = req.quantity
        clamped = False
        if not req.allow_negative and requested > existing["quantity"]:
            applied = existing["quantity"]
            clamped = True
        else:
            applied = requested

        new_qty = existing["quantity"] - applied
        updated = db.update_item(conn, existing["id"], quantity=new_qty)
        db.log_event(
            conn, item_id=updated["id"], barcode=req.barcode, item_name=updated["name"],
            event_type="scan_out", quantity_delta=-applied, quantity_after=new_qty,
            source="api", note=req.note,
        )
        return ScanResult(
            item=_row_to_item_out(updated),
            quantity_applied=applied,
            lookup_source="existing_item",
            clamped=clamped,
        )
    finally:
        conn.close()


@app.post("/items", response_model=ItemOut, status_code=201)
def create_item(req: ItemCreate) -> ItemOut:
    conn = db.get_conn()
    try:
        if req.barcode:
            dupe = db.get_item_by_barcode(conn, req.barcode)
            if dupe:
                raise HTTPException(status_code=409, detail="An item with this barcode already exists.")
        new_item = db.create_item(conn, **req.model_dump())
        db.log_event(
            conn, item_id=new_item["id"], barcode=req.barcode, item_name=new_item["name"],
            event_type="create", quantity_delta=req.quantity, quantity_after=req.quantity,
            source="api",
        )
        return _row_to_item_out(new_item)
    finally:
        conn.close()


@app.get("/items", response_model=list[ItemOut])
def list_items(
    category: Optional[str] = None,
    location: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = Query(default=100, le=500),
    offset: int = 0,
) -> list[ItemOut]:
    conn = db.get_conn()
    try:
        rows = db.list_items(conn, category=category, location=location, search=search,
                              limit=limit, offset=offset)
        return [_row_to_item_out(r) for r in rows]
    finally:
        conn.close()


@app.get("/items/expiring", response_model=list[ExpiringItemOut])
def items_expiring(days: int = 7) -> list[ExpiringItemOut]:
    conn = db.get_conn()
    try:
        rows = db.list_expiring(conn, days=days)
        today = date.today()
        out = []
        for r in rows:
            item = dict(r)
            expiry = datetime.strptime(item["expiry_date"], "%Y-%m-%d").date()
            item["days_until_expiry"] = (expiry - today).days
            out.append(ExpiringItemOut(**item))
        return out
    finally:
        conn.close()


@app.get("/items/{item_id}", response_model=ItemOut)
def get_item(item_id: int) -> ItemOut:
    conn = db.get_conn()
    try:
        row = db.get_item(conn, item_id)
        if not row:
            raise HTTPException(status_code=404, detail="Item not found.")
        return _row_to_item_out(row)
    finally:
        conn.close()


@app.patch("/items/{item_id}", response_model=ItemOut)
def patch_item(item_id: int, req: ItemUpdate) -> ItemOut:
    conn = db.get_conn()
    try:
        existing = db.get_item(conn, item_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Item not found.")

        fields = {k: v for k, v in req.model_dump().items() if v is not None}
        if not fields:
            return _row_to_item_out(existing)

        if "barcode" in fields and fields["barcode"]:
            dupe = db.get_item_by_barcode(conn, fields["barcode"])
            if dupe and dupe["id"] != item_id:
                raise HTTPException(status_code=409, detail="Another item already uses this barcode.")

        updated = db.update_item(conn, item_id, **fields)

        if "quantity" in fields:
            delta = fields["quantity"] - existing["quantity"]
            db.log_event(
                conn, item_id=item_id, barcode=updated["barcode"], item_name=updated["name"],
                event_type="manual_adjust", quantity_delta=delta, quantity_after=fields["quantity"],
                source="api",
            )
        else:
            db.log_event(
                conn, item_id=item_id, barcode=updated["barcode"], item_name=updated["name"],
                event_type="update", quantity_delta=0, quantity_after=updated["quantity"],
                source="api",
            )
        return _row_to_item_out(updated)
    finally:
        conn.close()


@app.delete("/items/{item_id}", status_code=204)
def delete_item(item_id: int) -> None:
    conn = db.get_conn()
    try:
        existing = db.get_item(conn, item_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Item not found.")
        db.log_event(
            conn, item_id=None, barcode=existing["barcode"], item_name=existing["name"],
            event_type="delete", quantity_delta=-existing["quantity"], quantity_after=0,
            source="api",
        )
        db.delete_item(conn, item_id)
    finally:
        conn.close()


@app.get("/barcode/{barcode}", response_model=BarcodeLookupResult)
def barcode_lookup(barcode: str, refresh: bool = False) -> BarcodeLookupResult:
    conn = db.get_conn()
    try:
        lookup = off_client.lookup_barcode(conn, barcode, refresh=refresh)
        return BarcodeLookupResult(
            barcode=barcode,
            found=lookup.found,
            name=lookup.name,
            brand=lookup.brand,
            category=lookup.category,
            source=lookup.source,
        )
    finally:
        conn.close()


@app.get("/shopping-list", response_model=list[ShoppingListEntry])
def shopping_list(location: Optional[str] = None) -> list[ShoppingListEntry]:
    conn = db.get_conn()
    try:
        rows = db.list_low_stock(conn, location=location)
        return [
            ShoppingListEntry(
                item_id=r["id"], barcode=r["barcode"], name=r["name"], brand=r["brand"],
                category=r["category"], quantity=r["quantity"], unit=r["unit"],
                par_level=r["par_level"], deficit=r["deficit"], location=r["location"],
            )
            for r in rows
        ]
    finally:
        conn.close()


DASHBOARD_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Inventory</title>
<style>
  body { font-family: sans-serif; background:#111; color:#eee; margin:0; padding:16px; font-size:20px; }
  h2 { margin:0 0 8px; }
  table { width:100%; border-collapse:collapse; margin-bottom:24px; }
  th, td { text-align:left; padding:8px 6px; border-bottom:1px solid #333; }
  th { color:#999; font-weight:normal; font-size:16px; }
  .low { color:#ff6b6b; font-weight:bold; }
  .empty { color:#888; padding:12px 0; }
  input, select { background:#222; color:#eee; border:1px solid #444; border-radius:4px; padding:6px 8px; font-size:16px; }
  button { background:#2a7a4a; color:#fff; border:none; border-radius:4px; padding:8px 14px; font-size:15px; cursor:pointer; margin-right:6px; }
  button.secondary { background:#444; }
  button.danger { background:#a33; }
  .row-actions { white-space:nowrap; }
  .edit-cell input { width:100%; box-sizing:border-box; }
  #add-form { display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-bottom:24px; }
  #add-form input { width:140px; }
  #controls { display:flex; flex-wrap:wrap; gap:16px; margin-bottom:24px; }
  #controls label { display:flex; flex-direction:column; gap:4px; font-size:14px; color:#999; }
  #controls select { font-size:16px; }
  a.trip-link {
    display:inline-block; background:#0071ce; color:#fff; text-decoration:none;
    padding:8px 16px; border-radius:6px; font-size:15px; margin-bottom:16px;
  }
  a.trip-link:hover { background:#004f9a; }
</style>
</head>
<body>
  <a class='trip-link' href='shopping-trip'>Shopping Trip View &rarr;</a>
  <div id='controls'>
    <label>Scan Mode
      <select id='scan-mode-select' onchange='updateSetting("scan-mode", this.value)'></select>
    </label>
    <label>Scan Speakers
      <select id='scan-speakers-select' onchange='updateSetting("scan-speakers", this.value)'></select>
    </label>
  </div>

  <h2>Shopping List</h2>
  <table id='shopping'><thead><tr><th>Item</th><th>Qty</th><th>Par</th></tr></thead>
    <tbody><tr><td colspan='3' class='empty'>Loading&hellip;</td></tr></tbody></table>

  <h2>All Items</h2>
  <table id='items'><thead><tr><th>Item</th><th>Brand</th><th>Qty</th><th>Par</th><th>Location</th><th>Actions</th></tr></thead>
    <tbody><tr><td colspan='6' class='empty'>Loading&hellip;</td></tr></tbody></table>

  <h2>Add Item</h2>
  <form id='add-form' onsubmit='addItem(event)'>
    <input name='name' placeholder='Name' required>
    <input name='category' placeholder='Category'>
    <input name='quantity' type='number' step='any' placeholder='Qty' value='0'>
    <input name='unit' placeholder='Unit' value='each'>
    <input name='par_level' type='number' step='any' placeholder='Par level' value='0'>
    <input name='location' placeholder='Location'>
    <button type='submit'>Add</button>
  </form>

<script>
let itemsCache = [];
let editingId = null;

const SETTINGS = {
  'scan-mode':     { selectId: 'scan-mode-select',     options: ['Scan In', 'Scan Out'] },
  'scan-speakers': { selectId: 'scan-speakers-select', options: ['Both', 'Living Room Only', 'Bedroom Only', 'Headset Only', 'None'] },
};

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

async function loadSettings() {
  for (const [key, cfg] of Object.entries(SETTINGS)) {
    const select = document.getElementById(cfg.selectId);
    select.innerHTML = cfg.options.map(o => `<option value='${o}'>${o}</option>`).join('');
    try {
      const r = await fetch(`settings/${key}`, { cache: 'no-store' });
      if (!r.ok) throw new Error('load failed');
      const data = await r.json();
      select.value = data.value;
    } catch (e) {
      console.error(`Could not load setting ${key}`, e);
    }
  }
}

async function updateSetting(key, value) {
  try {
    const r = await fetch(`settings/${key}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ value }),
    });
    if (!r.ok) throw new Error('save failed');
  } catch (e) {
    alert('Could not update setting -- check that Home Assistant is reachable.');
    console.error(e);
    loadSettings();
  }
}

function renderShopping(shop) {
  const shopBody = document.querySelector('#shopping tbody');
  shopBody.innerHTML = shop.length
    ? shop.map(i => `<tr><td>${escapeHtml(i.name)}</td><td class='low'>${i.quantity}</td><td>${i.par_level}</td></tr>`).join('')
    : `<tr><td colspan='3' class='empty'>Nothing low</td></tr>`;
}

function renderItemRow(i) {
  return `<tr data-id='${i.id}'>
    <td>${escapeHtml(i.name)}</td>
    <td>${escapeHtml(i.brand || '')}</td>
    <td>${i.quantity}</td>
    <td>${i.par_level}</td>
    <td>${escapeHtml(i.location || '')}</td>
    <td class='row-actions'>
      <button class='secondary' onclick='editRow(${i.id})'>Edit</button>
      <button class='danger' onclick='deleteRow(${i.id})'>Delete</button>
    </td>
  </tr>`;
}

function renderItems(items) {
  itemsCache = items;
  if (editingId !== null) return;
  const itemsBody = document.querySelector('#items tbody');
  itemsBody.innerHTML = items.length
    ? items.map(i => renderItemRow(i)).join('')
    : `<tr><td colspan='6' class='empty'>No items yet</td></tr>`;
}

function editRow(id) {
  editingId = id;
  const item = itemsCache.find(i => i.id === id);
  if (!item) return;
  const tr = document.querySelector(`tr[data-id='${id}']`);
  tr.innerHTML = `
    <td class='edit-cell'><input id='e-name-${id}' value='${escapeHtml(item.name)}'></td>
    <td class='edit-cell'><input id='e-brand-${id}' value='${escapeHtml(item.brand || '')}'></td>
    <td class='edit-cell'><input id='e-qty-${id}' type='number' step='any' value='${item.quantity}'></td>
    <td class='edit-cell'><input id='e-par-${id}' type='number' step='any' value='${item.par_level}'></td>
    <td class='edit-cell'><input id='e-loc-${id}' value='${escapeHtml(item.location || '')}'></td>
    <td class='row-actions'>
      <button onclick='saveRow(${id})'>Save</button>
      <button class='secondary' onclick='cancelEdit()'>Cancel</button>
    </td>`;
}

function cancelEdit() {
  editingId = null;
  renderItems(itemsCache);
}

async function saveRow(id) {
  const name = document.getElementById(`e-name-${id}`).value.trim();
  const brand = document.getElementById(`e-brand-${id}`).value.trim();
  const quantity = parseFloat(document.getElementById(`e-qty-${id}`).value);
  const par_level = parseFloat(document.getElementById(`e-par-${id}`).value);
  const location = document.getElementById(`e-loc-${id}`).value.trim();
  try {
    const r = await fetch(`items/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name, brand, quantity, par_level, location }),
    });
    if (!r.ok) throw new Error('save failed');
    editingId = null;
    await refresh();
  } catch (e) {
    alert('Could not save changes.');
    console.error(e);
  }
}

async function deleteRow(id) {
  const item = itemsCache.find(i => i.id === id);
  if (!confirm(`Delete "${item ? item.name : 'this item'}"?`)) return;
  try {
    const r = await fetch(`items/${id}`, { method: 'DELETE' });
    if (!r.ok && r.status !== 204) throw new Error('delete failed');
    await refresh();
  } catch (e) {
    alert('Could not delete item.');
    console.error(e);
  }
}

async function addItem(event) {
  event.preventDefault();
  const form = event.target;
  const body = {
    name: form.name.value.trim(),
    category: form.category.value.trim() || null,
    quantity: parseFloat(form.quantity.value) || 0,
    unit: form.unit.value.trim() || 'each',
    par_level: parseFloat(form.par_level.value) || 0,
    location: form.location.value.trim() || null,
  };
  if (!body.name) return;
  try {
    const r = await fetch('items', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error('add failed');
    form.reset();
    await refresh();
  } catch (e) {
    alert('Could not add item.');
    console.error(e);
  }
}

async function refresh() {
  try {
    const [items, shop] = await Promise.all([
      fetch('items', { cache: 'no-store' }).then(r => r.json()),
      fetch('shopping-list', { cache: 'no-store' }).then(r => r.json()),
    ]);
    renderShopping(shop);
    renderItems(items);
  } catch (e) {
    console.error('refresh failed', e);
  }
}
refresh();
loadSettings();
setInterval(() => { refresh(); loadSettings(); }, 5000);
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') { refresh(); loadSettings(); }
});
</script>
</body>
</html>"""


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    return DASHBOARD_HTML


SHOPPING_TRIP_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Shopping Trip</title>
<style>
  body { font-family: sans-serif; background:#111; color:#eee; margin:0; padding:16px; font-size:20px; }
  h1 { margin:0 0 4px; font-size:26px; }
  .subtitle { color:#999; font-size:15px; margin-bottom:20px; }
  a.back { color:#8ab4ff; text-decoration:none; font-size:15px; }
  table { width:100%; border-collapse:collapse; margin-top:16px; }
  th, td { text-align:left; padding:12px 8px; border-bottom:1px solid #333; vertical-align:middle; }
  th { color:#999; font-weight:normal; font-size:15px; }
  .qty { font-weight:bold; color:#ff6b6b; font-size:22px; }
  .location { color:#999; font-size:14px; }
  .empty { color:#888; padding:24px 0; text-align:center; font-size:18px; }
  button.walmart-link {
    display:inline-block; background:#0071ce; color:#fff; text-decoration:none;
    padding:10px 18px; border-radius:6px; font-size:16px; white-space:nowrap;
    border:none; cursor:pointer; font-family:inherit;
  }
  button.walmart-link:hover { background:#004f9a; }
</style>
</head>
<body>
  <a class='back' href='dashboard'>&larr; Back to Inventory</a>
  <h1>Shopping Trip</h1>
  <div class='subtitle'>Everything currently low or out of stock &mdash; tap an item to search for it on Walmart</div>
  <table id='trip'><thead><tr><th>Item</th><th>Have</th><th>Need</th><th></th></tr></thead>
    <tbody><tr><td colspan='4' class='empty'>Loading&hellip;</td></tr></tbody></table>

<script>
function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}

function walmartSearchUrl(item) {
  const query = item.brand ? `${item.brand} ${item.name}` : item.name;
  return 'https://www.walmart.com/search?q=' + encodeURIComponent(query);
}

function openWalmartSearch(url) {
  window.open(url, 'walmart_search');
}

async function loadTrip() {
  try {
    const shop = await fetch('shopping-list', { cache: 'no-store' }).then(r => r.json());
    const body = document.querySelector('#trip tbody');
    body.innerHTML = shop.length
      ? shop.map(i => `<tr>
          <td>
            ${escapeHtml(i.name)}${i.brand ? ' <span class="location">(' + escapeHtml(i.brand) + ')</span>' : ''}
            ${i.location ? `<div class='location'>${escapeHtml(i.location)}</div>` : ''}
          </td>
          <td>${i.quantity} ${escapeHtml(i.unit)}</td>
          <td class='qty'>${i.deficit}</td>
          <td><button class='walmart-link' onclick='openWalmartSearch("${walmartSearchUrl(i)}")'>Search Walmart</button></td>
        </tr>`).join('')
      : `<tr><td colspan='4' class='empty'>Nothing needed right now &mdash; you're all stocked up.</td></tr>`;
  } catch (e) {
    console.error('load failed', e);
  }
}
loadTrip();
setInterval(loadTrip, 30000);
</script>
</body>
</html>"""


@app.get("/shopping-trip", response_class=HTMLResponse)
async def shopping_trip():
    return SHOPPING_TRIP_HTML
