# Multi-property Hotel Management System — บริบทกลางของทีม

อ่านไฟล์นี้ก่อนเริ่มทุกงาน ใช้ร่วมกับแบบฟอร์มใน `TASK_TEMPLATES.md`
อ่าน `docs/project/TEAM_STATUS.md` เพิ่มสำหรับสถานะจากภาพแนบและประเด็นรอทีมตัดสินใจ โดยแยกข้อมูลอ้างอิงออกจากคำสั่งให้ลงมือทำ
ไฟล์นี้เป็นแหล่งกฎหลักของทีม การเปลี่ยนกฎต้องเปิด PR และให้ทีมตกลง ห้ามเปลี่ยนกฎเฉพาะในแชทของตนเอง

## บริบทและขอบเขต

โปรเจกต์จบ ทีม 5 คน ระยะเวลา 4 สัปดาห์ ระบบเดียว ฐานข้อมูลเดียว รองรับหลายโรงแรม
ข้อมูลของแต่ละโรงแรมต้องแยกจากกัน ไม่ใช่ SaaS: ไม่มี self-signup, subdomain หรือ billing

ทำเฉพาะรายการต่อไปนี้:
- Property, Membership และการแยกข้อมูล
- Room Type, Room, Amenities และปฏิทินราคา พร้อม bulk update
- ค้นหาห้องว่างและจองหลายห้อง
- ชำระเงินด้วย PromptPay / Stripe test และอีเมลยืนยัน
- ยกเลิกโดยไม่มีค่าปรับ
- Tape Chart: แสดงผลและลากย้ายห้อง
- Arrival / Departure / In-house และเช็คอิน / เช็คเอาต์
- สถานะห้อง Clean / Dirty และใบเสร็จ PDF
- Dashboard: Occupancy / ADR / RevPAR และเปรียบเทียบโรงแรมตามสิทธิ์

ห้ามเพิ่มฟีเจอร์นอกขอบเขต เช่น walk-in, seasonal rate, ค่าปรับ, add-on,
โค้ดส่วนลด, แก้ไขการจองทั่วไป หรือหลายสกุลเงิน หากจำเป็นให้เสนอเหตุผลให้ทีมพิจารณาก่อน

## Tech stack

- Django 5, Django REST Framework, PostgreSQL 16
- Django templates + HTMX + Alpine.js + Chart.js; ไม่ใช้ React / Vue
- pytest + pytest-django และ factory_boy สำหรับ test data
- Docker Compose
- ไม่มี Celery / Redis; ส่งอีเมลแบบ synchronous
- ห้ามเพิ่ม library ใหม่โดยไม่ได้รับความเห็นชอบจากทีม

## กฎข้อมูลและความปลอดภัย

1. ทุก model ข้อมูลดำเนินงานต้องสืบทอด `PropertyScopedModel` และมี FK ไป `Property`
2. ห้ามเรียก `Model.objects` ตรง ๆ ใน view / service ใช้ scoped manager เช่น
   `Model.scoped.for_property(request.property)` หรือ base view ที่ filter ตามโรงแรมแล้ว
   ตรวจ API จริงใน repo ก่อนใช้ ตัวอย่างนี้ไม่ใช่หลักฐานว่ามี implementation แล้ว
3. เข้าถึง object ของโรงแรมอื่นต้องได้ 404 ไม่ใช่ 403 และต้องไม่เกิดการเปลี่ยนข้อมูล
4. ตรวจ isolation ทุกช่องทาง: query, URL, request body, form choices, serializer,
   related object, ไฟล์อัปโหลด และรายงานข้ามโรงแรม
5. `unique_together` / `UniqueConstraint` ของข้อมูลที่ scoped ตามโรงแรมต้องรวม `property`
   หากเป็นข้อมูลส่วนกลาง ให้ขอให้ทีมยืนยันข้อกำหนดก่อนสร้าง constraint
6. เงินใช้ `Decimal` เท่านั้น ห้ามใช้ float ใน model หรือการคำนวณ;
   ส่งค่าเงินใน JSON เป็น decimal string เพื่อรักษาความแม่นยำ
7. `Property` และ `Booking` ใช้ UUID เป็น primary key
8. การจองต้องใช้ `transaction.atomic()` + `select_for_update()` และมี
   `ExclusionConstraint` ระดับฐานข้อมูลเป็นด่านสุดท้ายป้องกันการจองทับซ้อน
9. ราคารวมคำนวณแบบ per-night จากปฏิทินราคา ห้ามใช้ราคาเดียวคูณจำนวนคืน
10. ธุรกรรมการเงินเป็น append-only: สร้าง record ใหม่ ห้าม update ทับ และต้อง idempotent
11. ห้าม hardcode โดเมน, secret หรือ API key ให้อ่านจาก environment variable
12. ไฟล์อัปโหลดเก็บที่ `properties/<property_id>/<filename>` และตรวจสิทธิ์ตอนเข้าถึง
13. validate ฝั่ง server ทุกครั้ง รวมถึงผลการลาก Tape Chart ห้ามเชื่อค่าจาก frontend

## Roles

`OWNER / MANAGER / FRONT_DESK / HOUSEKEEPING` ผูกผ่าน `Membership(user, property, role)`
ตรวจทั้ง Membership ในโรงแรมที่เข้าถึงและ role ที่เหมาะสม
object ข้ามโรงแรมต้องได้ 404; ผู้ใช้ในโรงแรมเดียวกันที่ role ไม่พอต้องถูกปฏิเสธตามนโยบายสิทธิ์
หากยังไม่มี permission matrix ที่ตกลงกัน ให้ถามก่อนกำหนดสิทธิ์เอง

