# BUSINESS_LOGIC.md — Workspace System v1

> **Forensic Audit Document**
> **Scope:** Every business rule, calculation, validation, threshold, and edge-case behavior found in the codebase.
> **Sources audited:** [app.py](app.py) (2,532 lines), [setup_database.py](setup_database.py), all Python helpers (`reset_data.py`, `import_data.py`, `clean_students.py`, `add_users.py`, `add_manual_discount_col.py`, `add_year_column.py`, `create_settings_table.py`), all 38 HTML templates under [templates/](templates/), and `requirements.txt`.
> **Stack:** Flask 3.1.3 + Werkzeug 3.1.6 + SQLite (`workspace.db`) + openpyxl/pandas + pure-JS templates (Arabic RTL UI).
> **Server runs on:** `127.0.0.1:5009` ([app.py:2531](app.py#L2531)). Browser auto-opens via `Timer(3, open_browser)` at [app.py:2527](app.py#L2527).

---

## 0. SYSTEM TOPOLOGY

### 0.1 Roles
Two roles enforced via `session['role']` ([setup_database.py:27](setup_database.py#L27) — DB CHECK constraint):
| Role | Capabilities |
|---|---|
| `manager` | All routes. Sees `manager_dashboard.html`. Can manage products/users/rooms/coupons/settings/subscription types, view financial reports, generate coupons, edit any sale, reset DB, edit subscription packages. |
| `employee` | Operational routes only: shifts, check-in/out, sales, expenses, library, reservations. Sees `employee_dashboard.html`. **Anomaly:** the `edit_student` route at [app.py:1553](app.py#L1553) checks `role != 'employee'` (i.e. it BLOCKS managers from editing students — likely a bug). |

### 0.2 The "Manager Phone"
Hardcoded constant: `MANAGER_PHONE = "201070671508"` at [app.py:43](app.py#L43). Used to generate WhatsApp report links when an employee ends a shift ([app.py:427](app.py#L427)).

### 0.3 Resource Path Resolution
[app.py:19-40](app.py#L19-L40) handles dual-mode runtime:
- **Frozen / EXE mode:** `BASE_DIR = os.path.dirname(sys.executable)` (DB lives next to the EXE — important for persistence across runs); `RESOURCES_DIR = sys._MEIPASS` (templates bundled inside the EXE).
- **Dev mode:** both paths = the script directory.
- **DB filename:** always `workspace.db` next to executable.

### 0.4 Python Dependencies
[requirements.txt](requirements.txt): `Flask==3.1.3`, `pandas==3.0.1`, `Werkzeug==3.1.6`, `openpyxl` (no version pin).

### 0.5 Session Secret
`app.secret_key = "super_secret_key_fixed"` at [app.py:42](app.py#L42) — **hardcoded** (security note: rotate before public deployment).

---

## 1. THE "HIDDEN" MATH — EVERY CALCULATION IN THE CODE

### 1.1 Core Pricing Function: `calculate_dynamic_cost(hours, room_name, hourly_rate)` — [app.py:55-107](app.py#L55-L107)

This is the **single source of truth** for time-cost. It branches on room name:

#### 1.1.1 Private / Meeting Rooms — Hourly with Half-Hour Rounding UP
```python
if 'Private' in room_name or 'Meeting' in room_name:
    billed_hours = math.ceil(hours * 2) / 2     # round UP to nearest 0.5h
    if billed_hours < 1.0:
        billed_hours = 1.0                      # 1-hour minimum
    return billed_hours * hourly_rate
```
- **Rounding:** `ceil(hours × 2) ÷ 2` → next half-hour UP. (e.g. 1.05 h → 1.5 h; 0.1 h → 0.5 h then upgraded to 1.0 h.)
- **Minimum charge:** 1 hour even if visit is 0 minutes long.
- **Rate source:** `Rooms.hourly_rate` per room ([setup_database.py:48](setup_database.py#L48)). Default seed `10.0` for Private/Meeting; manager can edit via [edit_room/<room_id>](app.py#L1786).

#### 1.1.2 Shared / Silent Rooms — 7-Tier Bracket Pricing (NOT hourly)
The duration is matched against ascending bracket caps; whichever bracket the duration falls into yields a **flat** price. Brackets are configurable in `Settings` table; defaults defined inline:

| Tier key (Settings table) | Default price (EGP) | Cap (hours) | Stored in `TIERS` list |
|---|---:|---:|---|
| `tier_price_1h` | 10 | ≤ 1.05 h | [app.py:94](app.py#L94) |
| `tier_price_3h` | 25 | ≤ 3.05 h | [app.py:95](app.py#L95) |
| `tier_price_6h` | 35 | ≤ 6.05 h | [app.py:96](app.py#L96) |
| `tier_price_9h` | 40 | ≤ 9.05 h | [app.py:97](app.py#L97) |
| `tier_price_12h` | 50 | ≤ 12.05 h | [app.py:98](app.py#L98) |
| `tier_price_16h` | 60 | ≤ 16.05 h | [app.py:99](app.py#L99) |
| `tier_price_24h` | 70 | > 16.05 h (catch-all "full day") | [app.py:107](app.py#L107) |

> **Hidden tolerance:** the cap for each tier has **+0.05 hours (3 minutes) grace** to avoid penalising a student who is 1–2 minutes over.
> Algorithm — [app.py:102-107](app.py#L102-L107):
> ```python
> for limit, key in TIERS:
>     if hours <= limit:
>         return float(tier_prices[key])
> return float(tier_prices['tier_price_24h'])  # > 16.05h → full-day rate
> ```

> **Important difference vs Private/Meeting:** Shared/Silent does **NOT** multiply by `hourly_rate` from the `Rooms` table — the bracket price is the total. The `hourly_rate` column for these rooms is **ignored** by the cost calculation; it is only displayed cosmetically in the room dropdown ([select_room.html:23](templates/select_room.html)).

> **Tier price persistence:** The `Settings` table is read on every cost call ([app.py:81-90](app.py#L81-L90)) — settings changes apply instantly, no cache.

### 1.2 Manual Discount Calculation — [app.py:651-661](app.py#L651-L661) and [app.py:789-816](app.py#L789-L816)

Two flavours selectable on the invoice page (`apply_manual_discount` route at [app.py:1540](app.py#L1540)):
```python
if manual_disc_type == 'percentage':
    manual_discount_amount = time_cost * (manual_disc_value / 100.0)
else:  # fixed
    manual_discount_amount = manual_disc_value
manual_discount_amount = min(manual_discount_amount, time_cost)  # cap
```
- **Cap:** A discount can never exceed the `time_cost` itself (no negative totals).
- **Scope:** Discount applies **only** to time-cost, not to cafeteria sales — sales must always be paid in full ([app.py:649](app.py#L649) explicit comment: *"الخصومات كلها بتطبق على تكلفة الوقت (Time Cost) فقط"*).
- **Storage column:** `Visits.manual_discount` (REAL, default 0) — added retroactively via [add_manual_discount_col.py](add_manual_discount_col.py) since it's not in `setup_database.py`.

### 1.3 Coupon Discount Calculation — [app.py:683-688](app.py#L683-L688) and [app.py:802-812](app.py#L802-L812)

```python
if cp['discount_type'] == 'percentage':
    disc_val = time_cost * (cp['discount_value'] / 100.0)
else:  # 'fixed'
    disc_val = cp['discount_value']
discount_info = {"code": cp['code'], "amount": min(disc_val, time_cost)}
```
- Same min-cap-vs-time_cost logic as manual discount.
- Coupon and reservation discounts are **mutually exclusive**: reservation discount has priority; coupon is only checked if `discount_info['amount'] == 0` ([app.py:678](app.py#L678)).

### 1.4 Reservation (Pre-Booking) Discount — [app.py:666-675](app.py#L666-L675), [app.py:790-799](app.py#L790-L799)

```python
if res_data['discount_type'] == 'percentage':
    disc_val = time_cost * (res_data['discount_value'] / 100.0)
else:  # 'fixed' (any value other than 'none')
    disc_val = res_data['discount_value']
discount_info = {"code": "عرض حجز", "amount": min(disc_val, time_cost)}
```
- The `discount_type='none'` sentinel skips the discount entirely.
- Display label is hard-coded `"عرض حجز"` ("Reservation Offer").

### 1.5 Final Invoice Total — [app.py:693-697](app.py#L693-L697)

```python
total_discount  = discount_info['amount'] + manual_discount_amount   # coupon/reservation + manual stack
grand_total_before = time_cost + sales_total
final_total     = max(0, grand_total_before - total_discount)        # never negative
```
- **Stacking:** Manual discount is **additive** with coupon/reservation discount (line 694).
- The `confirm_payment` route ([app.py:741](app.py#L741)) recomputes everything server-side at checkout — the front-end values are advisory.

### 1.6 Visit Duration Computation — [app.py:612-614](app.py#L612-L614), [app.py:765-767](app.py#L765-L767)

```python
check_in = datetime.datetime.strptime(visit_data['check_in_time'], "%Y-%m-%d %H:%M:%S")
now      = datetime.datetime.now()
duration_hours = (now - check_in).total_seconds() / 3600.0
```
- Pure float hours (no rounding here — rounding happens inside `calculate_dynamic_cost`).
- Stored to `Visits.duration_hours` only at checkout in `confirm_payment`.

### 1.7 Subscription Hour Deduction — [app.py:775-786](app.py#L775-L786)

```python
if payment_method == 'subscription':
    sub = ... # fetch active subscription
    if sub and sub['remaining_hours'] >= duration:
        new_bal = sub['remaining_hours'] - duration
        UPDATE StudentSubscriptions SET remaining_hours = new_bal
        final_time_cost = 0.0       # zeroed out — does not enter cash drawer
    else:
        flash("رصيد الباقة لا يكفي! تم التحويل للكاش.", "error")
        payment_method = 'cash'     # auto-fallback
```
- **Deduction precision:** Float hours subtracted exactly (e.g. 0.123456 hrs).
- **Sales not covered:** Only time is paid by subscription; cafeteria sales remain payable in cash.
- **Auto-fallback to cash** if balance insufficient — silently flips method.
- **No balance partial-use:** If duration > remaining_hours by any amount → entire visit becomes cash (subscription is NOT partially debited).

### 1.8 Shift Net Cash — Two Different Formulas in Codebase

#### 1.8.1 The "official" formula in `end_shift` — [app.py:402](app.py#L402)
```python
net_cash = (rev_hours + rev_sales) - expenses - discounts
```
Here `rev_hours = rev_shared + rev_private` and `rev_sales` includes ALL sales (cafeteria + packages, see qualification below).

#### 1.8.2 The "preview" formula in `shift_summary_preview` — [app.py:909](app.py#L909)
```python
net_cash = (rev_hours_total + cafeteria_sales + package_sales) - expenses - total_discounts
```
- Splits sales into `cafeteria_sales` and `package_sales` for display, but math is equivalent (their sum equals `rev_sales`).

#### 1.8.3 Cash drawer counted ONLY for closed visits paid in cash
- **Hours revenue** ([app.py:377-388](app.py#L377-L388) and [app.py:855-869](app.py#L855-L869)):
  ```sql
  SELECT V.total_cost, R.name FROM Visits V JOIN Rooms R...
  WHERE V.shift_id = ? AND V.payment_method = 'cash' AND V.check_out_time IS NOT NULL
  ```
  Subscription-paid visits are excluded (because `final_time_cost` was zeroed).
- **Sales revenue** ([app.py:391-396](app.py#L391-L396)):
  ```sql
  SELECT SUM(total_price) FROM Sales
  WHERE shift_id = ? AND (visit_id IS NULL OR visit_id IN (
      SELECT visit_id FROM Visits WHERE check_out_time IS NOT NULL
  ))
  ```
  Includes "cash sales" (no visit_id) AND sales tied to checked-out visits. **Excludes** sales tied to active visits — those stay "pending" until checkout.
- **Expenses** ([app.py:397](app.py#L397)): `SUM(amount) FROM Expenses WHERE shift_id = ?` — all expenses unconditionally.
- **Discounts** ([app.py:399-400](app.py#L399-L400)): `SUM(total_discount) FROM Visits WHERE shift_id = ? AND check_out_time IS NOT NULL` — only closed visits.

#### 1.8.4 Shared vs Private Split for Display — [app.py:382-388](app.py#L382-L388), [app.py:863-867](app.py#L863-L867)
```python
for v in visits:
    if 'Private' in v['room_name'] or 'Meeting' in v['room_name']:
        rev_private += v['total_cost']
    else:
        rev_shared += v['total_cost']
```
Hard-coded substring matching (`'Private' in name` or `'Meeting' in name`) — adding rooms with these strings in the name auto-classifies them as Private.

#### 1.8.5 Cafeteria vs Package Sales Split — [app.py:875-893](app.py#L875-L893)
```sql
-- Cafeteria
WHERE P.name NOT LIKE '%باقة%'
-- Package
WHERE P.name LIKE '%باقة%'
```
Classification is by the Arabic substring **"باقة"** in the product name — a subscription sold via `sell_subscription` is logged as a fake product with name `"اشتراك باقة"` ([app.py:1206-1220](app.py#L1206-L1220)) so it lands in `package_sales`.

### 1.9 Daily Financial Summary — [app.py:1866-1874](app.py#L1866-L1874)
```sql
SELECT SUM(revenue_hours+revenue_sales) AS total_rev,
       SUM(total_expenses)              AS total_exp,
       SUM(total_discounts)             AS total_disc,
       SUM(net_cash)                    AS net
FROM Shifts WHERE end_time IS NOT NULL AND DATE(start_time)=?
```
- **Important:** filters by `start_time` date — if a shift spans midnight, it counts entirely in its **start day**.
- **`net_profit`** in the report is just `SUM(net_cash)` of closed shifts ([app.py:1873](app.py#L1873)). The label is misleading — it's net cash drawer, not accounting profit (purchase costs are not subtracted anywhere).

### 1.10 Per-Visit Bill in Shift Details — [app.py:1907-1916](app.py#L1907-L1916)
```python
'grand_total': (v['total_cost'] + s_total) - v['total_discount']
```
- Combines time cost + cafeteria sales − total discount (manual + coupon already merged in `total_discount`).

### 1.11 Library Rental Math — [app.py:2440-2454](app.py#L2440-L2454)
```python
borrow_date = strptime(borrow_date)
return_date = datetime.now()
days = (return_date - borrow_date).days
if days < 1: days = 1                       # 1-day minimum
rental_cost     = days * rental_fee_per_day  # daily rate × days
deposit         = borrow.deposit_paid
amount_to_return = deposit - rental_cost     # what student gets back
```
- **Default daily fee:** `5.0 EGP` ([setup_database.py:231](setup_database.py#L231) and [app.py:2376](app.py#L2376)).
- **`days` uses `.days` attribute** — fractional time-deltas are floored, then `<1` is forced to 1. So a same-day return = 1 day rental.
- **Deposit equals book.price** ([app.py:2408](app.py#L2408)).
- **No protection against negative `amount_to_return`** — if rental exceeds deposit, the student would owe money but the code only books a refund expense if `amount_to_return > 0` ([app.py:2465](app.py#L2465)). The deficit is silently absorbed.

### 1.12 Internet Card Tiered Pricing — [app.py:1011-1015](app.py#L1011-L1015), [app.py:1054-1066](app.py#L1054-L1066), [app.py:1660-1684](app.py#L1660-L1684)

Identifier: any product whose `name` contains the substring `"كارت نت"` ([app.py:1012](app.py#L1012)).

```python
FREE_LIMIT = int(setting_row['setting_value']) if setting_row else 2
# ...
if visit_id != 0 and INTERNET_CARD_NAME in p['name']:
    prev_qty = SUM(quantity) FROM Sales WHERE visit_id=? AND product_id=?
    for i in range(1, qty + 1):
        if (prev_qty + i) <= FREE_LIMIT:
            line_total += 0          # FREE
        else:
            line_total += unit_price # billed
```
- **Default free limit:** 2 cards/visit. Configurable via `manage_settings` (key: `internet_free_limit`, [create_settings_table.py:17](create_settings_table.py#L17)).
- **Per-visit, cumulative:** counts ALL cards previously sold to this visit, so giving 1 free + 1 free + 1 paid is identical to giving 3 cards in one go.
- **Cash sales (`visit_id == 0`/None):** the free-limit logic is **bypassed** — every card is billed at `sale_price`. Free cards only apply when tied to a student visit.
- **Edit edge case:** `edit_sale_quantity` ([app.py:1660-1684](app.py#L1660-L1684)) hardcodes the free limit to **`2`** literally (line 1678 — `if (prev_qty + i) <= 2:`), ignoring the manager-configurable `internet_free_limit` setting. **This is a bug-or-trap to know about.**

### 1.13 Sale Quantity Edit Recalc — [app.py:1646-1696](app.py#L1646-L1696)

When a sale's quantity changes:
```python
quantity_diff   = new_quantity - old_quantity
# Stock direction is INVERTED: if you increased a sale by N, stock decreases by N
UPDATE Products SET stock_quantity = stock_quantity - quantity_diff
# For Internet Cards: rebuild price piece-by-piece (see 1.12)
# For ordinary products:
new_total_price = new_quantity * sale_price
# Adjust shift revenue
price_diff = new_total_price - sale.total_price
UPDATE Shifts SET revenue_sales = revenue_sales + price_diff
```
- **Stock guard:** if `quantity_diff > 0` and current stock < quantity_diff, edit is rejected ([app.py:1649](app.py#L1649)).
- **Permission guard:** only allowed if `is_manager` OR `is_visit_open` (active visit) — [app.py:1644](app.py#L1644). Closed-bill edits require manager.
- **Live shift revenue update**: `revenue_sales` column on `Shifts` is mutated directly by the price diff. **Note:** this column is also overwritten on shift close ([app.py:407](app.py#L407)), so this only matters for an in-progress shift.

### 1.14 Sale Item Delete — [app.py:1737-1741](app.py#L1737-L1741)
```python
UPDATE Products SET stock_quantity = stock_quantity + sale.quantity
DELETE FROM Sales WHERE sale_id = ?
```
- Stock returns. Same permission gate (manager or visit open).
- **Does NOT** adjust `Shifts.revenue_sales` (this is asymmetric vs `edit_sale_quantity` and may leave shifts inconsistent if deleted between recompute and close).

---

## 2. PRICING ARCHITECTURE — ROOM TYPES, RATES, ROUNDING

### 2.1 Seeded Rooms ([setup_database.py:52](setup_database.py#L52))
| Name | Default `hourly_rate` | Pricing model |
|---|---:|---|
| `Shared` | 10.0 | **Tiered brackets** (rate ignored) |
| `Silent` | 10.0 | **Tiered brackets** (rate ignored) |
| `Private` | 10.0 | **Hourly × half-hour ceiling, 1-h min** |

### 2.2 Implicit `Meeting` Convention
Any room whose name contains `"Meeting"` falls into the Private branch ([app.py:59](app.py#L59), [app.py:385](app.py#L385), [app.py:864](app.py#L864)). Manager creates such rooms manually via `manage_rooms` form — there is no seed.

### 2.3 Room CRUD
- **Create:** [manage_rooms](app.py#L1772) `POST` — name + hourly_rate (REAL, `min=0`, `step=0.1` in HTML).
- **Edit:** [edit_room/<room_id>](app.py#L1786) — `hourly_rate` only. **Name cannot be changed** through the UI.
- **Delete:** no UI route exists.
- **Uniqueness:** `Rooms.name UNIQUE` constraint ([setup_database.py:47](setup_database.py#L47)).

### 2.4 Special: `'Private'` Excluded from `switch_room`
[app.py:2262](app.py#L2262):
```sql
SELECT name, room_id FROM Rooms WHERE name != 'Private'
```
A student cannot be **transferred into** Private via the switch-room flow. They can only be transferred between Shared / Silent / custom rooms. (Likely because Private requires a reservation.)

### 2.5 Tier Editor (Manager → Settings) — [manage_settings.html](templates/manage_settings.html)
The 7 tier prices and the internet free-limit live in the `Settings` table (key/value text store). REPLACE INTO is used for upserts ([app.py:1262](app.py#L1262), [app.py:1268](app.py#L1268)).

### 2.6 Manual Check-in Time Override
Both `register_student` ([app.py:443](app.py#L443)) and `check_in` ([app.py:491](app.py#L491)) accept an optional `manual_time` (format `HH:MM`):
- Validated with `datetime.strptime(manual_time, "%H:%M")` ([app.py:560](app.py#L560)).
- Concatenated with **today's date** at the server: `f"{today} {manual_time}:00"`.
- Falls back to `datetime.now()` if string is empty, the literal `"None"`, or fails parsing.
- HTML guidance ([check_in.html:73-75](templates/check_in.html)): *"⚠️ لو الجهاز كان فاصل، حدد وقت دخول الطالب الفعلي."* (For when the device was off and the operator is reconciling.)

---

## 3. INVENTORY & ADD-ONS

### 3.1 Default Cafeteria Products (seeded if `Products` empty — [setup_database.py:69-77](setup_database.py#L69-L77))
| Name | Purchase | Sale | Initial stock |
|---|---:|---:|---:|
| شاي (Tea) | 2.0 | 5.0 | 1000 |
| قهوة (Coffee) | 5.0 | 10.0 | 1000 |
| اندومي (Indomie) | 7.0 | 10.0 | 50 |
| نسكافيه (Nescafé) | 8.0 | 20.0 | 1000 |
| مياه (Water) | 3.0 | 10.0 | 50 |

### 3.2 Stock Mutation Rules
| Operation | Stock change | Code |
|---|---|---|
| Sale recorded | `stock_quantity -= qty` | [app.py:1076](app.py#L1076) |
| Sale deleted | `stock_quantity += qty` | [app.py:1737](app.py#L1737) |
| Sale qty edited | `stock_quantity -= (new − old)` | [app.py:1653](app.py#L1653) |
| `log_expense` with `stock_add_<id>` field filled | `stock_quantity += qty_added` | [app.py:1129](app.py#L1129) |
| Reset DB (manager) | All stock returns to seeded defaults via re-import | [app.py:2502](app.py#L2502) |

### 3.3 Stock Display Coding ([manage_products.html:64-67](templates/manage_products.html))
- ≤ 0 → red ("نفد")
- ≤ 5 → orange ("منخفض")
- > 5 → green ("متاح")

### 3.4 Cafeteria Sale Routing ([app.py:1017-1086](app.py#L1017-L1086))
- Sale can be tied to a visit (`visit_id` ≠ 0) → counts in that student's invoice.
- Or cash-only (`visit_id == 0`) → `Sales.visit_id = NULL` — counts in shift sales but no specific student.
- **Defensive shift creation:** if no `active_shift_id` in session but employee is logged in, the route auto-opens a shift to avoid losing the sale ([app.py:1024-1039](app.py#L1024-L1039)).

### 3.5 Internet Card Rules — see §1.12
Identification: any product `name LIKE '%كارت نت%'`. Free limit: `Settings.internet_free_limit` (default 2). Behaviour scope: per-visit cumulative only; cash sales bill all cards.

### 3.6 The Phantom "اشتراك باقة" Product
Created on-the-fly when a subscription is sold ([app.py:1206-1215](app.py#L1206-L1215)) if it doesn't exist:
```python
INSERT INTO Products (name, purchase_price, sale_price, stock_quantity)
VALUES ('اشتراك باقة', 0.0, 0.0, 0)
```
Used as a vehicle to log the subscription sale into the `Sales` table so it shows up in shift package_sales.

### 3.7 Product Deletion ([app.py:1148-1162](app.py#L1148-L1162))
- Manager-only.
- Wraps DELETE in try/except: if FK constraint fails (linked to historic Sales), shows: *"لا يمكن حذف المنتج لأنه مرتبط بعمليات بيع سابقة"*.

---

## 4. MEMBER & SUBSCRIPTION LIFECYCLE

### 4.1 Seeded Membership Types ([setup_database.py:197-201](setup_database.py#L197-L201))
| Name | Hours | Validity (days) | Price (EGP) |
|---|---:|---:|---:|
| باقة المحترفين | 200 | 30 | 600 |
| باقة المتقدمين | 100 | 20 | 450 |
| باقة المبتدئين | 50 | 10 | 300 |

### 4.2 Sale Flow ([app.py:1164-1233](app.py#L1164-L1233))
On `confirm_sub` POST:
```python
start = datetime.now()
end   = start + timedelta(days=pkg['days_valid'])
INSERT INTO StudentSubscriptions (student_id, type_id, start_date, end_date, remaining_hours)
VALUES (sid, tid, start, end, pkg['total_hours'])
```
- **Active flag** defaults to 1 (`is_active INTEGER DEFAULT 1` — [setup_database.py:217](setup_database.py#L217)).
- **No deactivation logic** when end_date passes — expiry is enforced query-time:
  ```sql
  WHERE student_id=? AND is_active=1 AND remaining_hours >= ? AND end_date >= ?
  ```
  ([app.py:631-633](app.py#L631-L633)).
- A subscription is "expired" by date but `is_active` remains 1 forever in DB. The query just filters it out.

### 4.3 Hour Deduction on Visit Close — see §1.7
- All-or-nothing: if `remaining_hours < duration`, the subscription is **not used at all** and the visit converts to cash.
- Successful deduction zeroes `final_time_cost` so it doesn't enter the cash drawer.
- Cafeteria sales remain billable in cash even when subscription is used.

### 4.4 Multiple Active Subscriptions
The fetch in `confirm_payment` ([app.py:776](app.py#L776)) does `SELECT * ... LIMIT 1` (implicitly via fetchone()). If a student has multiple actives, the **first one returned** (no ORDER BY) is used. This is a non-deterministic edge case.

### 4.5 Subscription Listing — `subscriptions_list` [app.py:2029](app.py#L2029)
- Shown to BOTH manager and employee (intentional — comment says *"عشان الموظف كمان يشوفها"*).
- `WHERE SS.is_active = 1` — does NOT filter by end_date, so date-expired subs still show until manually deactivated.
- `ORDER BY end_date DESC`.

### 4.6 Subscription Type CRUD
- **Create:** **No add route exists.** Manager can only edit existing 3 packages.
- **Edit:** [edit_subscription_type/<type_id>](app.py#L2194) — name, total_hours, days_valid, price.
- **Delete:** none.

### 4.7 What Happens to "Remaining Hours" — Summary
| Scenario | Effect |
|---|---|
| Used hour-by-hour | Decremented as float by exact `duration_hours` |
| Insufficient balance | Sub left untouched, visit pays cash |
| End date passes | Stays in DB, just filtered from "active" lookups |
| Student doesn't return | Hours are lost (no carryover, no refund) |
| `reset_data_keep_users` | All `StudentSubscriptions` rows wiped ([app.py:2502](app.py#L2502)) |

---

## 5. MARKETING & LOYALTY — COUPONS, PROMOS, "TOP STUDENT"

### 5.1 Coupon Generation — `generate_coupon/<student_id>` ([app.py:1801](app.py#L1801))
- **Manager-only.**
- **Suggested code generator:**
  ```python
  rnd = "GIFT-" + ''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(5))
  ```
  Cryptographically random 5-char alnum upper. The manager can override.
- Code is uppercased on insert ([app.py:1810](app.py#L1810)) and on lookup ([app.py:726](app.py#L726)).
- **Discount type:** `'percentage'` or `'fixed'` (CHECK constraint — [setup_database.py:100](setup_database.py#L100)).
- **Expiry date:** optional `DATE` in `Coupons.expiry_date`. Stored format `'YYYY-MM-DD'`.
- **Per-student binding:** `Coupons.student_id` FK — each coupon is tied to a single student at creation, but the redeem flow does **not** verify the coupon's `student_id` matches the current visit's student. Effectively coupons are **bearer tokens** despite the binding.

### 5.2 Coupon Validation — `apply_coupon` ([app.py:723](app.py#L723))
Checks in order:
1. Code exists in `Coupons` table.
2. `is_used == 0`.
3. `expiry_date IS NULL OR expiry_date >= today` (compared as date objects).

Errors are surfaced via flash + `error_message` URL param.

### 5.3 Coupon Consumption — `confirm_payment` ([app.py:813](app.py#L813))
```sql
UPDATE Coupons SET is_used=1, visit_id=? WHERE coupon_id=?
```
- Coupon becomes single-use.
- The `Coupons.visit_id` column is referenced but **not declared** in `setup_database.py` (only `coupon_id` FK on `Visits` exists). This UPDATE will silently fail or ALTER may have been applied externally — **schema-vs-code mismatch noted**.

### 5.4 Coupon Listing — `manage_coupons` ([app.py:1836](app.py#L1836))
- Manager-only.
- Joins to Students for display.
- `is_expired` computed on the fly per-row. Sorted by `is_used` (active first).
- Row colors: gray = expired, red = used, default = active.

### 5.5 Coupon WhatsApp Push — [app.py:1818-1819](app.py#L1818-L1819)
Generates a wa.me link with text:
```
أهلا {student.name}، ليك كوبون خصم {value}{ % | ج} الكود: {code}
```
**Phone formatting**: hardcoded prefix `"20"` (Egypt) + `phone[1:]` — strips the leading 0 from local numbers. Breaks for any number not starting with 0.

### 5.6 "Top Student" Rewards
**There is no automated rewards system.** The closest features:
- `analytics_report` ([app.py:1921](app.py#L1921)) ranks top students by:
  - Visits (excluding `Meeting Client`)
  - Spending (visits.total_cost + sales.total_price)
  - Visits/spending for `Meeting Client` cohort separately
- The manager **manually** generates a coupon for any qualifying student via `generate_coupon/<id>`.
- Limit selectable: 5/10/20 ([analytics_report.html](templates/analytics_report.html)).
- Date range: defaults to first-of-month → today.
- `Meeting Client` is a magic college value used to segregate this cohort ([app.py:2317](app.py#L2317) — auto-set when a reservation is started for a non-existing student).

### 5.7 Win-Back / Absence Reports
Built into `analytics_report`:
- **Regular students absent ≥ 10 days** ([app.py:1992](app.py#L1992)): `WHERE college != 'Meeting Client' AND MAX(check_in_time) < (now − 10d)`.
- **Meeting Clients absent ≥ 20 days** ([app.py:2004](app.py#L2004)): same logic, `college = 'Meeting Client'`, threshold 20.
- Each row gets a pre-formatted WhatsApp link with hardcoded Arabic message templates ([analytics_report.html:209-335](templates/analytics_report.html)).

### 5.8 Reservation Coupon Channel
Reservations carry their own `discount_type` / `discount_value` ([setup_database.py:119-120](setup_database.py#L119-L120)). Effectively a "promo per booking" instead of a coupon code. See §1.4.

### 5.9 Reservation Reminder Push — Dashboard
[app.py:262-296](app.py#L262-L296): on each dashboard load, the system queries:
```sql
SELECT R.*, RM.name FROM Reservations R JOIN Rooms RM
WHERE R.status='confirmed' AND R.start_time > NOW AND R.start_time <= NOW + 1 hour
ORDER BY start_time LIMIT 1
```
Generates a WhatsApp link prefilled with a reminder template:
```
مرحباً أ/ {name} 👋، نود تذكيركم بموعد حجزكم في Workspace (قاعة {room}) اليوم الساعة {time}...
```
Phone format: `"20" + phone` — does **not** strip leading 0 (so different convention than coupon push).

---

## 6. ADMINISTRATIVE CONTROLS — EVERY MANAGER-CONFIGURABLE VARIABLE

### 6.1 `Settings` Table (key/value)
Created by [create_settings_table.py](create_settings_table.py); `REPLACE INTO` upserted via `manage_settings` form ([app.py:1262-1268](app.py#L1262-L1268)).

| Key | Default | Type | Affects |
|---|---|---|---|
| `internet_free_limit` | `2` | int | First N internet cards free per visit (§1.12) |
| `tier_price_1h` | `10` | float | Shared/Silent ≤ 1.05 h cost |
| `tier_price_3h` | `25` | float | Shared/Silent ≤ 3.05 h cost |
| `tier_price_6h` | `35` | float | Shared/Silent ≤ 6.05 h cost |
| `tier_price_9h` | `40` | float | Shared/Silent ≤ 9.05 h cost |
| `tier_price_12h` | `50` | float | Shared/Silent ≤ 12.05 h cost |
| `tier_price_16h` | `60` | float | Shared/Silent ≤ 16.05 h cost |
| `tier_price_24h` | `70` | float | Shared/Silent > 16.05 h (full-day) cost |

### 6.2 Manager-Editable Records
| Entity | Route | Editable fields |
|---|---|---|
| Rooms | `manage_rooms`, `edit_room` | `hourly_rate` only (name set at creation) |
| Products | `manage_products`, `edit_product` | name, purchase_price, sale_price, stock_quantity |
| Subscription Types | `edit_subscription_type` | name, total_hours, days_valid, price (no add/delete UI) |
| Users (employees) | `manage_users`, `delete_user` | username, password (hashed), role; cannot delete self |
| Coupons | `generate_coupon`, `manage_coupons` | code, type, value, expiry, student binding |
| Reservations | `reservations`, `cancel_reservation` | room, dates, discount, notes (cancel only — no edit) |
| Books | `add_book` | title, author, price (deposit) — `rental_fee_per_day` hardcoded `5.0` on add ([app.py:2376](app.py#L2376)) |

### 6.3 Manager-Only Destructive Action: Reset Data ([app.py:2482](app.py#L2482))
- **Wipes:** all tables EXCEPT `Users`, `sqlite_sequence`, `Students`, `Products`, `Rooms`.
- Then re-runs `setup_database.create_database()` to re-seed any missing rows.
- Resets `sqlite_sequence` for cleared tables → IDs restart from 1.
- Pops `active_shift_id` from session.
- **Toggle in HTML:** [manager_dashboard.html](templates/manager_dashboard.html) requires JS `confirm()` before POST.

### 6.4 Helper Scripts (Operator-Run, Not Routed)
| Script | Effect |
|---|---|
| [reset_data.py](reset_data.py) | Standalone CLI confirmation (`yes` to proceed). Wipes Visits/Sales/Expenses/Borrowings/Reservations/Coupons/StudentSubscriptions/Shifts/Students/Books. Resets Products stock to 0 (keeps definitions). |
| [clean_students.py](clean_students.py) | Wipes ALL Students rows + auto-increment counter. |
| [import_data.py](import_data.py) | Bulk-imports `students.xlsx` (5- or 4-column auto-detection). |
| [add_users.py](add_users.py) | Creates 3 hardcoded accounts: `admin/admin123` (manager), `emp_morning/emp123`, `emp_night/emp456`. |
| [add_year_column.py](add_year_column.py) | One-shot ALTER for `Students.year`. |
| [add_manual_discount_col.py](add_manual_discount_col.py) | One-shot ALTER for `Visits.manual_discount`. |
| [create_settings_table.py](create_settings_table.py) | Bootstraps `Settings` table + default `internet_free_limit=2`. |

### 6.5 Authentication
- Passwords: `werkzeug.security.generate_password_hash(password, method='pbkdf2:sha256')` — [app.py:1594](app.py#L1594), [add_users.py:10](add_users.py#L10).
- Login check: `check_password_hash` — [app.py:221](app.py#L221).
- No password reset, no rate-limiting, no lockout.
- No CSRF protection (Flask-WTF not in requirements).

---

## 7. EDGE CASE HANDLING

### 7.1 Duplicate Student Detection — `clean_duplicates` ([app.py:1435](app.py#L1435))
Two-pass algorithm wrapped in `BEGIN/COMMIT/ROLLBACK`:

#### Pass 1: Exact name+phone matches (lines 1478-1486)
- Group by `TRIM(name)` then by `phone.replace(' ', '')`.
- Keep the **lowest student_id** in each bucket; mark the rest for deletion.

#### Pass 2: Phone-Reversal Detection (lines 1488-1517) — **the "obscure" rule**
- For each (name → phone) pair, check if the **reversed string** of the phone exists as another record under the same name.
- If reversed phone exists:
  - If exactly one of the two starts with `'0'` → keep that one (the leading-zero version is "canonical Egyptian local"), delete the reversed one.
  - If both start with `'0'` or neither does → keep both (ambiguous).
- Pair-dedup safeguard: `if phone > reversed_phone: continue` to ensure each pair is examined once.
- **Self-palindrome guard:** `if reversed_phone == phone: continue` — palindromic numbers (very rare) are skipped.

Wrapped in try/except/rollback — if anything throws, the entire dedup is rolled back.

### 7.2 Excel Import Robustness — `import_students` route ([app.py:1317](app.py#L1317))
- File-extension whitelist: `.xlsx`, `.xls`.
- **Header validation:** required columns `{name, phone, college, year}` — case-insensitive lookup. Missing → flash error, no insert.
- **Field cleaning per row:**
  - Replace ` ` (NBSP) with space (name) or strip (phone).
  - Convert literal `'None'` string to empty.
  - Skip if both name AND phone are empty.
- **Validation errors:** name empty, phone empty → row skipped, error logged. First 5 errors shown in flash.
- **Duplicate check on import:** `WHERE TRIM(phone) = ?` — leading/trailing spaces ignored.
- **Counts returned:** inserted, skipped_dup, skipped_empty, error count.

### 7.3 Standalone Excel Import — `import_data.py`
- **No header required:** auto-detects whether row 1 is a header by looking for "الاسم" substring ([line 29](import_data.py#L29)).
- **Column count auto-detection:**
  - 5 cols (default): expects code, name, college, year, phone.
  - 4 cols (fallback): name, college, year, phone (panda-stripped code column).
  - <4: aborts.
- **Phone cleaning:** `clean_phone_number` — strips spaces/dashes; if a `.` is in the string (Excel scientific notation artifact) takes prefix-before-dot.
- **Skip rules:** name is digit + ≥5 cols → assumes mis-shifted, tries `col_name+1`. Phone shorter than 5 chars → skip. Name == 'nan' or contains 'الاسم' → skip.
- **Duplicate phones:** caught by `IntegrityError` (UNIQUE constraint), printed but doesn't abort.

### 7.4 Unclosed Visits
- Excluded from shift cash drawer (§1.8.3).
- Surfaced separately in `shift_summary_preview` as `still_inside_visits` ([app.py:933-979](app.py#L933-L979)) with a warning that they're **not** in net_cash.
- The shift can still be closed with active visits inside — they'll dangle (no auto-checkout).
- HTML confirmation popup ([shift_summary_preview.html:293](templates/shift_summary_preview.html)) is the only safeguard.
- A subsequent shift will inherit those open visits — when finally checked out, the visit's `shift_id` is updated to the **current** shift ([app.py:819-820](app.py#L819-L820)) so the revenue counts in the closing shift, not the opening one.

### 7.5 Auto Shift-Recovery
Several routes handle "no active_shift_id in session" defensively:
- `add_sale` ([app.py:1024-1039](app.py#L1024-L1039)) → looks up open shift in DB, if none, creates one on the spot.
- `confirm_payment` ([app.py:819-820](app.py#L819-L820)) → falls back to the visit's original `shift_id` if session has no active shift.
- `log_expense` ([app.py:1099-1101](app.py#L1099-L1101)) → flashes error and redirects to dashboard (does NOT auto-create).
- `borrow_book` ([app.py:2415-2418](app.py#L2415-L2418)) → silently skips revenue update if no shift.

### 7.6 Missing Data Defensive Patterns
- `or 0` everywhere on aggregate fetches (`SUM(...)` returning `None`):
  - [app.py:396](app.py#L396), [app.py:398](app.py#L398), [app.py:400](app.py#L400), [app.py:883](app.py#L883), [app.py:893](app.py#L893), [app.py:897](app.py#L897), [app.py:901](app.py#L901), [app.py:905](app.py#L905), [app.py:1058](app.py#L1058), [app.py:1674](app.py#L1674).
- `try/except sqlite3.OperationalError` around `SUM(manual_discount)` ([app.py:903-907](app.py#L903-L907)) — defensive for DBs that don't have the manual_discount column yet.
- Flash + redirect on `if not visit:` / `if not row:` ([app.py:609](app.py#L609), [app.py:759](app.py#L759), [app.py:1196](app.py#L1196), [app.py:2252](app.py#L2252)).

### 7.7 Database Rollbacks
Explicit `conn.rollback()` only in three places:
- `import_students` ([app.py:1422-1423](app.py#L1422-L1423)) — entire batch atomicity.
- `clean_duplicates` ([app.py:1529](app.py#L1529)) — entire dedup atomicity.
- Most other routes commit per-INSERT/UPDATE without explicit transactions, relying on SQLite's auto-commit.

### 7.8 "Status" State Machine — Reservations
| State | Set by | Effect |
|---|---|---|
| `confirmed` (default — [setup_database.py:121](setup_database.py#L121)) | `reservations` POST insert | Counted in conflict checks; eligible for `start_reservation_visit`. |
| `active` | `start_reservation_visit` ([app.py:2331](app.py#L2331)) | Visit underway; shows "الزيارة جارية" in UI. |
| `cancelled` | `cancel_reservation` ([app.py:2219](app.py#L2219)) | Excluded from conflict + reminder queries. |

### 7.9 "Status" State Machine — Borrowings
| State | Set by | Effect |
|---|---|---|
| `active` (default — [setup_database.py:246](setup_database.py#L246)) | `borrow_book` POST | Book unavailable; counts as outstanding. |
| `returned` | `return_book` ([app.py:2453](app.py#L2453)) | Final cost stamped, book becomes available. |

### 7.10 Reservation Conflict Algorithm — [app.py:2105-2113](app.py#L2105-L2113), [app.py:2138-2146](app.py#L2138-L2146)
Three overlap conditions OR'd:
```sql
WHERE room_id=? AND status='confirmed' AND (
    (start_time <= ? AND end_time >= ?) OR    -- existing wraps new start
    (start_time <= ? AND end_time >= ?) OR    -- existing wraps new end
    (start_time >= ? AND end_time <= ?)       -- new wraps existing
)
```
Note this **excludes** `status='active'` and `status='cancelled'` — so the moment a reservation flips to active, you could double-book it on paper.

### 7.11 Recurring Reservation Generation — [app.py:2077-2125](app.py#L2077-L2125)
- Day-of-week selection: list of integers `0..6` (Python ISO: Mon=0). HTML labels: السبت=5, الأحد=6, الاثنين=0, ... ([reservations.html:61-68](templates/reservations.html)).
- Loop: for each date from start to `recurring_end_date`, if `weekday() in recurring_days` → run conflict check and either insert or skip.
- **Aggregate result flash:** counts `inserted` and `skipped`. If all skipped → error flash.
- The recurring rows share identical client/phone/discount but get unique start/end timestamps.

### 7.12 Tier Tolerance Trick — `+0.05 hours`
[app.py:94-99](app.py#L94-L99): every tier cap is bumped by 0.05 h (3 min) so a customer who lingers for 1 h 2 min is not bumped to the next bracket. This is the **deliberate** rounding policy and should be preserved.

### 7.13 Phone Reversal in Coupon WA Push — see §5.5
The hardcoded `phone[1:]` slice silently breaks for phones not starting with `0` and may produce malformed wa.me links.

### 7.14 Subscription Sale Without Shift
[app.py:1190-1192](app.py#L1190-L1192): `sell_subscription` flashes `"لا يمكن بيع الباقة بدون وردية مفتوحة."` and redirects. **Does not** auto-create a shift (unlike `add_sale`).

### 7.15 Edit-Student Permission Bug
[app.py:1553](app.py#L1553):
```python
if session.get('role') != 'employee': return redirect(url_for('login'))
```
This **blocks managers** from the edit-student route — likely a typo (should be `!= 'manager'` or the inverse).

### 7.16 `edit_sale_quantity` Hardcoded Free-Card Limit
[app.py:1678](app.py#L1678) hardcodes `(prev_qty + i) <= 2:` instead of reading `Settings.internet_free_limit`. Editing a sale recomputes price using literal 2 — out of sync with `add_sale`.

### 7.17 Concurrent Shift Defence
[app.py:355](app.py#L355): `start_shift` blocks if any open shift exists for this user.
```sql
SELECT shift_id FROM Shifts WHERE user_id=? AND end_time IS NULL
```
Multi-shift per user is impossible. (Concurrent shifts across users on different machines hitting the same SQLite DB → file-locking is the only safety.)

### 7.18 Logout Clears Session Entirely
[app.py:235](app.py#L235): `session.clear()` — including `active_shift_id`. After re-login, the session reattaches via DB lookup ([app.py:305](app.py#L305)).

### 7.19 Shutdown Route ([app.py:124-185](app.py#L124-L185))
- **No auth check** — anyone can hit `/shutdown` and kill the process (intentional per comment line 125).
- Returns goodbye HTML, then `os._exit(0)` after a 1 s thread sleep — bypasses Flask's normal teardown.

### 7.20 Coupon `student_id` Mismatch
A coupon is generated for student A but the redeem flow doesn't enforce that the visit's student is A. Anyone with the code can use it ([app.py:802-812](app.py#L802-L812) — no `student_id` check).

### 7.21 `Coupons.visit_id` Schema Mismatch
[app.py:813](app.py#L813) does `UPDATE Coupons SET ... visit_id=?` but `setup_database.py` doesn't declare a `visit_id` column on `Coupons`. Either silently fails or was added externally.

---

## 8. SHIFT & FINANCIALS — CASH DRAWER MATH

### 8.1 Lifecycle
| Event | Route | Effect |
|---|---|---|
| Open shift | `start_shift` ([app.py:349](app.py#L349)) | INSERT row with `start_time=now`, all aggregate cols = 0 |
| Add visit cost | `confirm_payment` ([app.py:741](app.py#L741)) | Stamps `total_cost`, `total_discount`, `manual_discount`, `payment_method`, `shift_id` on the Visit |
| Add sale | `add_sale` ([app.py:999](app.py#L999)) | INSERT into `Sales`, decrement stock |
| Edit sale qty | `edit_sale_quantity` ([app.py:1605](app.py#L1605)) | Recalc + `Shifts.revenue_sales += diff` |
| Add expense | `log_expense` ([app.py:1087](app.py#L1087)) | INSERT into `Expenses`, optionally `+=` stock |
| Sell subscription | `sell_subscription` ([app.py:1164](app.py#L1164)) | INSERT StudentSubscription + INSERT Sale (under "اشتراك باقة") |
| Borrow book | `borrow_book` ([app.py:2382](app.py#L2382)) | `Shifts.revenue_sales += deposit` |
| Return book | `return_book` ([app.py:2425](app.py#L2425)) | INSERT into Expenses for refund + `Shifts.total_expenses += amount_to_return` |
| Preview shift | `shift_summary_preview` ([app.py:845](app.py#L845)) | Recomputes everything live, doesn't persist |
| Close shift | `end_shift` ([app.py:367](app.py#L367)) | Recomputes + writes final aggregate columns + flips `end_time` |

### 8.2 What Adds to the Cash Drawer (Net Cash)
| + Source | Condition | Code |
|---|---|---|
| Visit time cost (Shared) | `payment_method='cash'` AND `check_out_time IS NOT NULL` | [app.py:382-388](app.py#L382-L388) |
| Visit time cost (Private/Meeting) | same | same |
| Sales attached to closed visits | sale's visit checked out | [app.py:391-396](app.py#L391-L396) |
| Cash sales (no visit_id) | `visit_id IS NULL` | same |
| Subscription sales (under "اشتراك باقة" product) | counted as sale | [app.py:1218](app.py#L1218) |
| Library deposit | `revenue_sales += book.price` | [app.py:2417](app.py#L2417) |

### 8.3 What Subtracts from the Cash Drawer
| − Source | Condition | Code |
|---|---|---|
| Expenses (all) | regardless of category | [app.py:397](app.py#L397) |
| Library deposit refund | refund routed as expense | [app.py:2466](app.py#L2466) |
| Total discounts | only from closed visits | [app.py:399-400](app.py#L399-L400) |
| (manual_discount is **already** part of total_discount and not subtracted twice) | | [app.py:816](app.py#L816) |

### 8.4 What Does NOT Affect Cash Drawer
- Time cost paid via subscription (zeroed at checkout — §1.7).
- Sales tied to active (still-inside) visits (filtered out — §1.8.3).
- Stock added via expense (independent of money math).
- Subscription `remaining_hours` adjustments (no cash impact).
- Coupon issuance (only impacts cash when redeemed).
- Reservation creation (no cash impact until visit starts).

### 8.5 Shift Closing Flow — `end_shift` ([app.py:367-431](app.py#L367-L431))
1. Re-fetch all closed-cash visits, split into `rev_shared` / `rev_private`.
2. Sum sales (cash + checked-out-visit-sales).
3. Sum expenses.
4. Sum visit discounts.
5. Compute `net_cash`.
6. UPDATE `Shifts` row with all 5 aggregate columns + `end_time`.
7. Render `shift_ended_success.html` with WhatsApp link to `MANAGER_PHONE`.
8. POP `active_shift_id` from session.

### 8.6 Shift Preview — `shift_summary_preview` ([app.py:845](app.py#L845))
- Same recompute, plus splits `rev_sales` into `cafeteria_sales` vs `package_sales` (using `name LIKE '%باقة%'`).
- Lists every visit in the shift, separated into `checked_out_visits` and `still_inside_visits`.
- Per-visit row shows: student_name, room_name, check_in HH:MM, check_out HH:MM, duration, cost, discount, payment_method, sales_summary string ("شاي ×2, قهوة ×1"), sales_total.
- **Read-only** — does not write to DB. Used only for preview before pressing the close button.

### 8.7 Shift Detail (Manager Drilldown) — `shift_details/<shift_id>` ([app.py:1878](app.py#L1878))
- Manager-only.
- Lists all closed visits + their sales line-items.
- Computes `grand_total = total_cost + sales − discount` per bill.
- Lists "cash sales" (`visit_id IS NULL`) separately.
- Shows expense list.

### 8.8 Daily Aggregate — `financial_reports` ([app.py:1854](app.py#L1854))
- Manager-only.
- Filter: `?report_date=YYYY-MM-DD`. Default = today.
- Groups by closed shifts started on that date.
- Summary fields: `total_revenue` (rev_hours+rev_sales), `total_expenses`, `total_discounts`, `net_profit` (= sum of net_cash, **misnamed** — see §1.9).

### 8.9 Display Color Code (Layout & Templates)
| Color | Meaning |
|---|---|
| `#28a745` / green | Revenue / success / available |
| `#dc3545` / red | Expense / discount-loss / used coupon / out-of-stock |
| `#ffc107` / orange | Warning / pending / low-stock |
| `#007bff` / blue | Active / informational / package sales |
| `#6c757d` / gray | Disabled / expired |

---

## 9. DATABASE SCHEMA REFERENCE

(All from [setup_database.py](setup_database.py) unless noted; columns added by patch scripts marked with **(patched)**.)

### Users
- `user_id` PK, `username` UNIQUE NOT NULL, `password` (pbkdf2:sha256 hash), `role` CHECK IN (`'employee','manager'`).

### Students
- `student_id` PK, `name` NOT NULL, `phone` NOT NULL UNIQUE, `college`, `year` **(was patched, now in setup)**, `created_at` DEFAULT now.

### Rooms
- `room_id` PK, `name` UNIQUE NOT NULL, `hourly_rate` REAL NOT NULL.

### Products
- `product_id` PK, `name` UNIQUE NOT NULL, `purchase_price`, `sale_price`, `stock_quantity` REAL DEFAULT 0.

### Shifts
- `shift_id` PK, `user_id` FK, `start_time` NOT NULL, `end_time`, `revenue_hours`, `revenue_sales`, `total_expenses`, `total_discounts`, `net_cash` (all REAL DEFAULT 0).

### Coupons
- `coupon_id` PK, `code` UNIQUE NOT NULL, `discount_type` CHECK IN (`'percentage','fixed'`), `discount_value` REAL NOT NULL, `is_used` DEFAULT 0, `expiry_date` DATETIME, `student_id` FK.
- **(patched)** `visit_id` referenced in code but NOT declared in setup — schema/code mismatch, see §7.21.

### Reservations
- `reservation_id` PK, `room_id` FK, `client_name`, `phone`, `start_time` NOT NULL, `end_time` NOT NULL, `agreed_price`, `discount_type`, `discount_value` DEFAULT 0, `status` DEFAULT `'confirmed'`, `notes`.

### Visits
- `visit_id` PK, `student_id` FK, `user_id` FK, `shift_id` FK, `room_id` FK, `check_in_time` NOT NULL, `check_out_time`, `duration_hours`, `total_cost`, `coupon_id` FK, `total_discount` DEFAULT 0, `payment_method` DEFAULT `'cash'`, `reservation_id` FK.
- **(patched)** `manual_discount` REAL DEFAULT 0 — added by [add_manual_discount_col.py](add_manual_discount_col.py).

### Sales
- `sale_id` PK, `product_id` FK, `user_id` FK, `shift_id` FK, `quantity` INTEGER NOT NULL, `total_price` REAL NOT NULL, `sale_time` DEFAULT now, `visit_id` FK (nullable for cash sales).

### Expenses
- `expense_id` PK, `user_id` FK, `shift_id` FK, `description` NOT NULL, `amount` NOT NULL, `expense_time` DEFAULT now.

### MembershipTypes
- `type_id` PK, `name` NOT NULL, `total_hours` NOT NULL, `days_valid` INTEGER NOT NULL, `price` NOT NULL.

### StudentSubscriptions
- `sub_id` PK, `student_id` FK, `type_id` FK, `start_date` DEFAULT now, `end_date` NOT NULL, `remaining_hours` REAL NOT NULL, `is_active` DEFAULT 1.

### Books
- `book_id` PK, `title` NOT NULL, `author`, `category`, `price` NOT NULL (used as deposit), `rental_fee_per_day` DEFAULT 5.0, `is_available` DEFAULT 1.

### Borrowings
- `borrow_id` PK, `student_id` FK, `book_id` FK, `borrow_date` DEFAULT now, `return_date`, `deposit_paid` NOT NULL, `final_cost` DEFAULT 0, `status` DEFAULT `'active'`.

### Settings (created by [create_settings_table.py](create_settings_table.py))
- `setting_key` TEXT PRIMARY KEY, `setting_value` TEXT.

---

## 10. UI / TEMPLATE BUSINESS RULES (Embedded only-in-HTML logic)

### 10.1 Live Shift Indicator on Employee Dashboard ([employee_dashboard.html:13-22](templates/employee_dashboard.html))
- Green badge `"الوردية رقم (X) مفتوحة"` if `session.active_shift_id` truthy.
- Red badge `"الوردية مغلقة"` otherwise.
- Without an active shift, a giant "Start Shift" call-to-action replaces the operations grid (lines 39-49).

### 10.2 Imminent-Reservation Banner ([employee_dashboard.html:25-36](templates/employee_dashboard.html), driven by [app.py:262-296](app.py#L262-L296))
- Triggered when there's a confirmed reservation within the next 1 hour.
- Yellow strip with "click to WhatsApp the client" link.

### 10.3 Invoice Receipt Print Layout ([invoice.html](templates/invoice.html))
- All UI hidden via CSS, only `#print-section` printed.
- 300px wide monospace receipt format (designed for thermal printer).
- Total breakdown section shows: time cost, sales, discounts, final.

### 10.4 Confirmation Popups (HTML `onclick="return confirm(...)"`)
| Action | Message |
|---|---|
| End shift | `"⚠️ هل أنت متأكد من إنهاء الوردية نهائياً؟"` ([shift_summary_preview.html:293](templates/shift_summary_preview.html)) |
| Reset data | `"هل أنت متأكد؟ سيتم مسح جميع التعاملات والطلاب والبدء من جديد!"` ([manager_dashboard.html](templates/manager_dashboard.html)) |
| Shutdown server | `"⚠️ هل أنت متأكد أنك تريد إغلاق البرنامج تماماً؟"` ([layout.html](templates/layout.html)) |
| Cancel reservation | (confirm via `<a onclick="return confirm">`) |
| Return book | `"تأكيد استرجاع الكتاب وحساب التكلفة؟"` ([library.html:78-82](templates/library.html)) |

### 10.5 WhatsApp Messages — Hardcoded Templates
| Context | Template | Code location |
|---|---|---|
| Reservation reminder | `"مرحباً أ/ {name} 👋، نود تذكيركم بموعد حجزكم في Workspace (قاعة {room}) اليوم الساعة {time}.\n\nنحن في انتظاركم! 🌹"` | [app.py:284](app.py#L284) |
| Shift end report | `"تقرير وردية {id}\nموظف: {name}\nصافي: {net}"` | [app.py:425](app.py#L425) |
| Coupon push | `"أهلا {name}، ليك كوبون خصم {value}{ % \| ج} الكود: {code}"` | [app.py:1818](app.py#L1818) |
| Win-back regular | `"وحشتنا يا {name}! ليك عرض خاص لو جيت النهاردة ❤️"` | [analytics_report.html](templates/analytics_report.html) |
| Win-back meeting (10d) | `"ازيك يا {name} 👋، ليك وحشة والله! ..."` | [analytics_report.html](templates/analytics_report.html) |
| Win-back meeting (20d) | `"أهلاً {client.name} 🤝، يسعدنا استضافتكم مجدداً..."` | [analytics_report.html](templates/analytics_report.html) |

### 10.6 HTML Form Validation Constants
| Field | Constraint | File |
|---|---|---|
| Reservation start_time | `min={today_min}` (current datetime) | [reservations.html:36](templates/reservations.html) |
| Recurring end_date | `required` only when checkbox is on (JS-driven) | [reservations.html:94-106](templates/reservations.html) |
| Discount value | `step="0.1"`, `min="0"` | multiple |
| Stock add | `step="0.1"`, `min="0"` | [log_expense.html](templates/log_expense.html) |
| Internet free limit | `min="0"` | [manage_settings.html:18](templates/manage_settings.html) |
| Manual time | `type="time"` (HH:MM) | [check_in.html](templates/check_in.html) |

### 10.7 Display Formatting Conventions
| Field | Format |
|---|---|
| Money | `"%.2f"` + ` ج` or `جنيه` |
| Hours (display) | `"%.2f"` (cost contexts) or `"%.1f"` (subscription remaining) |
| Hours (sub list) | `"%.0f"` (rounded int) |
| Date in invoice/print | full `YYYY-MM-DD HH:MM:SS` from DB |
| Date in receipts | `HH:MM` only (`.split(' ')[1]`) |

---

## 11. RUN / OPERATE NOTES

- **Default URL:** http://127.0.0.1:5009/login
- **Default seeded users** (after `add_users.py`): `admin/admin123`, `emp_morning/emp123`, `emp_night/emp456`.
- **DB initialisation order** (when starting fresh): `setup_database.py` → `create_settings_table.py` → `add_users.py` → optional `import_data.py`.
- **Patch scripts are idempotent** — re-running ALTER TABLE catches `OperationalError` and prints "already exists".
- **Backup before reset:** `reset_data_keep_users` and `reset_data.py` both wipe transaction history — copy `workspace.db` first.

---

## 12. KNOWN BUGS / SCHEMA INCONSISTENCIES (Discovered during audit)

1. **[app.py:1553](app.py#L1553)** — `edit_student` blocks managers via `role != 'employee'` check (likely typo).
2. **[app.py:1678](app.py#L1678)** — Internet card free limit hardcoded to `2` instead of reading `Settings.internet_free_limit` (out-of-sync with `add_sale`).
3. **[app.py:813](app.py#L813)** — `UPDATE Coupons SET visit_id=?` references column not declared in `setup_database.py`.
4. **[app.py:1741](app.py#L1741)** — `delete_sale_item` does not adjust `Shifts.revenue_sales` (asymmetric vs `edit_sale_quantity`).
5. **[app.py:802-812](app.py#L802-L812)** — Coupon redemption ignores the bound `student_id` — coupons effectively work for any student.
6. **[app.py:776](app.py#L776)** — `SELECT * FROM StudentSubscriptions WHERE is_active=1` has no ORDER BY → non-deterministic when student has multiple actives.
7. **[app.py:1819](app.py#L1819)** — Coupon WA push uses `phone[1:]` assuming phone starts with `0`; breaks for international/non-zero-prefix numbers.
8. **[app.py:2178](app.py#L2178)** — `conn.close()` is called twice in the `reservations` route (line 2173 and 2177) — second close raises silently on most SQLite versions but is harmless.
9. **[app.py:1873](app.py#L1873)** — `net_profit` field in `financial_reports` is misnamed; it's `SUM(net_cash)`, not actual profit (purchase costs never subtracted).
10. **[app.py:124](app.py#L124)** — `/shutdown` has no auth — anyone (LAN or local) can kill the server.
11. **[app.py:42](app.py#L42)** — `secret_key` hardcoded.

---

> **End of forensic audit.** All formulas, thresholds, magic strings, and edge-case behaviors documented above are derived directly from the codebase as it stands at commit `5c175de` (branch `main`). Any developer or AI inheriting the project should treat this as the single source of truth and reconcile against `app.py` line-by-line before introducing changes.
