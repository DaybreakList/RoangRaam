# ADR 0005: ระบบจริงบน Render แพ็กเกจฟรี แทน Azure VM

- **สถานะ:** ใช้อยู่ (แทนแผน Azure VM ใน `docs/project/STAGING.md`)
- **วันที่:** 2 พฤศจิกายน 2026 (Sprint 3)

## บริบท

แผนแรก (TASK-03, TECH-10) คือ VM บน Azure for Students: Docker Compose ที่มี Caddy → gunicorn → PostgreSQL
และ GitHub Actions build image แล้ว deploy ผ่าน SSH (#129, #130)
ก่อนตั้งค่าเสร็จ อาจารย์กำหนดให้ใช้ Render ทีมจึงเปรียบเทียบค่าใช้จ่ายและข้อจำกัดแล้วตกลงย้ายในวันที่ 2 พ.ย.

## การตัดสินใจ

- ใช้ Render Web Service (Docker จาก `Dockerfile`) คู่กับ Render PostgreSQL 16 ทั้งคู่ region Singapore แพ็กเกจฟรี
- `scripts/start.sh` เป็นคำสั่งเริ่มของ container: migrate, สร้างข้อมูลเดโมตาม `SEED_ON_START`
  (รีเซ็ตด้วย `SEED_RESET_ON_START`), สร้าง admin จาก `DJANGO_SUPERUSER_*`, แล้วเปิด gunicorn ที่ `$PORT`
  เพราะแพ็กเกจฟรีไม่มี Shell และ Cron
- HTTPS จบที่ Render: `DJANGO_HTTPS=True` เปิด proxy header, redirect, cookie แบบ Secure และ HSTS
  ชื่อโฮสต์จาก `RENDER_EXTERNAL_HOSTNAME` ถูกเพิ่มใน `ALLOWED_HOSTS` และ CSRF origins เอง
- static ใช้ WhiteNoise ส่วน `/media/` ให้ Django เสิร์ฟเมื่อ `DJANGO_SERVE_MEDIA=True` (#183) เพราะไม่มี web server ด้านหน้า
- อีเมลใช้ console backend (แสดงใน Logs) เพราะแพ็กเกจฟรีบล็อก SMTP
- ค่าลับทั้งหมดอยู่ใน Environment ของ Render เท่านั้น และ Render deploy ใหม่เมื่อมี commit เข้า `main`

## ผลที่ตามมา

- **ดี:** ไม่มีค่าใช้จ่าย ไม่ต้องดูแลเครื่อง ได้ HTTPS และ deploy จาก `main` ทันที
- **ข้อจำกัดที่ทีมยอมรับ** (รายละเอียดและวิธีรับมือใน `docs/project/DEPLOY.md`):
  - เครื่องพักเมื่อไม่มีคนใช้ 15 นาที เปิดครั้งแรกรอประมาณ 1 นาที
  - ไฟล์ในเครื่องหายเมื่อ restart, deploy หรือเครื่องพัก โลโก้และรูปห้องต้องอัปโหลดใหม่ก่อนเดโม
  - ส่งอีเมลจริงไม่ได้
  - ฐานข้อมูลฟรีหมดอายุ 30 วันหลังสร้าง และไม่มี backup อัตโนมัติ ต้อง `pg_dump` เอง
  - ไม่มี Cron: `release_expired_bookings` ไม่ได้รันตามเวลา แต่การค้นหาไม่นับการจองที่หมดเวลาชำระอยู่แล้ว
- **ของที่เลิกใช้แต่ยังอยู่ใน repo:** job `deploy` ใน `.github/workflows/ci.yml` (ถูกข้ามเสมอเพราะไม่ได้ตั้ง `STAGING_DEPLOY_ENABLED`),
  `docker-compose.prod.yml`, `Caddyfile`, `scripts/deploy_staging.sh`, `.env.production.example` และ `docs/project/STAGING.md`
  เก็บไว้ถึงหลังนำเสนอเพื่อไม่เสี่ยงกับโค้ดหลัง code freeze (มีเทสต์ที่อ่าน `docker-compose.prod.yml` อยู่) แล้วค่อยลบใน PR แยก