## โครงสร้างโค้ดและเจ้าของ

ตรวจ repo ณ วันที่ 13 ตุลาคม 2026: มีโฟลเดอร์ app ตามตารางแล้ว และมีโค้ด Property/Membership,
middleware, permissions และ tests ใน apps/properties/ ส่วนความรับผิดชอบในตารางเป็นขอบเขตงาน
ไม่ใช่การยืนยันว่าทุกฟีเจอร์เสร็จแล้ว ตรวจ implementation จริงก่อนเริ่มงานและอัปเดตผ่าน PR
การติดตั้งและคำสั่งรันอยู่ใน README.md; เอกสารต้นฉบับอยู่ใน docs/project/message.txt
ต้นฉบับเก็บเพื่ออ้างอิง หากข้อความขัดกันให้ใช้ AGENTS.md เป็นกฎกลางของทีม

| Path / ส่วนงาน | เนื้อหา | เจ้าของ |
| --- | --- | --- |
| `apps/properties/` | Property, Membership, middleware, scoped manager | A |
| `apps/rooms/` | Room Type, Room, Amenity, Rate | D |
| `apps/bookings/` | Booking, availability engine | A |
| payment ใน `apps/bookings/` | การชำระเงิน | C |
| Tape Chart ใน `apps/frontdesk/` | แสดงผลและลากย้ายห้อง | D |
| check-in/out ใน `apps/frontdesk/` | เช็คอิน / เช็คเอาต์ | A, B |
| `apps/dashboard/` | KPI queries และ views | E |

- business logic อยู่ใน `services.py` ไม่ใช่ view
- AI แก้ `apps/properties/` ได้เฉพาะงานของคน A หากไม่ทราบเจ้าของงานต้องถามก่อนแตะส่วนนี้
- ห้ามแก้ `models.py` ของ app ที่ผู้ออกงานไม่ได้เป็นเจ้าของ ให้ระบุสิ่งที่ต้องเปลี่ยนเพื่อส่งต่อเจ้าของ
- หากไฟล์มีเจ้าของร่วมและขอบเขตไม่ชัด ให้ตกลงผู้รับผิดชอบก่อนแก้

## มาตรฐานและการตรวจสอบ

- ทุกฟีเจอร์มี pytest อย่างน้อย happy path, 1 edge case และ isolation test
- isolation test ตรวจทั้งการอ่านและการเขียน รวมถึง related ID ของโรงแรมอื่น
- ใช้ factory_boy สร้าง test data ไม่ใช้ fixture JSON
- ลด N+1 ด้วย `select_related` / `prefetch_related` ตามความสัมพันธ์จริง
- ข้อความที่ผู้ใช้เห็นเป็นภาษาไทย ชื่อตัวแปรและคอมเมนต์ในโค้ดเป็นภาษาอังกฤษ
- ทุก migration ต้องรันผ่านบนฐานข้อมูลเปล่า
- โค้ดจาก AI ต้องผ่าน test ก่อน merge ไม่มีข้อยกเว้น
- รายงานคำสั่งและผลตรวจจริง หากรันไม่ได้ให้บอกข้อจำกัด ห้ามอ้างว่าผ่าน

## วิธีทำงานร่วมกับ AI

- 1 prompt = 1 งานบน board ระบุเจ้าของ A–E และสัปดาห์ 1–4
- ก่อนเริ่มงานให้อัปเดต main และแยก branch เช่น codex/rooms-models; commit/push เฉพาะ branch งาน แล้วเปิด PR ให้ review ห้าม push ตรง main หรือ merge เองโดยไม่มีคำสั่งจากผู้ใช้
- วางแผนก่อนเขียนเสมอ สำหรับงานที่แตะมากกว่า 2 ไฟล์ ต้องเสนอแผนและรอยืนยันก่อนแก้
  หาก template ของงานกำหนดให้รอยืนยัน ให้ทำตามแม้แตะไม่เกิน 2 ไฟล์
- เขียนเฉพาะไฟล์ที่เกี่ยวข้อง ไม่ refactor ส่วนอื่น
- ระบุ path ของทุกไฟล์ที่สร้างหรือแก้ และคำสั่ง migration หากเกี่ยวข้อง
- ถ้าโจทย์กำกวมหรือขัดกับกฎ ให้ถามเฉพาะประเด็นที่จำเป็นก่อนทำส่วนที่ขึ้นกับคำตอบ
- ห้ามวาง secret, `.env` หรือข้อมูลแขกจริงลงในแชท ใช้ข้อมูลสมมติหรือข้อมูลที่ลบตัวระบุแล้ว
- หาก AI เสนอ library ใหม่หรือฟีเจอร์นอกขอบเขต ให้หยุดส่วนดังกล่าวและถามทีมก่อน
- ผู้รับผิดชอบต้องอ่านโค้ดทุกบรรทัดก่อน commit และอธิบายโค้ดในพรีเซนต์ได้

## การใช้กับเครื่องมืออื่น

ทีมนี้ใช้ `AGENTS.md` เป็นไฟล์หลักที่ root ของ repo
หากใช้ Claude Code ให้มี `CLAUDE.md` ที่สั่งให้อ่าน `AGENTS.md` ก่อนทุกงาน;
หากใช้ Cursor ให้ตั้ง project rule ให้อ่านไฟล์เดียวกัน ไม่คัดลอกกฎไปดูแลหลายชุด
หากใช้แชทธรรมดา ให้วางเนื้อหา `AGENTS.md` ต้นแชททุกครั้ง ตามด้วย template ของงาน
ตรวจว่าเครื่องมืออ่านบริบทแล้วก่อนเริ่มงาน
