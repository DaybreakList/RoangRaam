# API Contract

เอกสารกลางของ JSON API ทั้งหมดในโปรเจกต์ (การ์ด #8)
ทุก endpoint ที่ระบุว่า "ใช้งานได้" ตรงกับโค้ดใน `main` และมีเทสยืนยันรูปแบบ JSON แล้ว
เพิ่ม API ใหม่ให้เพิ่มหัวข้อในไฟล์นี้ใน PR เดียวกับโค้ด

## ข้อตกลงร่วม

| เรื่อง | ข้อตกลง |
| --- | --- |
| Base path | `/api/p/<property_slug>/...` โรงแรมมาจาก slug ใน URL เสมอ ห้ามรับ `property_id` จาก body |
| วันที่ | `YYYY-MM-DD` (ISO 8601) ช่วงเข้าพักเป็น `[check_in, check_out)` คือไม่นับคืนวันเช็คเอาต์ |
| เงิน | **decimal string** ทศนิยม 2 ตำแหน่ง เช่น `"3700.50"` ห้ามแปลงเป็น float ทั้งฝั่ง server และหน้าเว็บ |
| ข้อความ error | ภาษาไทย รูปแบบ `{"<field>": ["ข้อความ"]}` แบบเดียวกับ DRF |
| Auth | Session ของ Django ถ้าล็อกอินอยู่แล้วส่ง `POST` ต้องแนบ CSRF token (`base.html` ใส่ header ให้ HTMX แล้ว) |
| Public endpoint | ต้องมี `PublicPropertyViewMixin` และเพิ่มชื่อ route ใน `PUBLIC_ROUTES` ของ `apps/accounts/tests/test_isolation_sweep.py` |

### Status code

| Code | เมื่อไหร่ |
| --- | --- |
| 200 / 201 | สำเร็จ |
| 400 | ข้อมูลไม่ถูกต้อง หรือทำตามที่ขอไม่ได้ (เช่น ห้องเต็ม) |
| 404 | slug ไม่มีอยู่ / โรงแรมปิดใช้งาน / ไม่ใช่สมาชิก (endpoint สำหรับพนักงาน) / id ใน URL หรือ body เป็นของโรงแรมอื่น |
| 405 | method ที่ endpoint ไม่รองรับ |

**ข้อจำกัด:** 404 ที่มาจาก `PropertyMiddleware` (slug ผิด, โรงแรมปิด, ไม่ใช่สมาชิก) เป็นหน้า HTML
ไม่ใช่ JSON เพราะ middleware ตอบก่อนถึง DRF ฝั่งหน้าเว็บให้เช็คแค่ status code
ส่วน 404 จาก id ใน body (เช่น `room_type_id` ของโรงแรมอื่น) เป็น JSON ของ DRF

---

## ค้นหาห้องว่าง — ใช้งานได้ (#51)

`GET /api/p/<property_slug>/availability/`

- **เจ้าของ:** A · **โค้ด:** `apps/bookings/api.py` → `get_available_room_types()`
- **สิทธิ์:** public ไม่ต้องล็อกอิน

### Query string

| Param | บังคับ | หมายเหตุ |
| --- | --- | --- |
| `check_in` | ✓ | ห้ามเป็นวันที่ผ่านมาแล้ว |
| `check_out` | ✓ | ต้องหลัง `check_in` |
| `guests` | | จำนวนเต็ม ≥ 1 แสดงเฉพาะประเภทห้องที่ `max_occupancy × available_count >= guests` (แบ่งกลุ่มไปหลายห้องได้) |

### 200

```json
{
  "check_in": "2026-10-28",
  "check_out": "2026-10-31",
  "nights": 3,
  "guests": 2,
  "room_types": [
    {
      "id": 1,
      "name": "Deluxe",
      "description": "",
      "max_occupancy": 3,
      "bed_type": "King",
      "size_sqm": 32,
      "amenities": ["Wi-Fi"],
      "images": ["/media/properties/<property_id>/room.png"],
      "available_count": 2,
      "total_price": "3700.50"
    }
  ]
}
```

- `guests` เป็น `null` ถ้าไม่ได้ส่งมา
- `total_price` คือผลรวม `DailyRate` ทีละคืน ไม่ใช่ราคา × จำนวนคืน
- ไม่แสดงประเภทห้องที่ห้องเต็ม หรือยังตั้งราคาไม่ครบทุกคืน ไม่มีผลลัพธ์ได้ `"room_types": []`
- การจอง `FULL` ที่ค้าง `PENDING_PAYMENT` เกิน 30 นาที (`PAYMENT_WINDOW`) ไม่นับว่าห้องไม่ว่าง ห้องจะถูกปล่อยจริงตอนมีคนจองประเภทนั้น
- เรียงตาม `name`

### 400 (ตัวอย่าง)

```json
{"check_in": ["กรุณาระบุวันที่"], "check_out": ["กรุณาระบุวันที่"]}
{"check_in": ["รูปแบบวันที่ไม่ถูกต้อง (ใช้ YYYY-MM-DD)"]}
{"check_in": ["วันเช็คอินต้องไม่ใช่วันที่ผ่านมาแล้ว"]}
{"check_out": ["วันเช็คเอาต์ต้องอยู่หลังวันเช็คอิน"]}
{"guests": ["จำนวนผู้เข้าพักต้องมีอย่างน้อย 1 คน"]}
{"guests": ["จำนวนผู้เข้าพักต้องเป็นตัวเลข"]}
```

---

## สร้างการจอง (หลายห้องในบิลเดียว) — ใช้งานได้ (#52)

`POST /api/p/<property_slug>/bookings/`

- **เจ้าของ:** A · **โค้ด:** `apps/bookings/api.py` → `create_booking()`
- **สิทธิ์:** public ไม่ต้องล็อกอิน
- **Content-Type:** `application/json`

### Request

```json
{
  "check_in": "2026-10-28",
  "check_out": "2026-10-30",
  "adults": 3,
  "children": 0,
  "payment_option": "FULL",
  "guest": {
    "first_name": "Somchai",
    "last_name": "Jaidee",
    "email": "somchai@example.com",
    "phone": "0812345678"
  },
  "rooms": [{"room_type_id": 1, "quantity": 2}]
}
```

| Field | หมายเหตุ |
| --- | --- |
| `adults` | ≥ 1 |
| `children` | ไม่บังคับ ค่าเริ่มต้น 0 |
| `payment_option` | `FULL` → สถานะ `PENDING_PAYMENT` · `PAY_AT_HOTEL` → สถานะ `CONFIRMED` |
| `rooms` | อย่างน้อย 1 รายการ รวมไม่เกิน 20 ห้อง `room_type_id` ซ้ำได้ (ระบบรวมจำนวนให้) |
| `idempotency_key` | ไม่บังคับ UUID ที่หน้าเว็บสร้างตอนแสดงฟอร์มยืนยัน ส่ง key เดิมซ้ำ (เช่น กดเบิ้ล หรือเน็ตหลุดแล้วส่งใหม่) จะได้การจองเดิมกลับมาเป็น 201 พร้อม JSON เดิม ไม่สร้างใหม่ แม้ข้อมูลที่ส่งมาจะต่างไป key แยกกันในแต่ละโรงแรม |

ระบบเลือกห้องจริงให้อัตโนมัติตามลำดับเลขห้อง และตรวจว่า `adults + children` ไม่เกินความจุรวมของห้องที่เลือก

### 201

```json
{
  "id": "6f1c2a8e-0000-0000-0000-000000000000",
  "code": "K7QM2XPA",
  "status": "PENDING_PAYMENT",
  "payment_option": "FULL",
  "check_in_date": "2026-10-28",
  "check_out_date": "2026-10-30",
  "nights": 2,
  "adults": 3,
  "children": 0,
  "total_amount": "5001.00",
  "guest": {"email": "somchai@example.com"},
  "rooms": [
    {
      "room_type_id": 1,
      "room_type_name": "Twin",
      "room_number": "101",
      "subtotal": "2500.50",
      "nights": [
        {"night": "2026-10-28", "price": "1000.00"},
        {"night": "2026-10-29", "price": "1500.50"}
      ]
    }
  ],
  "created_at": "2026-10-18T15:40:18.123456+07:00"
}
```

- `id` เป็น UUID ส่วน `code` เป็นรหัส 8 ตัวที่ใช้บอกลูกค้า (ไม่มี 0/O/1/I)
- ราคาใน `nights` เป็นค่า ณ ตอนจอง ถ้าแก้ `DailyRate` ภายหลัง การจองนี้ไม่เปลี่ยน
- `total_amount` = ผลรวม `subtotal` ทุกห้อง (ราคารวม VAT แล้ว)
- `guest` ส่งกลับแค่ `email` หน้ายืนยันการจองให้แสดงชื่อจากที่ลูกค้ากรอกในฟอร์มเอง

### ผู้เข้าพัก (Guest)

หนึ่งโรงแรมมี Guest ได้หนึ่งคนต่ออีเมล (`unique(property, lower(email))`) ไม่สนตัวพิมพ์เล็ก/ใหญ่และช่องว่างหัวท้าย

- อีเมลที่มีอยู่แล้ว → ใช้ Guest เดิม **ไม่เขียนทับ** ชื่อและเบอร์โทร (API นี้ไม่ต้องล็อกอิน ใครก็พิมพ์อีเมลคนอื่นได้)
- อีเมลเดียวกันคนละโรงแรม → Guest คนละคน
- ข้อมูลที่ส่งมายังถูกตรวจทุกช่องแม้อีเมลจะมีอยู่แล้ว
- response ไม่ส่งชื่อหรือเบอร์โทรที่บันทึกไว้กลับไป เพื่อไม่ให้ข้อมูลลูกค้ารั่วจากการเดาอีเมล

### 400 (ตัวอย่าง)

```json
{"rooms": ["ห้องประเภท Twin เหลือไม่พอ (ว่าง 1 ห้อง)"]}
{"rooms": ["ห้องประเภท Twin ยังไม่เปิดราคาครบทุกคืนที่เลือก"]}
{"rooms": ["ห้องที่เลือกเพิ่งถูกจองไป กรุณาค้นหาห้องว่างอีกครั้ง"]}
{"rooms": ["จองได้สูงสุด 20 ห้องต่อครั้ง"]}
{"adults": ["จำนวนผู้เข้าพักเกินความจุของห้องที่เลือก (พักได้สูงสุด 4 คน)"]}
{"adults": ["กรุณาระบุจำนวนผู้ใหญ่"]}
{"idempotency_key": ["idempotency_key ต้องเป็น UUID"]}
```

ข้อความ "เพิ่งถูกจองไป" เกิดเมื่อมีคนจองห้องเดียวกันพร้อมกัน หน้าเว็บควรพาลูกค้ากลับไปค้นหาใหม่

ก่อนเลือกห้อง ระบบยกเลิกการจอง `FULL` ของประเภทห้องที่ขอซึ่งค้าง `PENDING_PAYMENT` เกิน 30 นาที (สถานะ `CANCELLED`) แล้วจึงนำห้องนั้นมาใช้ได้

จำกัด 20 ห้องต่อการจองตรวจที่ service (`MAX_ROOMS_PER_BOOKING` ใน `apps/bookings/services.py`) จึงมีผลทั้ง API และหน้าเว็บ

### 404

`room_type_id` ไม่มีอยู่ หรือเป็นของโรงแรมอื่น ไม่มีข้อมูลใดถูกสร้าง

---

## API ที่ยังไม่มี

เจ้าของงานเติมหัวข้อเหล่านี้เมื่อออกแบบ endpoint แล้ว

| API | เจ้าของ | หมายเหตุ |
| --- | --- | --- |
| ยกเลิกการจอง | — | service `cancel_booking()` มีแล้ว (#54) ยังไม่มี endpoint |
| ชำระเงิน (PromptPay / Stripe test) | C | ต้องกำหนดการหมดเวลาของ `PENDING_PAYMENT` ด้วย |
| Arrival / Departure / In-house, เช็คอิน / เช็คเอาต์ | A, B | |
| Tape Chart (แสดงผล + ลากย้ายห้อง) | D | ผลการลากต้อง validate ฝั่ง server |
| Dashboard KPI | E | |
