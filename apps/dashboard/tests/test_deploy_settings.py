import importlib
import json
import os
import subprocess
import sys

import pytest
from django.conf import settings as django_settings
from django.core.management import call_command
from django.test import Client
from django.urls import clear_url_caches

import config.urls

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


@pytest.fixture
def media_urls(settings, tmp_path):
    """
    Rebuild config.urls with DEBUG off and SERVE_MEDIA set by the test, over a temporary
    MEDIA_ROOT holding one logo. The original URLs are rebuilt afterwards.
    """
    original = (settings.DEBUG, settings.SERVE_MEDIA, settings.MEDIA_ROOT)
    settings.MEDIA_ROOT = tmp_path / 'media'
    logo = settings.MEDIA_ROOT / 'properties' / 'demo' / 'logo.png'
    logo.parent.mkdir(parents=True)
    logo.write_bytes(b'\x89PNG fake logo')
    (tmp_path / 'outside.txt').write_text('not public')

    def build(serve_media):
        settings.DEBUG, settings.SERVE_MEDIA = False, serve_media
        importlib.reload(config.urls)
        clear_url_caches()

    yield build
    settings.DEBUG, settings.SERVE_MEDIA, settings.MEDIA_ROOT = original
    importlib.reload(config.urls)
    clear_url_caches()


@pytest.mark.django_db
def test_uploaded_logos_are_served_when_asked_with_debug_off(client, media_urls):
    media_urls(serve_media=True)

    response = client.get('/media/properties/demo/logo.png')

    assert response.status_code == 200
    assert b''.join(response.streaming_content) == b'\x89PNG fake logo'
    assert response['X-Content-Type-Options'] == 'nosniff'


@pytest.mark.django_db
def test_media_is_not_served_by_default_with_debug_off(client, media_urls):
    media_urls(serve_media=False)

    assert client.get('/media/properties/demo/logo.png').status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize('path', ['/media/../outside.txt', '/media/%2E%2E/outside.txt', '/media/properties/'])
def test_media_never_leaves_media_root_or_lists_folders(client, media_urls, path):
    media_urls(serve_media=True)
    # Answer like the real server: a path outside MEDIA_ROOT is a 400, not an exception
    client.raise_request_exception = False

    response = client.get(path)

    assert response.status_code in (400, 404)
    assert b'not public' not in response.content


@pytest.mark.parametrize('name', ['.gitignore', '.dockerignore'])
def test_server_secrets_file_is_ignored(name):
    lines = (REPO / name).read_text(encoding='utf-8').splitlines()

    assert '.env.production' in lines
