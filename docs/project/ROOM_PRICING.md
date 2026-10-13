# Price calendar and range updates — Sprint 1 / D

Branch: codex/room-pricing, based on codex/rooms-crud.
Depends on models PR #25 and CRUD PR #27; review/merge those first. This PR targets
main and includes its prerequisite commits until they merge.

## Approved behavior

- Monthly calendar for one property-scoped RoomType, with previous/next month links.
- Missing days show "ยังไม่ตั้งราคา". Zero is an explicit valid price, not a missing rate.
- Single-day editing and bulk start/end dates include both endpoints.
- Existing DailyRate records are overwritten; missing dates are created.
- OWNER/MANAGER edit, FRONT_DESK reads, HOUSEKEEPING is denied.
- All dates in a request commit together or roll back together.
- No changes to booking snapshots: this code only writes DailyRate. Booking snapshot
  integration remains dependent on A's booking implementation and is not tested here.

## Implementation and operational limits

Forms validate nonnegative Decimal values with at most 12 total digits and 2
fractional digits. Service entry also validates its inputs and rejects float values.
The synchronous request is limited to 366 inclusive days, displayed on the form,
to bound transaction duration. Larger changes can be split into multiple requests.
No seasonal rules, fallback rate or date-based cancellation policy is introduced.

set_daily_prices() uses transaction.atomic() and locks the parent RoomType before
reading/writing DailyRate. This serializes writers using this service even when
none of the daily rows exist yet. Existing DailyRate rows are also locked. Both
single-day and bulk forms use this same service. Other future writers must use it
or take the same parent lock. Concurrent requests use last-writer-wins semantics
for overlapping days; no optimistic version conflict UI is added.

Monthly reads fetch prices in one query and build the calendar in memory. Scoped
querysets and form choices prevent cross-property reads and writes. Foreign IDs
in URLs, calendar selection or submitted property/type fields return 404. Existing
membership middleware and role policy are reused. POST requires CSRF protection;
GET does not write. Unsupported PATCH/DELETE return 405 for local objects and
foreign object routes return 404.

## Routes (under /p/<property_slug>/inventory/)

- rates/?room_type=<id>&month=YYYY-MM — GET/HEAD calendar; defaults to the first
  scoped room type and current month in the project's configured timezone.
- rates/<pk>/day/YYYY-MM-DD/ — GET/HEAD form and POST single-day price.
- rates/<pk>/bulk/ — GET/HEAD form and POST inclusive start_date/end_date/price.

A successful POST redirects back to the selected month with a saved-day count.
Invalid data returns 400 with field errors and preserves submitted values. The
calendar is horizontally scrollable on mobile; the rest of the page fits the viewport.

## Validation — 13 October 2026

- Django system check: no issues.
- makemigrations --check --dry-run: no changes detected; no model/migration change.
- Full pytest --create-db on PostgreSQL 16.15: 303 passed, including 72 pricing tests.
- Tests cover inclusive upserts and repeat submissions, unchanged other dates/types/
  properties, zero versus missing price, invalid dates/amounts, year/leap boundaries,
  rollback after partial work, role/membership/CSRF, forged IDs, constant calendar
  query counts, and concurrent writers with initially absent rates.
- Browser QA in Microsoft Edge: three-day bulk update, single-day zero price,
  reversed-range validation, desktop 1280px and mobile 390px. No JavaScript page
  errors or outer-page horizontal overflow. Mobile calendar scroll is intentional.
- Docker is deferred at the user's request; PostgreSQL was run locally for tests.
- Snapshot end-to-end tests must follow once booking models/services exist.

## Changed files

- apps/rooms/forms.py
- apps/rooms/services.py
- apps/rooms/views.py
- apps/rooms/urls.py
- apps/rooms/tests/test_pricing.py
- templates/rooms/base.html
- templates/rooms/calendar.html
- templates/rooms/price_form.html
- static/rooms/inventory.css
- docs/project/ROOM_PRICING.md

Reproduce with the configured PostgreSQL environment:

```sh
python manage.py check
python manage.py makemigrations --check --dry-run
python -m pytest --create-db
```
