import json
import os
import subprocess
import sys

import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from django.test import Client

REPO = django_settings.BASE_DIR


def read_https_settings(**env):
    """The HTTPS-related settings of a fresh process that imports config.settings with `env`."""
    code = (
        'import json, config.settings as s; '
        'print(json.dumps({'
        '"proxy": getattr(s, "SECURE_PROXY_SSL_HEADER", None), '
        '"session": getattr(s, "SESSION_COOKIE_SECURE", False), '
        '"csrf": getattr(s, "CSRF_COOKIE_SECURE", False), '
        '"hsts": getattr(s, "SECURE_HSTS_SECONDS", 0)}))'
    )
    result = subprocess.run(
        [sys.executable, '-c', code],
        capture_output=True, text=True, check=True, cwd=REPO,
        env={**os.environ, 'DJANGO_SECRET_KEY': 'test-only', **env},
    )
    return json.loads(result.stdout)


def test_https_settings_are_off_by_default():
    result = read_https_settings(DJANGO_HTTPS='False')

    assert result == {'proxy': None, 'session': False, 'csrf': False, 'hsts': 0}


def test_https_setting_turns_on_secure_cookies_and_the_proxy_header():
    result = read_https_settings(DJANGO_HTTPS='True')

    assert result['proxy'] == ['HTTP_X_FORWARDED_PROTO', 'https']
    assert result['session'] is True
    assert result['csrf'] is True
    assert result['hsts'] > 0


def test_static_files_are_served_with_debug_off(tmp_path, settings):
    # Same as the server: collectstatic into STATIC_ROOT, DEBUG off (the tests' default)
    settings.STATIC_ROOT = tmp_path
    call_command('collectstatic', interactive=False, verbosity=0)
    client = Client()

    for path in ('/static/css/tokens.css', '/static/admin/css/base.css'):
        response = client.get(path)
        assert response.status_code == 200, path
        assert b''.join(response.streaming_content)


def test_production_compose_publishes_only_the_caddy_ports():
    text = (REPO / 'docker-compose.prod.yml').read_text(encoding='utf-8')

    # Databases and gunicorn must be reachable inside the stack only
    assert '5432' not in text
    assert '8000:8000' not in text
    assert text.count('ports:') == 1


@pytest.mark.parametrize('name', ['.gitignore', '.dockerignore'])
def test_server_secrets_file_is_ignored(name):
    lines = (REPO / name).read_text(encoding='utf-8').splitlines()

    assert '.env.production' in lines
