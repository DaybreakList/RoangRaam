# ADR 0001: Django monolith แบ่งตาม app และ service ไม่ทำ SPA

- **สถานะ:** ใช้อยู่
- **วันที่:** ตุลาคม 2026 (Sprint 1)

## บริบท

ทีม 5 คนมีเวลา 4 สัปดาห์ ต้องส่งระบบที่มีทั้งหน้าลูกค้าและหน้าพนักงานหลายส่วน
แต่ละคนรับผิดชอบคนละส่วน (ห้องและราคา, การจอง, ชำระเงิน, หน้าเคาน์เตอร์, Dashboard)
ต้องทำงานคู่ขนานได้โดยไม่ชนกันมาก และทุกคนต้องอธิบายโค้ดของตัวเองได้ตอนนำเสนอ

## การตัดสินใจ

- ใช้ Django project เดียว deploy เป็นบริการเดียว แบ่งโค้ดเป็น app ตามขอบเขตงาน
  (`properties`, `rooms`, `bookings`, `frontdesk`, `dashboard`, `accounts`) และกำหนดเจ้าของแต่ละ app ใน `AGENTS.md`
- business logic อยู่ในไฟล์ service (`services.py`, `services/*.py`, `payments.py`, `folio.py`) view ทำแค่รับ request,
  ตรวจสิทธิ์ และเรียก service ทำให้เทสต์กฎทางธุรกิจได้โดยไม่ผ่าน HTTP
- หน้าเว็บใช้ Django templates แสดงผลจาก server เสริมด้วย HTMX และ Alpine.js เฉพาะส่วนที่ต้องโต้ตอบ
  และ Chart.js สำหรับกราฟ ไม่ใช้ React หรือ Vue
- หน้าเว็บส่งข้อมูลด้วย Django form และ HTMX ไปที่ view ปกติ ส่วนที่หน้าเว็บเรียกเป็น JSON ด้วย JavaScript
  มีแค่การย้ายห้องใน Tape Chart
- JSON API ใต้ `/api/p/<slug>/...` (ค้นหาห้องว่าง, สร้างการจอง) มีให้แยกตาม `docs/project/API_CONTRACT.md`
  แต่หน้าเว็บของระบบไม่ได้ใช้
- ไม่มี Celery หรือ Redis ส่งอีเมลแบบ synchronous

## ผลที่ตามมา

- **ดี:** deploy และ debug ง่าย มี process เดียว ฐานข้อมูลเดียว transaction ข้าม app ได้ตรง ๆ
  (เช่น เช็คเอาต์ตรวจบิลจาก `bookings.folio` ใน transaction เดียวกัน)
- **ดี:** ไม่ต้องดูแล API และ frontend แยกกัน ตรวจสิทธิ์ที่ server ที่เดียว
- **ดี:** CI ชุดเดียว (ruff, `manage.py check`, migrations, pytest + coverage) ครอบทั้งระบบ
- **ข้อเสีย:** app ต่าง ๆ import service ของกันและกันโดยตรง ต้องคุมด้วยการ review ไม่ใช่ด้วยขอบเขตของบริการ
- **ข้อเสีย:** งานที่ใช้เวลานาน (ส่งอีเมล, เรียก Stripe) ทำใน request เดียวกัน ถ้าบริการภายนอกช้าหน้าเว็บจะช้าตาม
  อีเมลยืนยันจึงส่งแบบไม่ทำให้การจองล้มเมื่อส่งไม่สำเร็จ (`send_booking_confirmation_safely`)
