# สิทธิ์ของทุก endpoint (TECH-09 / TASK-47)

ตรวจเมื่อ 31 ตุลาคม 2026 บน main หลัง merge PR #136
ไฟล์นี้เป็น permission matrix ของทีม (เสนอใน #137 และยืนยันใน #139)
ถ้าเพิ่มหรือเปลี่ยน view ต้องอัปเดตไฟล์นี้และ `MATRIX` ใน
`apps/properties/tests/test_permission_matrix.py` ใน PR เดียวกัน

## การตรวจสิทธิ์ 2 ชั้น

1. **Membership:** `PropertyMiddleware` ตรวจทุก URL ที่มี `<property_slug>`
   ถ้าไม่ได้ล็อกอินจะพาไปหน้า login ถ้าไม่มี Membership ในโรงแรมนั้นได้ **404**
   ยกเว้น view ที่ติด `@public_property_view` / `PublicPropertyViewMixin`
2. **Role:** แต่ละ view ตรวจเองด้วย `RoleRequiredMixin`, `role_required` หรือ `check_role` (rooms)
   ถ้าอยู่โรงแรมเดียวกันแต่ role ไม่พอได้ **403**

## เทสต์ที่ล็อกกติกาไว้

| เทสต์ | ตรวจอะไร |
| --- | --- |
| `apps/accounts/tests/test_isolation_sweep.py` | ทุก route ของพนักงาน: โรงแรมอื่นได้ 404 ทุก method และไม่มีการเขียน DB; รายการหน้าสาธารณะต้องตรงกับ `PUBLIC_ROUTES` |
| `apps/properties/tests/test_permission_matrix.py` | ทุก route ของพนักงานต้องอยู่ใน `MATRIX`; role ที่ไม่อยู่ในตารางได้ 403 และไม่มีการเขียน DB; role ที่อยู่ในตารางเข้าได้ |

## หน้าของพนักงาน (`/p/<slug>/...`)

O = OWNER, M = MANAGER, F = FRONT_DESK, H = HOUSEKEEPING

| Route | URL | Method | O | M | F | H | เจ้าของ |
| --- | --- | --- | :-: | :-: | :-: | :-: | --- |
| `property_home` | `/p/<slug>/` | GET | ✓ | ✓ | ✓ | ✓ | C |
| `rooms:price-calendar` | `inventory/rates/` | GET | ✓ | ✓ | ✓ | | D |
| `rooms:price-day` | `inventory/rates/<pk>/day/<day>/` | GET, POST | ✓ | ✓ | | | D |
| `rooms:price-bulk` | `inventory/rates/<pk>/bulk/` | GET, POST | ✓ | ✓ | | | D |
| `rooms:<kind>-list` | `inventory/<kind>/` | GET | ✓ | ✓ | ✓ | | D |
| `rooms:<kind>-detail` | `inventory/<kind>/<pk>/` | GET | ✓ | ✓ | ✓ | | D |
| `rooms:<kind>-create` | `inventory/<kind>/new/` | GET, POST | ✓ | ✓ | | | D |
| `rooms:<kind>-edit` | `inventory/<kind>/<pk>/edit/` | GET, POST | ✓ | ✓ | | | D |
| `rooms:<kind>-delete` | `inventory/<kind>/<pk>/delete/` | GET, POST | ✓ | ✓ | | | D |
| `dashboard:hotel` | `dashboard/` | GET | ✓ | ✓ | | | E |
| `tape_chart:move` | `frontdesk/tape-chart/rooms/<id>/move/` | POST | ✓ | ✓ | ✓ | | D |
| `tape_chart:index` | `frontdesk/tape-chart/` | GET | ✓ | ✓ | ✓ | | D |
| `daily_ops:index` | `frontdesk/today/` | GET | ✓ | ✓ | ✓ | | E |
| `check_in_out:check_in` | `frontdesk/bookings/<uuid>/check-in/` | POST | ✓ | ✓ | ✓ | | A |
| `check_in_out:check_out` | `frontdesk/bookings/<uuid>/check-out/` | POST | ✓ | ✓ | ✓ | | A |
| `room_status:index` | `frontdesk/rooms/` | GET | ✓ | ✓ | ✓ | ✓ | A |
| `room_status:mark_clean` | `frontdesk/rooms/<id>/clean/` | POST | ✓ | ✓ | | ✓ | A |
| `folio:detail` | `frontdesk/folio/<uuid>/` | GET | ✓ | ✓ | ✓ | | C |
| `folio:pay` | `frontdesk/folio/<uuid>/pay/` | POST | ✓ | ✓ | ✓ | | C |
| `folio:close` | `frontdesk/folio/<uuid>/close/` | POST | ✓ | ✓ | ✓ | | C |

`<kind>` คือ `room-types`, `rooms` และ `amenities` (route ละ 5 แบบ)

## หน้าที่ไม่ได้อยู่ใต้โรงแรมใดโรงแรมหนึ่ง

| Route | URL | ใครเข้าได้ | เทสต์ |
| --- | --- | --- | --- |
| `accounts:home` | `/accounts/` | ผู้ใช้ที่ล็อกอิน เห็นเฉพาะโรงแรมที่ตัวเองเป็นสมาชิก | `apps/accounts/tests/` |
| login / logout | `/accounts/login/`, `/accounts/logout/` | ทุกคน | Django |
| `dashboard:compare` | `/dashboard/compare/` | ผู้ที่เป็น OWNER อย่างน้อย 1 โรงแรม เห็นเฉพาะโรงแรมที่ตัวเองเป็น OWNER (อื่น ๆ ได้ 403) | `apps/dashboard/tests/test_dashboard_views.py` |
| `bookings:stripe-webhook` | `/stripe/webhook/` | Stripe เท่านั้น: ตรวจลายเซ็น `STRIPE_WEBHOOK_SECRET` ก่อนทำงาน | `apps/bookings/tests/test_webhooks.py` |
| Django admin | `/admin/` | ผู้ใช้ที่มี `is_staff` ของ Django ผ่าน login ของ admin เอง ไม่ใช้ Membership หรือ role ของโรงแรม | Django |
| media | `/media/...` | เฉพาะตอน `DEBUG=True` ดูข้อสังเกตข้อ 1 | — |

## หน้าสำหรับลูกค้า (ไม่ต้องล็อกอิน)

อยู่ใน `PUBLIC_ROUTES` ของ sweep test ข้อมูลทุกอย่างกรองด้วย `request.property`
id ของโรงแรมอื่นได้ 404 หรือข้อความกลาง ๆ ที่ไม่บอกว่ามีอยู่ มีเทสต์ใน `apps/bookings/tests/`

`bookings:search`, `bookings:book`, `bookings:booking-confirmed`, `bookings:pay`, `bookings:payment-result`,
`bookings:manage-booking`, `bookings:manage-booking-cancel`, `bookings:api-availability`, `bookings:api-booking-create`

## `Model.objects` ใน view / service

ไม่มีการใช้กับข้อมูลที่ scoped ตามโรงแรม ที่เหลือเป็นโมเดลส่วนกลาง (`Property`, `Membership`)
ซึ่งเป็นข้อยกเว้นของกฎข้อ 2 ใน AGENTS.md:

| ไฟล์ | เหตุผล |
| --- | --- |
| `apps/properties/middleware.py` | หาโรงแรมจาก slug และ Membership ของผู้ใช้ (ด่านแรกของการแยกข้อมูล) |
| `apps/accounts/services.py` | รายการโรงแรมของผู้ใช้เอง กรองด้วย `user` |
| `apps/bookings/payments.py` | หาโรงแรมจาก metadata ของ Stripe หลังตรวจลายเซ็น webhook แล้ว |
| `apps/bookings/folio.py` | `close_bill()` ล็อกแถว `Property` ของโรงแรมที่ผ่าน middleware แล้ว (กรองด้วย pk) เพื่อออกเลขใบเสร็จทีละใบ และอ่าน `tax_rate` ล่าสุด |

## ข้อสังเกตที่ส่งต่อเจ้าของงาน

1. **ไฟล์อัปโหลดบน production (D, E):** `config/urls.py` เสิร์ฟ `/media/` เฉพาะตอน `DEBUG=True`
   บน production จึงไม่มีใครเสิร์ฟ โลโก้และรูปห้องจะขึ้น 404 ต้องแก้ตอน deploy (#114)
   โลโก้และรูปห้องเป็นข้อมูลสาธารณะตามข้อยกเว้นของกฎข้อ 12 จึงเสิร์ฟโดยไม่ตรวจสิทธิ์ได้
   แต่ไฟล์ที่มีข้อมูลแขกหรือการเงิน เช่น ใบเสร็จ PDF (#102) ห้ามเก็บใน `MEDIA_ROOT` เพราะ #114 ให้ Caddy
   เสิร์ฟ `/media/*` โดยตรง ไฟล์ในนั้นเปิดได้โดยไม่ต้องล็อกอิน แม้จะมี view ที่ตรวจสิทธิ์ก็ตาม
   ต้องสร้างหรือส่งไฟล์ผ่าน view ที่ตรวจสิทธิ์
2. **ลำดับการตรวจใน inventory (D):** `inventory_detail`, `inventory_edit`, `inventory_delete` และ `price_edit`
   หา object ก่อนตรวจ role ทำให้ HOUSEKEEPING ได้ 404 สำหรับ id ที่ไม่มี และ 403 สำหรับ id ที่มี
   เห็นแค่ข้อมูลในโรงแรมตัวเอง จึงไม่ใช่การรั่วข้ามโรงแรม
3. **หน้ายืนยันการจอง, ปุ่มชำระเงิน และผลการชำระเงิน (A, C):** `bookings:booking-confirmed`, `bookings:pay`
   และ `bookings:payment-result` ใครมี UUID ของการจองก็เปิดได้โดยไม่ล็อกอิน
   UUID เดายาก จึงใช้เป็นลิงก์ส่วนตัวของลูกค้าได้ แต่ห้ามแสดง UUID ในที่สาธารณะ
