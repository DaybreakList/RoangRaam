from django.core.management.base import BaseCommand, CommandError

from apps.bookings.payments import release_expired_bookings
from apps.properties.models import Property


class Command(BaseCommand):
    help = 'ยกเลิกการจองที่รอชำระเงินเกินเวลาที่กำหนด เพื่อปล่อยห้องให้จองได้อีกครั้ง'

    def add_arguments(self, parser):
        parser.add_argument('--property', metavar='SLUG', help='ทำเฉพาะโรงแรมนี้ (ไม่ใส่ = ทุกโรงแรม)')

    def handle(self, *args, **options):
        properties = Property.objects.order_by('name')
        if options['property']:
            properties = properties.filter(slug=options['property'])
            if not properties.exists():
                raise CommandError(f'ไม่พบโรงแรม {options["property"]}')

        total = 0
        for property in properties:
            released = release_expired_bookings(property)
            total += released
            self.stdout.write(f'- {property.name}: ยกเลิก {released} การจอง')
        self.stdout.write(self.style.SUCCESS(f'ยกเลิกการจองที่หมดเวลาชำระเงินแล้ว {total} การจอง'))
