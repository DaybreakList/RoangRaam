# RoangRaem

ระบบจัดการโรงแรมแบบ multi-property (CN331)

**Stack:** Django 5.2 · DRF · PostgreSQL 16 · pytest + pytest-django + factory_boy · Django templates + HTMX + Alpine.js + Chart.js · Docker Compose

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

docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py createsuperuser
```

เปิด http://localhost:8000/admin/

> ค่าใน `.env` ห้ามมีตัว `$` เพราะ Docker Compose จะอ่านเป็นตัวแปร
> สร้าง secret key ได้ด้วย `python -c "import secrets; print(secrets.token_urlsafe(50))"`

## คำสั่งที่ใช้บ่อย

```bash
docker compose up -d                                   # เปิด
docker compose down                                    # ปิด
docker compose logs -f web                             # ดู log
docker compose exec web python manage.py makemigrations
docker compose exec web python manage.py migrate
docker compose exec web pytest                         # รันเทส
docker compose exec web pytest --create-db             # รันเทสหลังแก้ migration
```

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
apps/frontdesk/    Tape Chart (D), check-in/out (A, B)
apps/dashboard/    KPI queries + views                                (E)
templates/         base.html (โหลด HTMX / Alpine.js / Chart.js แล้ว)
static/            CSS / JS / รูป ที่ใช้ร่วมกัน
```

- business logic อยู่ใน `services.py` ของแต่ละ app
- เทสอยู่ใน `apps/<app>/tests/test_*.py`

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
