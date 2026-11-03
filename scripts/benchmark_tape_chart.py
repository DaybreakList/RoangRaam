"""Opt-in Tape Chart benchmark. Uses a disposable PostgreSQL test database only.

Run with: python scripts/benchmark_tape_chart.py --output /tmp/chart.json
Add --serve --port 8010 for local browser measurement with synthetic data.
"""
import argparse
import json
import math
from pathlib import Path
from statistics import median
import sys
from time import perf_counter


def summary(values):
    return {'median_ms': round(median(values), 2),
            'p95_ms': round(sorted(values)[math.ceil(len(values) * .95) - 1], 2)}


def seed():
    from datetime import date, timedelta
    from apps.bookings.models import Booking, BookingRoom
    from apps.bookings.tests.factories import BookingFactory, BookingRoomFactory, GuestFactory
    from apps.properties.tests.factories import MembershipFactory, PropertyFactory, UserFactory
    from apps.rooms.tests.factories import RoomFactory, RoomTypeFactory

    start = date(2026, 11, 3)
    user = UserFactory(email='tape-benchmark@example.invalid')
    properties = {}
    for label in ('typical', 'dense'):
        prop = PropertyFactory(slug=f'bench-{label}', name=f'Synthetic {label}')
        MembershipFactory(user=user, property=prop)
        types = [RoomTypeFactory(property=prop, name=f'Type {i}') for i in range(3)]
        rooms = [RoomFactory(property=prop, room_type=types[i % 3], number=f'{i + 1:03}',
                             floor=i // 30 + 1, hk_status='DIRTY' if i % 4 == 1 else 'CLEAN')
                 for i in range(90)]
        guest = GuestFactory(property=prop, first_name='Synthetic', last_name='Guest')
        slots = [(i, day) for i in range(90) for day in range(60)] if label == 'dense' else [
            (i, day) for day in range(0, 60, 5) for i in range(89)
        ][:1000]
        bookings, stays = [], []
        for sequence, (i, day) in enumerate(slots):
            booking = BookingFactory.build(property=prop, guest=guest, code=f'T{sequence:07}',
                                           check_in_date=start + timedelta(days=day),
                                           check_out_date=start + timedelta(days=day + (1 if label == 'dense' else 3)))
            bookings.append(booking)
            stays.append(BookingRoomFactory.build(booking=booking, room=rooms[i]))
        Booking.objects.bulk_create(bookings)
        BookingRoom.objects.bulk_create(stays)
        properties[label] = prop
    return user, properties, start


def measure(client, properties, start, repeats):
    from django.db import connection
    from apps.frontdesk.services.tape_chart import build_tape_chart

    result = {}
    for label, prop in properties.items():
        for floor in (None, 1):
            samples, charts = [], []
            params = {'start': start.isoformat(), 'days': 60}
            if floor:
                params['floor'] = floor
            url = f'/p/{prop.slug}/frontdesk/tape-chart/'
            response = client.get(url, params)  # warmup, including template compilation
            assert response.status_code == 200
            for _ in range(repeats):
                began = perf_counter()
                chart = build_tape_chart(prop, start=start, days=60, floor=floor)
                charts.append((perf_counter() - began) * 1000)
                began = perf_counter()
                response = client.get(url, params)
                samples.append((perf_counter() - began) * 1000)
            queries = []

            def record(execute, sql, params, many, context):
                began = perf_counter()
                try:
                    return execute(sql, params, many, context)
                finally:
                    queries.append({'ms': round((perf_counter() - began) * 1000, 3),
                                    'rows': context['cursor'].rowcount})

            with connection.execute_wrapper(record):
                client.get(url, params)
            with connection.execute_wrapper(record):
                build_tape_chart(prop, start=start, days=60, floor=floor)
            from html.parser import HTMLParser

            class Count(HTMLParser):
                nodes = 0
                bars = 0
                rows = 0

                def handle_starttag(self, tag, attrs):
                    self.nodes += 1
                    classes = dict(attrs).get('class', '').split()
                    self.bars += 'tc-bar' in classes
                    self.rows += 'tc-row' in classes

            parser = Count()
            parser.feed(response.content.decode())
            result[f'{label}-floor-{floor or "all"}'] = {
                'chart': summary(charts), 'full_get': summary(samples),
                'html_bytes': len(response.content), 'html_elements': parser.nodes,
                'rendered_rows': parser.rows, 'rendered_bars': parser.bars,
                'bars': sum(len(row['bars']) for row in chart['rows']),
                'queries_get_then_chart': queries,
                'move_json_bytes': len(json.dumps(chart['move_data']).encode()),
            }
    return result


def serve(client, port, host):
    """Loopback-only fixture server: scoped chart endpoints plus project static files.

    Auth is supplied by the test client in a disposable DB. Never expose this server
    to a network. POSTs are only for synthetic room moves; no demo/production data.
    """
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import parse_qs, unquote, urlsplit
    import mimetypes

    root = Path(__file__).resolve().parents[1]
    metrics = (root / 'scripts/tape_chart_metrics.js').read_text()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            path = unquote(urlsplit(self.path).path)
            if path.startswith('/static/'):
                file = (root / 'static' / path.removeprefix('/static/')).resolve()
                if not file.is_relative_to(root / 'static') or not file.is_file():
                    return self.send_error(404)
                return self.reply(200, file.read_bytes(), mimetypes.guess_type(file)[0] or 'application/octet-stream')
            if not any(path == f'/p/bench-{label}/frontdesk/tape-chart/' for label in ('typical', 'dense')):
                return self.send_error(404)
            response = client.get(self.path, HTTP_HX_REQUEST=self.headers.get('HX-Request', 'false'))
            body = response.content
            if b'<head>' in body:
                body = body.replace(b'<head>', b'<head><script>' + metrics.encode() + b'</script>')
            self.reply(response.status_code, body, 'text/html; charset=utf-8')

        def do_POST(self):
            path = urlsplit(self.path).path
            if not path.startswith(('/p/bench-typical/frontdesk/tape-chart/rooms/',
                                    '/p/bench-dense/frontdesk/tape-chart/rooms/')) or not path.endswith('/move/'):
                return self.send_error(404)
            data = {k: v[-1] for k, v in parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode()).items()}
            response = client.post(path, data)
            self.reply(response.status_code, response.content, response.get('Content-Type', 'application/json'))

        def reply(self, status, body, content_type):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    print(f'Fixture browser server at http://127.0.0.1:{port}/p/bench-typical/frontdesk/tape-chart/?start=2026-11-03&days=60', flush=True)
    HTTPServer((host, port), Handler).serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--repeats', type=int, default=20)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=8010)
    parser.add_argument('--host', default='127.0.0.1', help='Use 0.0.0.0 only in a container published on host loopback')
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('repeats must be positive')
    import os
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from django.conf import settings
    from django.test import Client, override_settings
    from django.test.utils import setup_databases, teardown_databases
    settings.DATABASES['default']['TEST']['NAME'] = f'test_tape_benchmark_{args.port}'
    with override_settings(DEBUG=False, ALLOWED_HOSTS=['testserver'],
                           STORAGES={**settings.STORAGES, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}):
        databases = setup_databases(verbosity=0, interactive=False)
        try:
            user, properties, start = seed()
            client = Client()
            client.force_login(user)
            result = measure(client, properties, start, args.repeats)
            Path(args.output).write_text(json.dumps(result, indent=2))
            print(json.dumps(result), flush=True)
            if args.serve:
                serve(client, args.port, args.host)
        finally:
            teardown_databases(databases, verbosity=0)


if __name__ == '__main__':
    main()
