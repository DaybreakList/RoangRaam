import os

from django.core.management.base import BaseCommand, CommandError

from apps.dashboard.seed import (
    DEMO_EMAILS,
    OWNER_EMAIL,
    DemoDataExists,
    reset_demo_data,
    seed_demo_data,
)


class Command(BaseCommand):
    help = 'สร้างข้อมูลเดโม (โรงแรม 2 แห่ง ห้อง ราคา พนักงาน และการจอง) สำหรับพัฒนาและพรีเซนต์'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset',
            action='store_true',
            help='ลบข้อมูลเดโมเดิม (เฉพาะโรงแรมเดโม) แล้วสร้างใหม่',
        )

    def handle(self, *args, **options):
        # Read from the environment, never hardcoded (see .env.example)
        password = os.environ.get('SEED_DEMO_PASSWORD', '')
        if not password:
            raise CommandError('ต้องตั้งค่า SEED_DEMO_PASSWORD ใน environment (ไฟล์ .env) ก่อน')

        if options['reset']:
            removed = reset_demo_data()
            self.stdout.write(f'ลบข้อมูลเดโมเดิมแล้ว ({removed} โรงแรม)')

        try:
            summary = seed_demo_data(password)
        except DemoDataExists:
            self.stdout.write(
                'มีข้อมูลเดโมอยู่แล้ว ไม่ได้ทำอะไร (ใช้ --reset เพื่อลบแล้วสร้างใหม่)',
            )
            return

        self.stdout.write(self.style.SUCCESS('สร้างข้อมูลเดโมเรียบร้อย'))
        for hotel in summary:
            self.stdout.write(
                f'- {hotel["name"]} [{hotel["slug"]}]: {hotel["rooms"]} ห้อง, '
                f'{hotel["bookings"]} การจอง'
            )
        self.stdout.write('บัญชีเดโม (รหัสผ่านคือค่า SEED_DEMO_PASSWORD ใน .env):')
        self.stdout.write(f'  {OWNER_EMAIL} (เจ้าของ ทั้งสองโรงแรม)')
        for email in sorted(DEMO_EMAILS - {OWNER_EMAIL}):
            self.stdout.write(f'  {email}')
