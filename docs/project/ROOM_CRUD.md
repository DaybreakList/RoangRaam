# Room inventory CRUD — Sprint 1 / D

This change depends on the room models in PR #25. It is developed on
`codex/rooms-crud`, based on `codex/rooms-models`. Review and merge the model PR
first; this PR targets main and includes its prerequisite until that merge.

## Permission matrix approved in this task

| Role | List / detail | Create / edit / delete |
| --- | --- | --- |
| OWNER | Yes | Yes |
| MANAGER | Yes | Yes |
| FRONT_DESK | Yes | No (403) |
| HOUSEKEEPING | No (403) | No (403) |

PropertyMiddleware verifies membership. Object lookup uses a property-scoped
queryset before role checks, so foreign object IDs return 404 even for a role
without write access. Foreign or nonexistent relationship choices also return
404. The property comes from the URL/middleware; it is not editable in forms.

## Routes and behavior

Prefix: `/p/<property_slug>/inventory/`

For each of `room-types`, `rooms`, and `amenities`:

- `<kind>/`: paginated list, 25 rows per page (GET/HEAD).
- `<kind>/new/`: create form (GET/HEAD), create submission (POST).
- `<kind>/<pk>/`: detail (GET/HEAD).
- `<kind>/<pk>/edit/`: edit form (GET/HEAD), update submission (POST).
- `<kind>/<pk>/delete/`: confirmation (GET/HEAD), delete submission (POST).

PATCH and DELETE are not supported by these HTML form endpoints and return 405
for accessible objects. Foreign object URLs still return 404. These are not DRF
API endpoints. All mutations require CSRF protection; successful mutations redirect.
Validation errors re-render the form with status 400, retaining submitted values.
Protected deletions return 409 with a Thai explanation. GET never deletes records.

RoomType forms include amenities scoped to the current property. The service
updates the explicit through table with the same property inside the transaction.
Room forms include the room type, floor, housekeeping status and active flag.
Creation/editing/deletion is transactional, with existing rows locked when writing.
Database uniqueness conflicts become form errors rather than unhandled 500s.
List queries use select_related/prefetch_related to avoid per-row related queries.

## Scope and integration

- No changes to properties models, permissions, middleware or the shared base template.
- No new dependencies and no new migration.
- Authentication remains C's work. Until its routes merge, a staff account can sign
  in through `/admin/login/?next=/p/<property_slug>/inventory/rooms/` for local QA.
  Existing session authentication is used; the default anonymous login redirect
  depends on C wiring the application login route.
- Room images/upload UI, pricing calendar and bulk date-range prices are not part
  of this CRUD task. The model PR provides their data structures.
- Deletion uses existing model rules. Booked-room protection must be integrated
  with A's booking foreign keys before booking functionality goes live.
- Amenity deletion removes its type links. RoomType deletion is blocked when rooms
  or daily rates refer to it; otherwise its image records and amenity links cascade.

## Validation on 13 October 2026

- Django system check: no issues.
- makemigrations --check --dry-run: no changes detected.
- Full pytest --create-db on PostgreSQL 16.15: **231 passed**, including 156 CRUD tests.
- Coverage includes all four roles; foreign IDs for GET/POST/PATCH/DELETE;
  membership; scoped choices; forged property/relationship IDs; uniqueness;
  validation; protected deletion; CSRF; pagination; escaping; and stable list query counts.
- Browser QA in headless Microsoft Edge with synthetic data: create/edit/delete
  through HTML forms passed; no JavaScript page errors; desktop 1280px and mobile
  390px inspected, mobile page has no horizontal overflow.
- Docker remains deferred at the user's request. Tests used local temporary PostgreSQL.

Reproduce using the configured PostgreSQL environment:

```sh
python manage.py migrate
python manage.py check
python manage.py makemigrations --check --dry-run
python -m pytest --create-db
```

## Changed files

- apps/rooms/forms.py
- apps/rooms/services.py
- apps/rooms/views.py
- apps/rooms/urls.py
- apps/rooms/tests/test_crud.py
- config/urls.py
- templates/rooms/base.html
- templates/rooms/list.html
- templates/rooms/detail.html
- templates/rooms/form.html
- templates/rooms/delete.html
- static/rooms/inventory.css
- docs/project/ROOM_CRUD.md
