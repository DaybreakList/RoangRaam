# Production บน Render (TECH-10)

ระบบจริงสำหรับเดโมรันบน Render: Web Service จาก `Dockerfile` คู่กับ Managed PostgreSQL ใน region Singapore
แทนแผน Azure เดิมใน [STAGING.md](STAGING.md) ซึ่งเลิกใช้แล้ว

```text
Browser -> Render (HTTPS) -> gunicorn + WhiteNoise (Django) -> Render PostgreSQL
```

- ค่าลับทั้งหมดใส่ใน Environment ของ Render เท่านั้น repo นี้เป็น public ห้าม commit ค่าลับ และห้ามวางในแชท
- ทุกครั้งที่ container เริ่ม `scripts/start.sh` จะรัน `migrate` แล้วเปิด gunicorn ที่ `$PORT`
- Render deploy ใหม่เองเมื่อมี commit เข้า `main`

## ตั้งค่าครั้งแรก

### 1. สร้างฐานข้อมูล

New > PostgreSQL

| ช่อง | ค่า |
| --- | --- |
| Name | `roangraam-db` |
| Region | Singapore (Southeast Asia) |
| PostgreSQL Version | 16 |
| Instance Type | Free |

รอจนสถานะเป็น Available แล้วเปิดหน้า Connect > Internal จะเห็น Hostname, Port, Database, Username และ Password แยกช่อง
ใช้ค่าเหล่านี้ในขั้น 2 ไม่ต้องใช้ External URL

### 2. สร้าง Web Service

New > Web Service > เลือก repo `DaybreakList/RoangRaam`

| ช่อง | ค่า |
| --- | --- |
| Name | `roangraam` (ได้ URL `https://roangraam.onrender.com` ถ้าชื่อยังว่าง) |
| Region | Singapore (ต้องตรงกับฐานข้อมูล) |
| Branch | `main` |
| Language | Docker |
| Instance Type | Free |
| Auto-Deploy | After CI Checks Pass |

ไม่ต้องใส่ Build Command หรือ Start Command เพราะใช้ `Dockerfile` (build รัน `collectstatic` และ `CMD` เรียก `scripts/start.sh`)

### 3. Environment Variables

| Key | ค่า |
| --- | --- |
| `DJANGO_SECRET_KEY` | กด Generate บนหน้า Render |
| `DJANGO_DEBUG` | `False` |
| `DJANGO_HTTPS` | `True` |
| `DJANGO_ALLOWED_HOSTS` | เว้นว่างได้ ระบบเพิ่มชื่อโฮสต์ของ Render จาก `RENDER_EXTERNAL_HOSTNAME` ให้เอง |
| `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` | จากหน้า Internal ของฐานข้อมูลในขั้น 1 |
| `WEB_CONCURRENCY` | `2` (แพ็กเกจฟรีมี RAM 512 MB) |
| `EMAIL_BACKEND` | `django.core.mail.backends.console.EmailBackend` (อีเมลแสดงใน Logs เพราะแพ็กเกจฟรีบล็อก SMTP) |
| `DEFAULT_FROM_EMAIL` | `noreply@roangraam.onrender.com` |
| `STRIPE_SECRET_KEY`, `STRIPE_PUBLISHABLE_KEY`, `STRIPE_WEBHOOK_SECRET` | คีย์โหมดทดสอบเท่านั้น จากเจ้าของบัญชี Stripe |
| `SEED_ON_START` | `true` |
| `SEED_DEMO_PASSWORD` | รหัสผ่านของบัญชีเดโม |
| `DJANGO_SUPERUSER_EMAIL`, `DJANGO_SUPERUSER_PASSWORD` | บัญชี admin สำหรับ `/admin/` สร้างครั้งแรกที่เริ่ม |

Stripe webhook ให้ชี้มาที่ `https://<host>/stripe/webhook/`

### 4. ตรวจหลัง deploy

- Logs มี `migrate` ผ่าน และ gunicorn ขึ้น `Listening at: http://0.0.0.0:10000` แล้วสถานะเป็น Live
- เปิดหน้า login ผ่าน HTTPS ได้ CSS ครบ และ `http://` ถูกส่งต่อไป `https://`
- login บัญชีเดโมทั้ง 4 role และลอง จอง > ชำระเงิน > เช็คอิน > ปิดบิล > เช็คเอาต์ > ใบเสร็จ PDF
- เทสต์ `test_production_settings_pass_the_deployment_checklist` รัน `check --deploy` แบบไม่ยอมให้มีคำเตือนอยู่แล้ว
  ถ้าต้องการตรวจบน Render เอง ใช้แท็บ Shell ได้เฉพาะแพ็กเกจเสียเงิน

## ข้อจำกัดของแพ็กเกจฟรีและสิ่งที่ต้องทำวันเดโม

| ข้อจำกัด | ผล | สิ่งที่ทำ |
| --- | --- | --- |
| พักเครื่องหลังไม่มีคนเข้า 15 นาที ตื่นใช้ราว 1 นาที | หน้าแรกช้า | เปิดเว็บไว้ก่อนเริ่มนำเสนออย่างน้อย 2 นาที |
| ไฟล์ในเครื่องหายเมื่อ restart หรือ deploy ใหม่ | โลโก้และรูปห้องที่อัปโหลดหาย (ข้อมูลเดโมไม่มีรูป) | อัปโหลดใหม่ก่อนเดโม และไม่ deploy ระหว่างนั้น |
| บล็อก SMTP (port 25, 465, 587) | ส่งอีเมลจริงไม่ได้ | ดูอีเมลยืนยันในแท็บ Logs |
| PostgreSQL ฟรีหมดอายุ 30 วันหลังสร้าง | ฐานข้อมูลใช้ไม่ได้ | สร้างใหม่ถ้าต้องใช้หลังตรวจงาน |
| ไม่มี Shell และไม่มี Cron Job | รัน `seed_demo --reset` และ `release_expired_bookings` เองไม่ได้ | การค้นหาไม่นับ hold ที่หมดเวลาอยู่แล้ว |

## สร้างข้อมูลเดโมใหม่สำหรับวันนี้

ข้อมูลเดโมอิงวันที่ที่ seed เช่น "เช็คอินวันนี้" คือวันที่ seed ผ่านไปหนึ่งวันข้อมูลจะเลื่อนเป็นของเมื่อวาน
ก่อน UAT, การถ่ายภาพคู่มือ และวันเดโม ให้สร้างใหม่ดังนี้

1. Environment ของ Web Service: เพิ่ม `SEED_RESET_ON_START` = `true` แล้ว Save and deploy
2. รอสถานะ Live แล้วดู Logs ว่ามี `SEED_RESET_ON_START is on` และ `สร้างข้อมูลเดโมเรียบร้อย`
3. **ตั้ง `SEED_RESET_ON_START` กลับเป็น `false` ทันที** แล้ว Save and deploy อีกรอบ
   ถ้าลืม ทุกครั้งที่เครื่องตื่นจากการพักหรือ deploy ใหม่ ข้อมูลเดโมจะถูกลบและสร้างใหม่ การจองที่ทำไว้ระหว่างเดโมจะหายไปด้วย

การ reset ลบเฉพาะโรงแรมเดโม 2 แห่งและข้อมูลในโรงแรมนั้น รวมถึงการชำระเงินและใบเสร็จ
บัญชีเดโมใช้รหัสเดิมจาก `SEED_DEMO_PASSWORD` ส่วนบัญชี admin ไม่ถูกลบ

## ย้อนกลับ

หน้า Events ของ Web Service เลือก deploy ก่อนหน้าแล้วกด Rollback
