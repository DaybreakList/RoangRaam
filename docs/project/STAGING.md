# Staging บน Azure (TASK-03)

ทุก merge เข้า `main` ที่ CI ผ่าน จะ build image, push ขึ้น GHCR แล้ว deploy ขึ้น VM โดยอัตโนมัติ
job `deploy` ทำงานเมื่อ repo variable `STAGING_DEPLOY_ENABLED` เป็น `true` เท่านั้น
ถ้าปิดอยู่ job จะถูกข้ามและไม่กระทบ CI

```text
merge เข้า main -> test -> build และ push image (<sha>, latest)
                -> SSH ไป VM -> scripts/deploy_staging.sh <sha> -> ตรวจหน้า login ผ่าน HTTPS
```

- ค่าลับทั้งหมดอยู่บน VM หรือใน GitHub เท่านั้น repo นี้เป็น public ห้าม commit `.env.production` หรือ private key
- ตัวอย่างด้านล่างใช้ `<host>` แทนชื่อโดเมนของ VM และ `<user>` แทนผู้ใช้บน VM ที่อยู่ในกลุ่ม `docker`
- Migration รันอัตโนมัติทุกครั้งที่ container `web` เริ่มทำงาน

## ตั้งค่าครั้งแรก

ทำครั้งเดียว ตามลำดับนี้ โดยเปิด flag เป็นขั้นสุดท้าย

### 1. เตรียม VM

```bash
sudo mkdir -p /opt/roangraam /opt/roangraam-backups
sudo chown <user>: /opt/roangraam /opt/roangraam-backups
git clone https://github.com/DaybreakList/RoangRaam.git /opt/roangraam
cd /opt/roangraam
cp .env.production.example .env.production
chmod 600 .env.production
nano .env.production
```

แทนค่า `change-me` และ `<...>` ทุกตัวในไฟล์ แล้วสร้างค่าลับบน VM ด้วยคำสั่งนี้ ไม่ต้องคัดลอกผ่านแชท

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(50))"
```

### 2. สร้าง deploy key

สร้างบนเครื่องของตัวเอง นอกโฟลเดอร์ repo และใช้กับงานนี้เท่านั้น
บน Windows ให้รันคำสั่งบนเครื่องตัวเองในขั้น 2 และ 3 ด้วย Git Bash ไม่ใช่ PowerShell
เพราะ PowerShell ไม่รองรับ `<`, อาจเขียนไฟล์จาก `>` เป็น UTF-16 และอาจส่ง `-N ""` ไม่ถูกต้อง

```bash
ssh-keygen -t ed25519 -N "" -C github-actions-staging -f ~/.ssh/roangraam_staging_deploy
```

บน VM เพิ่มบรรทัดนี้ใน `~/.ssh/authorized_keys` โดยแทน `<public key>` ด้วยเนื้อหาไฟล์ `.pub`
`command=` ทำให้ key นี้รันได้เฉพาะสคริปต์ deploy

```text
command="/bin/bash /opt/roangraam/scripts/deploy_staging.sh",restrict <public key>
```

เก็บ key เดิมที่ใช้ login ไว้ อย่าลบ

### 3. ตั้งค่า GitHub

ใน Settings > Environments สร้าง environment ชื่อ `staging`
แล้วตั้ง Deployment branches เป็น Selected branches: `main`
ขั้นนี้กันไม่ให้ PR ที่แก้ workflow อ่าน secret ได้

```bash
ssh-keyscan -t ed25519 <host> > staging_known_hosts   # เทียบ fingerprint ด้านล่างก่อนใช้
ssh-keygen -lf staging_known_hosts                    # รันบนเครื่องตัวเอง
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub      # รันบน VM ค่าต้องตรงกัน

gh secret set STAGING_SSH_KEY --env staging < ~/.ssh/roangraam_staging_deploy
gh secret set STAGING_SSH_KNOWN_HOSTS --env staging < staging_known_hosts
gh variable set STAGING_HOST --env staging --body "<host>"
gh variable set STAGING_SSH_USER --env staging --body "<user>"
```

### 4. Deploy ครั้งแรก

```bash
gh variable set STAGING_DEPLOY_ENABLED --body true
gh workflow run CI --ref main
```

Image ถูกสร้างเป็น package ใหม่ ถ้าขั้น deploy ล้มเหลวเพราะ VM ดึง image ไม่ได้
ให้เปิดหน้า package `roangraam` บน GitHub แล้วตั้ง visibility เป็น Public จากนั้นสั่ง `gh workflow run` อีกครั้ง

### 5. หลัง deploy ครั้งแรก

รันบน VM ในโฟลเดอร์ `/opt/roangraam`

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml exec web python manage.py seed_demo
```

เพิ่มงานประจำด้วย `crontab -e`

```cron
*/5 * * * * cd /opt/roangraam && docker compose --env-file .env.production -f docker-compose.prod.yml exec -T web python manage.py release_expired_bookings >> /opt/roangraam-backups/release.log 2>&1
0 3 * * * cd /opt/roangraam && docker compose --env-file .env.production -f docker-compose.prod.yml exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' | gzip > /opt/roangraam-backups/db-$(date +\%F).sql.gz && find /opt/roangraam-backups -name 'db-*.sql.gz' -mtime +7 -delete
```

จากนั้นวัดหน่วยความจำด้วย `free -m` และ `docker stats --no-stream`
ถ้าไม่พอ ให้ปรับ VM เป็นขนาด `B2als_v2` (4 GiB)

## งานประจำ

ดู log และสถานะ รันบน VM

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml ps
docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail 100 web
```

ถ้าสั่ง `docker compose up` เอง ให้ระบุ `IMAGE_TAG=$(git rev-parse HEAD)` นำหน้า
ไม่เช่นนั้น compose จะใช้ tag `latest` ซึ่งอาจไม่ใช่รุ่นที่ deploy อยู่

### Rollback

รันบน VM ด้วย SHA เต็มของ commit บน `main` ที่ต้องการกลับไป

```bash
cd /opt/roangraam
bash scripts/deploy_staging.sh <full commit sha>
```

- Migration ไม่ถูกย้อนอัตโนมัติ ถ้ารุ่นใหม่แก้ schema ต้องตรวจก่อนว่ารุ่นเก่ายังทำงานกับ schema นี้ได้
- อย่าย้อนไป commit ที่เก่ากว่าตอนเพิ่ม `scripts/deploy_staging.sh` เพราะไฟล์จะหายจาก VM และ deploy อัตโนมัติครั้งถัดไปจะล้มเหลว
  ถ้าเกิดขึ้นแล้ว ให้ login ด้วย key ส่วนตัว รัน `git checkout --detach origin/main` ใน `/opt/roangraam` แล้วสั่ง `gh workflow run CI --ref main` จากเครื่องตัวเอง
- merge ครั้งถัดไปจะ deploy รุ่นล่าสุดทับ ถ้าต้องหยุดชั่วคราวให้ตั้ง `STAGING_DEPLOY_ENABLED` เป็น `false`

### หมุนเวียน deploy key

สร้าง key ใหม่ตามขั้น 2 แทนบรรทัดเดิมใน `authorized_keys` แล้วตั้ง `STAGING_SSH_KEY` ใหม่

## ลบทิ้งหลังตรวจงาน

1. ลบ resource group `rg-roangraam-staging` และ `cloud-shell-storage` ถ้ามี
2. รัน `gh variable delete STAGING_DEPLOY_ENABLED`
3. ลบ environment `staging` และ package `roangraam` บน GitHub
