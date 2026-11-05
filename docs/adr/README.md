# Architecture Decision Records

บันทึกการตัดสินใจทางสถาปัตยกรรมที่มีผลกับทั้งระบบ แต่ละไฟล์บอกบริบท สิ่งที่ตัดสินใจ และผลที่ตามมา
เปลี่ยนการตัดสินใจเดิมให้เพิ่ม ADR ใหม่แล้วแก้สถานะของฉบับเดิมเป็น "ถูกแทนที่โดย ADR xxxx" ไม่แก้เนื้อหาเดิม

| # | เรื่อง | สถานะ |
| --- | --- | --- |
| [0001](0001-modular-monolith.md) | Django monolith แบ่งตาม app และ service ไม่ทำ SPA | ใช้อยู่ |
| [0002](0002-one-database-scoped-by-property.md) | ฐานข้อมูลเดียว แยกข้อมูลแต่ละโรงแรมด้วย `PropertyScopedModel` | ใช้อยู่ |
| [0003](0003-prevent-double-booking.md) | กันจองซ้อนด้วย row lock และ ExclusionConstraint | ใช้อยู่ |
| [0004](0004-money-and-append-only-payments.md) | เงินเป็น Decimal, ราคารายคืน, รายการเงินแบบเพิ่มอย่างเดียว | ใช้อยู่ |
| [0005](0005-production-on-render.md) | ระบบจริงบน Render แพ็กเกจฟรี แทน Azure VM | ใช้อยู่ |

แผนภาพระบบอยู่ใน [docs/architecture](../architecture/README.md)
