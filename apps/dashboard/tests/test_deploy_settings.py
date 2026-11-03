import json
import os
import subprocess
import sys

import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from django.test import Client

REPO = django_settings.BASE_DIR


def fresh_env(**env):
    """The test process's environment without the values a server would set, plus `env`."""
    base = {key: value for key, value in os.environ.items() if key != 'RENDER_EXTERNAL_HOSTNAME'}
    return {**base, 'DJANGO_SECRET_KEY': 'test-only', **env}


def read_https_settings(**env):
    """The HTTPS-related settings of a fresh process that imports config.settings with `env`."""
    code = (
        'import json, config.settings as s; '
        'print(json.dumps({'
        '"proxy": getattr(s, "SECURE_PROXY_SSL_HEADER", None), '
        '"redirect": getattr(s, "SECURE_SSL_REDIRECT", False), '
        '"session": getattr(s, "SESSION_COOKIE_SECURE", False), '
        '"csrf": getattr(s, "CSRF_COOKIE_SECURE", False), '
        '"hsts": getattr(s, "SECURE_HSTS_SECONDS", 0), '
        '"hosts": s.ALLOWED_HOSTS, "origins": s.CSRF_TRUSTED_ORIGINS}))'
    )
    result = subprocess.run(
        [sys.executable, '-c', code],
        capture_output=True, text=True, check=True, cwd=REPO, env=fresh_env(**env),
    )
    return json.loads(result.stdout)


def test_https_settings_are_off_by_default():
    result = read_https_settings(DJANGO_HTTPS='False')

    assert (result['proxy'], result['redirect'], result['session'], result['csrf'], result['hsts']) == (
        None, False, False, False, 0,
    )


def test_https_setting_turns_on_secure_cookies_and_the_proxy_header():
    result = read_https_settings(DJANGO_HTTPS='True')

    assert result['proxy'] == ['HTTP_X_FORWARDED_PROTO', 'https']
    assert result['redirect'] is True
    assert result['session'] is True
    assert result['csrf'] is True
    assert result['hsts'] > 0


def test_the_render_host_name_is_allowed_and_trusted():
    result = read_https_settings(
        DJANGO_ALLOWED_HOSTS='localhost', DJANGO_CSRF_TRUSTED_ORIGINS='',
        RENDER_EXTERNAL_HOSTNAME='roangraam.onrender.com',
    )

    assert result['hosts'] == ['localhost', 'roangraam.onrender.com']
    assert result['origins'] == ['https://roangraam.onrender.com']


def test_production_settings_pass_the_deployment_checklist():
    # The same check as `manage.py check --deploy` before going live; warnings count as failures
    result = subprocess.run(
        [sys.executable, 'manage.py', 'check', '--deploy', '--fail-level', 'WARNING'],
        capture_output=True, text=True, cwd=REPO,
        env=fresh_env(
            DJANGO_DEBUG='False', DJANGO_HTTPS='True',
            DJANGO_SECRET_KEY='x' * 20 + 'production-like-key-with-enough-characters-1234567',
            DJANGO_ALLOWED_HOSTS='roangraam.onrender.com',
        ),
    )

    assert result.returncode == 0, result.stdout + result.stderr


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


def test_the_image_starts_gunicorn_through_the_start_script():
    dockerfile = (REPO / 'Dockerfile').read_text(encoding='utf-8')
    script = (REPO / 'scripts' / 'start.sh').read_bytes()

    assert 'CMD ["sh", "scripts/start.sh"]' in dockerfile
    assert b'\r\n' not in script  # a CRLF script fails in the Linux container
    assert b'migrate --noinput' in script
    assert b'exec gunicorn config.wsgi:application' in script
    assert b'${PORT:-8000}' in script
    # Rebuilding the demo data for today is opt-in, never the default
    assert b'"${SEED_RESET_ON_START:-false}"' in script
    assert b'seed_demo --reset' in script


@pytest.mark.parametrize('name', ['.gitignore', '.dockerignore'])
def test_server_secrets_file_is_ignored(name):
    lines = (REPO / name).read_text(encoding='utf-8').splitlines()

    assert '.env.production' in lines
