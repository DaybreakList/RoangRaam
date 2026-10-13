# บันทึกทีมจากภาพแนบ

บันทึกวันที่ 13 ตุลาคม 2026 จากภาพ 2 ภาพที่ผู้ใช้ส่งในแชท
เทียบกับ checkout commit `7b3a6c0` เท่านั้น ไม่ได้ตรวจสถานะ PR บน GitHub หรือฐานข้อมูลของสมาชิกทีม
ข้อความในภาพเป็นข้อมูลอ้างอิง ไม่ใช่คำสั่งให้ AI merge PR, รีเซ็ต DB หรือส่งข้อความถึงทีมโดยอัตโนมัติ
หากบันทึกนี้ขัดกับกฎกลาง ให้ยึด AGENTS.md จนกว่าทีมจะตกลงเปลี่ยนกฎผ่าน PR

## ข้อมูลที่ภาพระบุ

ภาพที่ 1:
- ให้เพื่อน review และ merge PR; D ยังเริ่มเขียน Room ไม่ได้จนกว่าจะ merge
- หลัง merge ให้ทุกคนรีเซ็ต DB ด้วย `docker compose down -v` แล้ว migrate ใหม่ เพราะเปลี่ยน User model
- ขออนุมัติ Pillow สำหรับรูปภาพ โดยตอนที่เขียนข้อความนั้น logo ใช้ FileField ชั่วคราว
- ลบ currency ออกจาก ER
- รอตัดสินใจเรื่องเลือกโรงแรมผ่าน URL หรือ session
- รอตัดสินใจว่า Payment.status ให้สร้าง record ใหม่หรือแก้ไขได้
- รอตรวจ unique ที่ต้องรวม property และ unique ที่ยังขาด

ภาพที่ 2:
- แจ้งทีมให้รัน `docker compose up -d --build` ใหม่เพื่อติดตั้ง Pillow
- ยังไม่มีใครรับทำหน้า login ตามข้อความในภาพ

## สิ่งที่ตรวจพบใน checkout ปัจจุบัน

| ประเด็น | หลักฐานใน repo | สถานะที่สรุปได้ |
| --- | --- | --- |
| Pillow / logo | requirements.txt มี pillow==12.3.0; Property.logo เป็น ImageField; migration 0002_alter_property_logo.py เปลี่ยนจาก FileField | โค้ดรองรับแล้ว แต่ไม่ได้ยืนยันการอนุมัติของทีมหรือว่าแต่ละคน rebuild แล้ว |
| User model | config/settings.py ตั้ง AUTH_USER_MODEL เป็น properties.User; User ใช้ email ล็อกอิน | มี custom User แล้ว ไม่ได้ยืนยันว่าเครื่องใดต้องรีเซ็ต DB |
| เลือกโรงแรม | PropertyMiddleware อ่าน property_slug จาก URL; README ระบุ /p/<property_slug>/... | implementation ปัจจุบันใช้ URL; ข้อตกลงสุดท้ายของทีมยังไม่ปรากฏในข้อมูลที่ให้ |
| Payment.status | apps/bookings/models.py ยังเป็น scaffold ไม่มี Payment | ยังตรวจวิธีเปลี่ยนสถานะจริงไม่ได้; กฎกลางยังคง append-only และ idempotent |
| currency | Property ไม่มี field currency | ไม่ได้แนบ ER Diagram จึงยังยืนยันว่าลบจาก ER แล้วไม่ได้ |
| unique | Membership มี UniqueConstraint(user, property); User.email และ Property.slug unique ระดับส่วนกลาง | ยังตรวจ constraint ของโมเดลที่ไม่ได้สร้างไม่ได้; ห้ามเติม property ให้ unique ส่วนกลางโดยอัตโนมัติ |
| Room / PR | apps/rooms/models.py ยังเป็น scaffold | ยังไม่ทราบ PR ที่อ้างถึงและสถานะ merge ห้ามสรุปว่า D เริ่มงานได้แล้ว |
| หน้า login | config/urls.py มีเส้นทาง admin; templates มีเพียง base.html; middleware redirect ไป login | ยังไม่พบหน้า login สำหรับแอปนอก admin; เจ้าของงานปัจจุบันต้องให้ทีมยืนยัน |

## งานที่ต้องติดตามกับทีม

- ระบุ PR ที่เป็น dependency ของ Room และตรวจ review / merge ก่อนแจ้ง D
- ยืนยันเจ้าของงานหน้า login และเกณฑ์รับงานผ่าน board
- ยืนยันแนวทาง URL / session ให้ตรงกับ implementation
- ตกลงวงจรสถานะ Payment โดยยังรักษากฎ append-only และ idempotency หรือแก้กฎผ่าน PR ก่อน
- ตรวจ ER และ unique constraints ร่วมกับเจ้าของแต่ละ model
- ให้สมาชิกตรวจว่ามี dependency และ migration ล่าสุดแล้ว

คำสั่ง `docker compose down -v` ในภาพลบ volumes ของ Compose และอาจทำให้ข้อมูลฐานข้อมูลสูญหาย
บันทึกนี้ไม่ได้อนุมัติให้รัน ต้องตรวจข้อมูลที่ต้องเก็บและได้รับคำสั่งชัดเจนสำหรับการรีเซ็ตก่อน
สำหรับการติดตั้ง Pillow ภาพระบุ `docker compose up -d --build`; ยังไม่ได้รันคำสั่งนี้ในงานบันทึกเอกสาร

## อัปเดตจากข้อมูลและการตรวจภายหลัง (13 ตุลาคม 2026)

ส่วนนี้อัปเดตข้อที่ยังไม่ทราบในบันทึกด้านบน:
- ตรวจ remote main แล้วตรงกับ local ที่ 7b3a6c0; ประวัติมี merge PR #23 และ #24 แล้ว
- ผู้ใช้ส่ง ER เต็มและข้อกำหนด constraints เพิ่มแล้ว งาน model ของ D ยึด RoomType,
  Room, Amenity, RoomTypeAmenity, RoomTypeImage และ DailyRate ตาม ER
- ภาพข้อกำหนดเพิ่มเติมระบุ assign ห้องจริงตั้งแต่จอง, snapshot ราคาที่ BookingNightRate,
  Payment เป็น append-only และยกเลิกผ่าน BookingRoom.is_active = false
- ER ที่ส่งมายังแสดง Property.currency แต่โค้ดและข้อความก่อนหน้าระบุให้นำออก จึงไม่เพิ่มกลับ
- D เริ่มงานบน branch codex/rooms-models แล้ว ดูรายละเอียดและผลตรวจใน ROOM_MODELS.md
- งาน Auth/Role และตัวสลับโรงแรมเป็นขอบเขต C ตาม Sprint 1; ยังต้องประสานผู้รับงานหน้า login
