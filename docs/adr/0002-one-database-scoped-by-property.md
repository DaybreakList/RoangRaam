# ADR 0002: ฐานข้อมูลเดียว แยกข้อมูลแต่ละโรงแรมด้วย `PropertyScopedModel`

- **สถานะ:** ใช้อยู่
- **วันที่:** ตุลาคม 2026 (Sprint 1) ข้อยกเว้นเพิ่มใน #139 (31 ต.ค.)

## บริบท

ระบบรองรับหลายโรงแรม เจ้าของหนึ่งคนอาจมีหลายโรงแรม และพนักงานอาจทำงานหลายแห่งด้วย role ต่างกัน
ข้อมูลของโรงแรมหนึ่งต้องไม่รั่วไปอีกโรงแรม ทั้งทาง query, URL, body ของ request, ตัวเลือกในฟอร์ม และรายงาน
ระบบนี้ไม่ใช่ SaaS (ไม่มีสมัครเอง, subdomain หรือ billing) และมีเวลาจำกัด การแยกฐานข้อมูลหรือ schema ต่อโรงแรมจึงหนักเกินไป

## การตัดสินใจ

- ใช้ฐานข้อมูล PostgreSQL เดียว ทุก model ข้อมูลดำเนินงานสืบทอด `PropertyScopedModel`
  (`apps/properties/models.py`) ซึ่งมี FK `property` และ manager `scoped`
- view และ service ต้อง query ผ่าน `Model.scoped.for_property(request.property)` เท่านั้น
  `Model.objects` ใช้ได้เฉพาะ admin, migration และเทสต์ ยกเว้น `Property` และ `Membership`
  ซึ่งเป็นข้อมูลส่วนกลางและต้องกรองด้วย slug, user หรือข้อมูลที่ตรวจแล้ว
- โรงแรมมาจาก slug ใน URL (`/p/<slug>/...`) เสมอ `PropertyMiddleware` ตั้ง `request.property` และ `request.membership`
  ไม่รับ `property_id` จาก body
- สิทธิ์ผูกผ่าน `Membership(user, property, role)` ตาราง role ต่อ URL อยู่ใน `docs/project/PERMISSIONS.md`
- เข้าถึง object ของโรงแรมอื่นได้ **404** ไม่ใช่ 403 เพื่อไม่บอกว่ามี object นั้นอยู่ ส่วน role ไม่พอในโรงแรมตัวเองได้ 403
- unique constraint ของข้อมูลที่ scoped ต้องรวม `property` และ model ตรวจว่า FK ที่เกี่ยวข้องเป็นของโรงแรมเดียวกัน

## ผลที่ตามมา

- **ดี:** โครงสร้างเดียวกันทั้งระบบ เพิ่มโรงแรมใหม่ไม่ต้อง migrate อะไร และรายงานข้ามโรงแรม (หน้าเทียบ KPI ของเจ้าของ) ทำได้จากฐานข้อมูลเดียว
  (`hotel_comparison()` ใช้ 2 query ต่อโรงแรม)
- **ดี:** ตรวจได้ด้วยเทสต์อัตโนมัติ `apps/accounts/tests/test_isolation_sweep.py` ไล่ทุก route ที่ต้องล็อกอินด้วยผู้ใช้ของโรงแรมอื่น
  และ `apps/properties/tests/test_permission_matrix.py` ตรวจ role ทุก URL ตาม `PERMISSIONS.md`
- **ข้อเสีย:** ความปลอดภัยขึ้นกับวินัยของโค้ด ถ้าใครเรียก `Model.objects` ใน view ข้อมูลจะรั่วได้
  จึงต้องมีกฎใน `AGENTS.md`, การ review และเทสต์ isolation ทุกฟีเจอร์
- **ข้อเสีย:** ไฟล์อัปโหลดอยู่ใน `MEDIA_ROOT` ที่เปิดได้โดยตรง จึงเก็บได้เฉพาะไฟล์สาธารณะ (โลโก้, รูปห้อง)
  ใบเสร็จ PDF สร้างใหม่ทุกครั้งผ่าน view ที่ตรวจสิทธิ์และไม่เก็บเป็นไฟล์ (AGENTS.md ข้อ 12)
