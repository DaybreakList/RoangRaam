# RoangRaam

[![CI](https://github.com/DaybreakList/RoangRaam/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/DaybreakList/RoangRaam/actions/workflows/ci.yml)

ระบบจัดการโรงแรมแบบ multi-property (CN331): ระบบเดียว ฐานข้อมูลเดียว รองรับหลายโรงแรม และข้อมูลของแต่ละโรงแรมแยกจากกัน

**Stack:** Django 5.2 · DRF · PostgreSQL 16 · pytest + pytest-django + factory_boy · Django templates + HTMX + Alpine.js + Chart.js · Docker Compose · Render

## ระบบจริง

- **หน้าจองของแขก:** https://roangraam.onrender.com/p/demo-riverside/search/
- **เข้าสู่ระบบพนักงาน:** https://roangraam.onrender.com/accounts/login/

Render แพ็กเกจฟรี เปิดครั้งแรกหลังไม่มีคนใช้อาจรอประมาณ 1 นาที (หน้า `/` เปล่าได้ 404 เพราะทุกหน้าอยู่ใต้ `/p/<slug>/` หรือ `/accounts/`)

### บัญชีเดโม

รหัสผ่านไม่ได้อยู่ใน repo ขอจากทีม

| อีเมล | Role | โรงแรม |
| --- | --- | --- |
| `owner@example.com` | OWNER | ทั้งสองโรงแรม |
| `manager.riverside@example.com`, `manager.hilltop@example.com` | MANAGER | Riverside, Hilltop |
| `frontdesk.riverside@example.com`, `frontdesk.hilltop@example.com` | FRONT_DESK | Riverside, Hilltop |
| `housekeeping.riverside@example.com`, `housekeeping.hilltop@example.com` | HOUSEKEEPING | Riverside, Hilltop |

หน้าที่ควรลองเปิด:

| หน้า | URL |
| --- | --- |
| ค้นหาและจองห้อง (แขก ไม่ต้องล็อกอิน) | `/p/demo-riverside/search/` |
| ดูและยกเลิกการจอง (แขก) | `/p/demo-riverside/my-booking/` |
| รายการวันนี้ (เช็คอิน / เช็คเอาต์) | `/p/demo-riverside/frontdesk/today/` |
| Tape Chart | `/p/demo-riverside/frontdesk/tape-chart/` |
| สถานะห้อง | `/p/demo-riverside/frontdesk/rooms/` |
| Dashboard KPI / เทียบโรงแรม | `/p/demo-riverside/dashboard/`, `/dashboard/compare/` |

ชำระออนไลน์เป็น Stripe โหมดทดสอบ ใช้บัตร `4242 4242 4242 4242` วันหมดอายุในอนาคตและ CVC อะไรก็ได้
อีเมลยืนยันแสดงใน Logs ของ Render เพราะแพ็กเกจฟรีส่งอีเมลจริงไม่ได้

## ความสามารถของระบบ

- **แขก:** ค้นหาห้องว่างตามวันที่และจำนวนผู้เข้าพัก, จองหลายห้องหลายประเภทในบิลเดียว, ราคาคิดทีละคืนตามปฏิทินราคา,
  จ่ายออนไลน์ (Stripe) หรือจ่ายที่โรงแรม, อีเมลยืนยัน, ดูและยกเลิกการจองด้วยรหัสการจองและอีเมลโดยไม่มีค่าปรับ
- **หน้าเคาน์เตอร์:** รายการเข้าพัก / ออก / พักอยู่วันนี้, เช็คอินพร้อมบันทึกบัตรประชาชนหรือพาสปอร์ต,
  Folio รับชำระและปิดบิล, ใบเสร็จ PDF, เช็คเอาต์แล้วห้องเป็น Dirty, Tape Chart ลากย้ายห้องโดย server ตรวจซ้ำ
- **แม่บ้าน:** สถานะห้อง Clean / Dirty
- **ผู้จัดการและเจ้าของ:** ประเภทห้อง ห้อง สิ่งอำนวยความสะดวก ปฏิทินราคาพร้อม bulk update, ตั้งค่าโรงแรม (โลโก้ เวลา VAT นโยบาย),
  Dashboard Occupancy / ADR / RevPAR พร้อมกราฟรายได้รายวัน และเทียบหลายโรงแรมสำหรับเจ้าของ
- **ความปลอดภัย:** สิทธิ์ 4 role ต่อโรงแรม, ข้อมูลโรงแรมอื่นได้ 404, กันจองซ้อนถึงระดับฐานข้อมูล,
  เงินเป็น Decimal และรายการเงินแก้ไขไม่ได้, webhook ส่งซ้ำไม่ตัดเงินซ้ำ

## เอกสาร

| เอกสาร | เนื้อหา |
| --- | --- |
| [docs/user-guide/USER_GUIDE.md](docs/user-guide/USER_GUIDE.md) | คู่มือการใช้งานสำหรับแขกและพนักงานแต่ละบทบาท |
| [docs/architecture/README.md](docs/architecture/README.md) | แผนภาพ C4 (Context, Container) และโครงสร้าง app |
| [docs/architecture/ERD.md](docs/architecture/ERD.md) | ERD และ constraint สำคัญ |
| [docs/adr/](docs/adr/README.md) | Architecture Decision Records |
| [docs/project/API_CONTRACT.md](docs/project/API_CONTRACT.md) | JSON API และ URL ของแต่ละส่วน |
| [docs/project/PERMISSIONS.md](docs/project/PERMISSIONS.md) | ตารางสิทธิ์ของทุก URL ตาม role |
| [docs/project/DEPLOY.md](docs/project/DEPLOY.md) | ตั้งค่า Render, ข้อจำกัดของแพ็กเกจฟรี, การรีเซ็ตข้อมูลเดโม |
| [docs/project/UAT_SCENARIOS.md](docs/project/UAT_SCENARIOS.md) | สถานการณ์ทดสอบกับผู้ใช้ (UAT) |
| [docs/project/TAPE_CHART.md](docs/project/TAPE_CHART.md), [ROOM_MODELS.md](docs/project/ROOM_MODELS.md), [ROOM_PRICING.md](docs/project/ROOM_PRICING.md) | รายละเอียดของแต่ละส่วน |
| [AGENTS.md](AGENTS.md) | กฎกลางของทีม (ขอบเขต, กฎข้อมูลและความปลอดภัย, เจ้าของโค้ด) |

**Project board:** https://github.com/users/DaybreakList/projects/4 (milestone Sprint 1–4 อยู่ใน [Issues](https://github.com/DaybreakList/RoangRaam/milestones))

## Definition of Done

งานหนึ่งชิ้น (issue) นับว่าเสร็จเมื่อครบทุกข้อ:

1. ทำครบตาม Acceptance Criteria ของ issue หรือเขียนหมายเหตุในส่วนที่ทีมตกลงให้ต่างออกไป
2. เปิด PR จาก branch งาน ผูก issue (`Closes #N` / `Refs #N`) และได้ approve จากสมาชิกคนอื่นอย่างน้อย 1 คน
3. CI ผ่านทั้งหมด: ruff, `manage.py check`, `makemigrations --check`, pytest บน PostgreSQL และ coverage ไม่ต่ำกว่า 80%
4. มีเทสต์อย่างน้อย happy path, 1 edge case และ isolation test (ข้อมูลโรงแรมอื่นต้องได้ 404 และไม่ถูกแก้)
5. ทำตามกฎใน `AGENTS.md`: ใช้ scoped manager, เงินเป็น Decimal, ข้อความที่ผู้ใช้เห็นเป็นภาษาไทย, ไม่ hardcode secret
6. แก้เอกสารที่เกี่ยวข้องใน PR เดียวกัน (เช่น `PERMISSIONS.md` เมื่อเพิ่ม URL, `API_CONTRACT.md` เมื่อเพิ่ม API)
7. merge เข้า `main` แล้ว deploy ขึ้น Render และปิด issue

`main` ถูกป้องกัน: ต้องผ่าน check `test` และ branch ต้องเป็นล่าสุดก่อน merge

## Requirements
- Docker Desktop
- Git
- Python 3.12 (ถ้าอยากมี venv บนเครื่องไว้ให้ editor ทำ autocomplete)

## Setup (ครั้งแรก)

```bash
git clone https://github.com/DaybreakList/RoangRaam.git
cd RoangRaam

# สร้าง .env จากตัวอย่าง แล้วแก้ DJANGO_SECRET_KEY และ POSTGRES_PASSWORD
copy .env.example .env        # Windows
cp .env.example .env          # macOS / Linux

docker compose up -d --build          # รัน migrate ให้อัตโนมัติทุกครั้งที่เปิด
docker compose exec web python manage.py createsuperuser
```

เปิด http://localhost:8000/admin/

> ค่าใน `.env` ห้ามมีตัว `$` เพราะ Docker Compose จะอ่านเป็นตัวแปร
> สร้าง secret key ได้ด้วย `python -c "import secrets; print(secrets.token_urlsafe(50))"`

### ข้อมูลเดโม

โรงแรม 2 แห่ง (`demo-riverside` 12 ห้อง, `demo-hilltop` 10 ห้อง) พร้อมห้อง ราคา พนักงาน และการจองย้อนหลัง
วันที่ทั้งหมดนับจากวันที่สร้าง ในวันที่สร้างจึงมีแขกเข้าพัก ออก และพักอยู่ "วันนี้" ครบ
ถ้าสร้างไว้ตั้งแต่วันก่อน ให้รีเซ็ตข้อมูลก่อนเดโมหรือทดสอบ

- ตั้ง `SEED_DEMO_PASSWORD` (รหัสผ่านของบัญชีเดโม) และ `SEED_ON_START=true` ใน `.env` แล้วรัน `docker compose up -d` อีกครั้ง
- หรือรันเอง `docker compose exec web python manage.py seed_demo`
- สร้างใหม่ให้เป็นของวันนี้: `docker compose exec web python manage.py seed_demo --reset` (ลบเฉพาะโรงแรมเดโม)
- บน Render ไม่มี Shell ให้ใช้ตัวแปร `SEED_RESET_ON_START` ตาม [DEPLOY.md](docs/project/DEPLOY.md)

## คำสั่งที่ใช้บ่อย

```bash
docker compose up -d                                   # เปิด
docker compose down                                    # ปิด
docker compose logs -f web                             # ดู log
docker compose exec web python manage.py makemigrations
docker compose exec web python manage.py migrate
docker compose exec web pytest                         # รันเทส
docker compose exec web pytest --create-db             # รันเทสหลังแก้ migration
docker compose exec web coverage run -m pytest         # รันเทสพร้อมวัด coverage
docker compose exec web coverage report                # สรุป coverage (ต่ำกว่า 80% ไม่ผ่าน เหมือน CI)
docker compose exec web coverage html                  # รายงานแบบเว็บ เปิด htmlcov/index.html
```

ถ้า container บอกว่าไม่มีคำสั่ง `coverage` ให้ build ใหม่ด้วย `docker compose up -d --build`

## เทสต์และคุณภาพโค้ด

ณ 6 พ.ย. 2026: **1,474 tests ผ่าน, coverage 97%** (วัดแบบ branch ด้วย ตั้งค่าใน `.coveragerc`)
ตัวเลขล่าสุดอยู่ในหน้า Summary ของ run ล่าสุดใน [Actions](https://github.com/DaybreakList/RoangRaam/actions/workflows/ci.yml)

CI (`.github/workflows/ci.yml`) รันทุก PR และทุก commit บน `main`:

1. เทสต์ JavaScript ของการลากใน Tape Chart (`node --test`)
2. `ruff check .` (กฎใน `ruff.toml`)
3. `python manage.py check` และ `makemigrations --check` (ลืม commit migration จะไม่ผ่าน)
4. `coverage run -m pytest --create-db` บน PostgreSQL 16 จากฐานข้อมูลเปล่า แล้ว `coverage report` (ต่ำกว่า 80% ไม่ผ่าน)

เทสต์ที่สำคัญ: จองห้องสุดท้ายพร้อมกัน 50 คำขอได้สำเร็จ 1 รายการ (`apps/bookings/tests/test_concurrency.py`),
ไล่ทุก URL ด้วยผู้ใช้ของโรงแรมอื่น (`apps/accounts/tests/test_isolation_sweep.py`),
ตรวจสิทธิ์ทุก URL ตาม role (`apps/properties/tests/test_permission_matrix.py`) และนับจำนวน query กัน N+1

## Deploy

Render Web Service (Docker) + Render PostgreSQL 16, region Singapore
ทุก commit เข้า `main` deploy ใหม่เอง ตอนเริ่ม container `scripts/start.sh` รัน migrate, สร้างข้อมูลเดโม (ถ้าตั้งไว้),
สร้างบัญชี admin แล้วเปิด gunicorn ค่าลับทั้งหมดอยู่ใน Environment ของ Render เท่านั้น
ขั้นตอนทั้งหมดอยู่ใน [docs/project/DEPLOY.md](docs/project/DEPLOY.md)

## venv บนเครื่อง (ไม่บังคับ ใช้ให้ editor รู้จัก package)

```bash
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

ถ้าจะรัน `manage.py` จากเครื่องตรง ๆ ต้องเปิด container `db` ไว้ และใช้ `POSTGRES_HOST=localhost` ใน `.env`

## โครงสร้างโปรเจกต์

```
config/            settings, urls หลัก
apps/properties/   Property, Membership, middleware, scoped manager   (A)
apps/rooms/        Room Type, Room, Amenity, Rate                     (D)
apps/bookings/     Booking, availability engine (A), payment (C)
apps/frontdesk/    Tape Chart (D), check-in/out (A, B), รายการวันนี้ (E)
apps/dashboard/    KPI queries + views, seed_demo                     (E)
apps/accounts/     login, เลือกโรงแรม, ตั้งค่าโรงแรม
templates/         base.html (โหลด HTMX / Alpine.js แล้ว; Chart.js โหลดเฉพาะหน้า dashboard)
static/            CSS / JS / รูป ที่ใช้ร่วมกัน
docs/              สถาปัตยกรรม, ADR, เอกสารแต่ละส่วน
scripts/start.sh   คำสั่งเริ่ม container บน Render
```

- business logic อยู่ใน `services.py` ของแต่ละ app
- เทสอยู่ใน `apps/<app>/tests/test_*.py`

## URL และสิทธิ์ของแต่ละโรงแรม

ทุกหน้าของโรงแรมอยู่ใต้ `/p/<property_slug>/...` แล้ว `PropertyMiddleware` จะ
- ตั้ง `request.property` และ `request.membership` ให้
- ตอบ **404** ถ้าไม่มีโรงแรมนี้ หรือ user ไม่ใช่สมาชิกของโรงแรมนี้
- ส่งไปหน้า login ถ้ายังไม่ได้ล็อกอิน

```python
# urls.py ของ app
path('p/<slug:property_slug>/rooms/', views.room_list, name='room_list')
```

```python
from apps.properties.permissions import (
    Role, role_required, RoleRequiredMixin,
    public_property_view, PublicPropertyViewMixin,
    PropertyScopedQuerysetMixin,
)

@role_required(Role.OWNER, Role.MANAGER)           # role ไม่พอ → 403
def rate_update(request): ...

class RoomDetail(PropertyScopedQuerysetMixin, RoleRequiredMixin, DetailView):
    model = Room
    allowed_roles = (Role.OWNER, Role.MANAGER, Role.FRONT_DESK)
    # ห้องของโรงแรมอื่น → 404 อัตโนมัติ

@public_property_view                               # หน้าสำหรับลูกค้า ไม่ต้องล็อกอิน
def search(request): ...
```

- ไฟล์อัปโหลดต้องผ่าน `ModelForm` หรือ DRF serializer เสมอ เพราะ Pillow ตรวจว่าไฟล์เป็นรูปจริงเฉพาะในขั้นนั้น

## ติดตั้ง package เพิ่ม
ต้องตกลงกับทีมก่อนทุกครั้ง แล้ว:

```bash
pip install <package>
pip freeze > requirements.txt
docker compose up -d --build
```

## หมายเหตุ
- ห้าม commit `.env` (อยู่ใน `.gitignore` แล้ว)
- commit ไฟล์ migrations ทุกครั้งที่แก้ model

## ทีม: กลุ่มเส้นเซวิก

| | รหัสนักศึกษา | ชื่อ | GitHub | รับผิดชอบหลัก |
| --- | --- | --- | --- | --- |
| A | 6710685030 | ปัณณวิชญ์ สุทธิโมกข์ | [@Gj-345](https://github.com/Gj-345) | Property / Membership และการแยกข้อมูล, ค้นหาและจองห้อง, service เช็คอิน / เช็คเอาต์, สถานะห้อง, ตรวจสิทธิ์ทุก endpoint (permission matrix), สถานการณ์ UAT |
| B | 6710615037 | กิตติกานต์ เมธีกุลสุเมธ | [@hansera1](https://github.com/hansera1) | design system และ template กลาง, หน้าจองของแขก, หน้าจอเช็คอิน / เช็คเอาต์, หน้าจอแท็บเล็ต |
| C | 6710615227 | รวีโรจน์ มานะคิด | [@Rawirot-ux](https://github.com/Rawirot-ux) | ชำระเงิน Stripe และ webhook, อีเมลยืนยัน, Folio และใบเสร็จ PDF, ตั้งค่าโรงแรม |
| D | 6710615078 | ณพัฐกรภ์ จิรานนท์อัครโชค | [@6710615078](https://github.com/6710615078) | ประเภทห้อง ห้อง และปฏิทินราคา, Tape Chart และการลากย้ายห้อง |
| E | 6710615193 | ภูริภัทร สายเนตร | [@DaybreakList](https://github.com/DaybreakList) | Dashboard KPI, รายการวันนี้, ข้อมูลเดโม, CI, deploy, เอกสารระบบ |
