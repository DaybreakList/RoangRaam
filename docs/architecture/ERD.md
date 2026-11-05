# ERD (TASK-62)

สร้างจาก model จริงใน `main` (`django.apps.apps.get_models()` ของ app `properties`, `rooms`, `bookings`)
ถ้าแก้ model ให้แก้ไฟล์นี้ใน PR เดียวกัน

ตารางข้อมูลดำเนินงานทุกตารางสืบทอด `PropertyScopedModel` จึงมี FK `property` ไปที่ `Property`
ในแผนภาพละเส้น `property` ของตารางเหล่านี้ไว้ และแสดงเฉพาะ field หลัก
(ไม่มี เช่น `Property.max_rooms`, `max_staff`, `RoomType.description`, `Booking.created_at`, `cancelled_at`)
เพื่อให้อ่านความสัมพันธ์หลักได้ง่าย field ครบอยู่ใน `models.py` ของแต่ละ app
([ADR 0002](../adr/0002-one-database-scoped-by-property.md))

```mermaid
erDiagram
    User ||--o{ Membership : "has"
    Property ||--o{ Membership : "has staff"
    Property ||--o{ RoomType : "owns (scoped)"
    Property ||--o{ Guest : "owns (scoped)"

    RoomType ||--o{ Room : "has"
    RoomType ||--o{ DailyRate : "priced per day"
    RoomType ||--o{ RoomTypeImage : "has"
    RoomType ||--o{ RoomTypeAmenity : "has"
    Amenity ||--o{ RoomTypeAmenity : "used by"

    Guest ||--o{ Booking : "makes"
    Booking ||--|{ BookingRoom : "holds rooms"
    RoomType ||--o{ BookingRoom : "booked as"
    Room ||--o{ BookingRoom : "assigned to"
    BookingRoom ||--|{ BookingNightRate : "price per night"
    Booking ||--o{ Payment : "money in / out"
    Booking ||--o| Receipt : "closed bill"
    User |o--o{ BookingRoom : "checked_in_by"
    User |o--o{ Payment : "recorded_by"
    User |o--o{ Receipt : "issued_by"

    User {
        bigint id PK
        string email UK
        string first_name
        string last_name
        bool is_staff
    }
    Property {
        uuid id PK
        string name
        string slug UK
        text address
        string timezone
        file logo
        time check_in_time
        time check_out_time
        decimal tax_rate
        text policy_text
        bool is_active
    }
    Membership {
        bigint id PK
        bigint user_id FK
        uuid property_id FK
        string role "OWNER, MANAGER, FRONT_DESK, HOUSEKEEPING"
    }
    RoomType {
        bigint id PK
        uuid property_id FK
        string name
        int max_occupancy
        string bed_type
        int size_sqm
    }
    Room {
        bigint id PK
        uuid property_id FK
        bigint room_type_id FK
        string number
        int floor
        string hk_status "CLEAN, DIRTY"
        bool is_active
    }
    Amenity {
        bigint id PK
        uuid property_id FK
        string name
    }
    RoomTypeAmenity {
        bigint id PK
        bigint room_type_id FK
        bigint amenity_id FK
    }
    RoomTypeImage {
        bigint id PK
        bigint room_type_id FK
        file image
    }
    DailyRate {
        bigint id PK
        bigint room_type_id FK
        date date
        decimal price
    }
    Guest {
        bigint id PK
        uuid property_id FK
        string first_name
        string last_name
        string email
        string phone
        string id_type "ID_CARD, PASSPORT"
        string id_number
        string nationality
    }
    Booking {
        uuid id PK
        uuid property_id FK
        bigint guest_id FK
        string code
        string status "PENDING_PAYMENT, CONFIRMED, CHECKED_IN, CHECKED_OUT, CANCELLED"
        string payment_option "FULL, PAY_AT_HOTEL"
        date check_in_date
        date check_out_date
        int adults
        int children
        decimal total_amount
        uuid idempotency_key
    }
    BookingRoom {
        bigint id PK
        uuid booking_id FK
        bigint room_type_id FK
        bigint room_id FK
        daterange stay_range
        bool is_active
        decimal subtotal
        datetime checked_in_at
        datetime checked_out_at
        bigint checked_in_by FK
    }
    BookingNightRate {
        bigint id PK
        bigint booking_room_id FK
        date night
        decimal price
    }
    Payment {
        uuid id PK
        uuid booking_id FK
        string kind "CHARGE, REFUND"
        string source "STRIPE, FRONT_DESK"
        decimal amount
        string provider_ref
        string idempotency_key
        bigint recorded_by FK
    }
    Receipt {
        bigint id PK
        uuid booking_id FK
        int number
        decimal total
        decimal tax_rate
        decimal tax_amount
        datetime issued_at
        bigint issued_by FK
    }
```

## Constraint ที่สำคัญ

กฎเหล่านี้บังคับที่ฐานข้อมูล ไม่ใช่แค่ใน Python จึงยังป้องกันได้แม้มีคำขอพร้อมกัน

| ตาราง | Constraint | ความหมาย |
| --- | --- | --- |
| BookingRoom | `bookings_bookingroom_no_overlap` (ExclusionConstraint: `room =`, `stay_range &&`) | ห้องเดียวกันมีช่วงเข้าพักที่ทับกันไม่ได้ มีผลเฉพาะแถว `is_active=True` (ยกเลิกหรือปล่อยห้องแล้วไม่กัน) ([ADR 0003](../adr/0003-prevent-double-booking.md)) ช่วงเป็น `[check_in, check_out)` เช็คเอาต์และเช็คอินวันเดียวกันจึงไม่ทับ |
| Booking | unique (`property`, `code`), unique (`property`, `idempotency_key`), check-out หลัง check-in, ผู้ใหญ่อย่างน้อย 1, ยอดไม่ติดลบ | รหัสการจองไม่ซ้ำในโรงแรม และกดส่งซ้ำไม่ได้การจองสองรายการ |
| BookingNightRate | unique (`property`, `booking_room`, `night`) | ราคาของแต่ละคืนบันทึกไว้ครั้งเดียวตอนจอง ([ADR 0004](../adr/0004-money-and-append-only-payments.md)) |
| Payment | unique (`property`, `provider_ref`), unique (`property`, `idempotency_key`), ยอดมากกว่า 0 | webhook ที่ Stripe ส่งซ้ำ หรือการกดซ้ำ ไม่สร้างรายการเงินซ้ำ |
| Receipt | unique (`property`, `number`), unique (`property`, `booking`), VAT ไม่เกินยอดรวม | เลขที่ใบเสร็จเรียงต่อโรงแรม และปิดบิลได้ครั้งเดียวต่อการจอง |
| Guest | unique (`property`, lower(`email`)) | แขกหนึ่งอีเมลต่อโรงแรม |
| Room, RoomType, Amenity, DailyRate | unique ที่รวม `property` เสมอ (เลขห้อง, ชื่อประเภท, ชื่อสิ่งอำนวยความสะดวก, ราคาต่อวัน) | ต่างโรงแรมใช้ชื่อหรือเลขซ้ำกันได้ (AGENTS.md ข้อ 5) |
| Membership | unique (`user`, `property`) | หนึ่งคนมีได้หนึ่ง role ต่อโรงแรม |

`Property` และ `Booking` ใช้ UUID เป็น primary key (AGENTS.md ข้อ 7) เพื่อไม่ให้เดาลำดับจาก URL ได้
`Payment` และ `Receipt` แก้หรือลบไม่ได้หลังสร้าง (`save()` และ `delete()` ปฏิเสธ)
