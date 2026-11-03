# Tape Chart — Sprint 2, D, PR 1–2

URL: `/p/<property_slug>/frontdesk/tape-chart/`; URL name: `tape_chart:index`.

Read-only CSS Grid showing fourteen days, defaulting to today in the property's timezone.
Each BookingRoom is a bar using `[check_in, check_out)`. Only active booking-room
rows are shown. Checked-out stays remain visible when in the window; cancelled
or expired bookings disappear after refresh through the shared cancel_booking service.
Active rooms are listed even without bookings. Inactive rooms with a visible booking
remain listed and labelled. Bars show booking codes, guest names and status, never
guest email or phone. Guest relations are also scoped to the current property.
Room headers stay fixed horizontally; date headers stay fixed vertically inside
the scrollable chart (maximum height 70vh). This completes issues #79 and #80.

Access agreed by A/C: OWNER, MANAGER and FRONT_DESK; HOUSEKEEPING is denied.
All querysets are property-scoped, including related property checks. Anonymous users
redirect to login; other-property staff receive 404. Write methods are unsupported.

D owns tape_chart_* files and services/tape_chart.py. E owns daily_ops_* and
services/daily_ops.py. services/__init__.py is empty. Existing frontdesk scaffolds
are untouched; the shared navbar changed in PR 3 (below). The accounts isolation sweep discovers the new
private route automatically; its public-route allowlist is unchanged.

## Filters and navigation (PR 2, issue #81)

GET parameters: `start=YYYY-MM-DD`, optional integer `floor` and `room_type` ID.
Choices come only from the current property. Both filters apply together; zero and
negative floors work. Foreign or missing room-type IDs return 404. Invalid date,
floor or malformed ID input renders Thai form errors with HTTP 200 so HTMX can
replace the panel. No chart is queried for invalid input.

Previous/today/next links preserve filters. Previous/next move fourteen days;
this is the temporary navigation design approved by D, not an additional team
acceptance criterion. Boundary dates cannot overflow Python's date range.
The GET form works without JavaScript. HTMX replaces only the chart panel and
updates the URL; history restoration requests receive a full page. Vary headers
distinguish partial/full responses. There is no automatic polling.

## Validation

- Docker: `pytest --create-db -q`: 644 passed on main 594fe25 plus PR 2.
- Focused frontdesk and accounts isolation sweep: 108 passed.
- Tests cover combined filters, scoped choices/IDs, invalid dates, date boundaries,
  navigation, HTMX/history, permissions and constant query count.
- PR 1 regressions cover guest names, HTML escaping and cross-property guest relations.
- Ruff 0.17.1: `ruff check .`: passed.
- `manage.py check`: passed; `makemigrations --check --dry-run`: no changes.
- Edge PR 2: floor filtering, next-window navigation, browser Back and combined
  filters with an empty result were verified using synthetic demo data.
- Earlier desktop/mobile layout checks passed; no real guest data was used.

No migrations or new application dependencies. Refresh controls are not built.
Dragging is outside Sprint 2.
No payment/staging end-to-end demo was performed.

## PR 3: navigation (B รับต่อจาก D)

B took over PR 3 from D; D agreed to this scope and will not open a duplicate PR 3.
D should still review the Tape Chart parts (`services/tape_chart.py`, `_tape_chart_panel.html`).

- Navbar menu "ตารางการจอง" links to `tape_chart:index` of `request.property` and sets
  `aria-current="page"` on that page. It shows only when `request.membership` exists and its
  role is in `TapeChartView.allowed_roles`. The roles are never repeated in templates: the
  `can_view_page` filter in `apps/frontdesk/templatetags/frontdesk_nav.py` reads them from the
  view classes registered in its single `NAV_PAGES` dict (only `tape_chart` for now; another page
  is added with one line). Unknown page names are hidden.
- The middleware sets `request.membership` only on non-public pages, so guests and staff on
  public pages (e.g. `bookings:search`) see no menu.
- Navbar brand link: `property_home` when `request.membership` exists; otherwise `bookings:search`
  of the current property; pages without `request.property` are unchanged.
- Each bar from `build_tape_chart()` carries `booking_id` (the Booking primary key, not the
  BookingRoom id or the booking code, no extra query) and renders `data-booking-id` on `.tc-bar`.
  No guest link, detail page or dragging yet.
- Tests: `apps/frontdesk/tests/test_tape_chart_navigation.py`. No migrations, routes or dependencies.
- Docker: `pytest --create-db -q`: 744 passed; Ruff 0.17.1, `manage.py check` and
  `makemigrations --check --dry-run` clean.


## Sprint 3: room cleanliness (D portion of US-12, #96)

Room headers show the current `Room.hk_status` as a text badge: CLEAN = สะอาด,
DIRTY = รอทำความสะอาด. The badge is separate from the booking status color and
remains visible for empty rooms and inactive rooms retained by visible bookings.
It reflects current readiness, even when browsing historical booking dates.

The existing scoped room query already loads this field; there is no new query,
endpoint, permission, migration or dependency. Full reloads and HTMX panel requests
read the latest state. There is no automatic polling or cleanliness write action
on the chart. Check-out marks rooms Dirty; the existing housekeeping board marks
them Clean. This change does not define drag-to-move eligibility.

Tests in `test_tape_chart.py` cover both badges on full/partial responses, inactive
rooms, the check-out/cleaning lifecycle, and cross-property isolation without writes.
Existing chart tests cover roles, unsupported write methods and constant query counts.
This implements only D's Tape Chart portion; #96 also includes the check-in screen.


Validation on `d209089` plus this change (2026-10-31):
- `docker compose exec -T web pytest apps/frontdesk/tests apps/accounts/tests/test_isolation_sweep.py apps/properties/tests/test_permission_matrix.py -q`: 362 passed (including 9 new cases).
- `docker compose exec -T web python manage.py check`: no issues.
- `git diff --check`: passed.
- Browser layout inspection used a rendered template preview with unsaved synthetic
  room objects; it was not a logged-in end-to-end browser test.
- Host `python -m ruff check apps/frontdesk/tests/test_tape_chart.py` could not run:
  Ruff is not installed in that Python environment.


## Sprint 3: room move endpoint (D, #107)

`services/tape_chart_moves.py` moves one BookingRoom before check-in. The POST route
`tape_chart:move` uses the chart's staff roles and returns JSON for the later Alpine.js
interaction (#106). See API_CONTRACT.md for the payload, responses and agreed rules.
No drag UI is added in this PR. No models, migrations or dependencies are changed.

The permission matrix includes the route and a valid move request (with A's approval).
Tests in `test_tape_chart_moves.py` cover preserved snapshots, allowed Dirty targets,
status and exact expiry boundaries, whole-stay overlap, same-booking collisions,
foreign IDs/relations, CSRF, role/method restrictions, repeated requests and the real
PostgreSQL exclusion constraint. Concurrent tests use independent database connections
for competing moves and moves racing cancellation/check-in/check-out.


Validation on main `ca58228` plus #107 changes (2026-10-31):
- `docker compose exec -T web pytest apps/frontdesk/tests apps/properties/tests/test_permission_matrix.py apps/accounts/tests/test_isolation_sweep.py apps/bookings/tests -q`: **826 passed**, including 50 room-move cases, in 106.56 seconds on PostgreSQL.
- `.venv/Scripts/ruff.exe check apps/frontdesk/services/tape_chart_moves.py apps/frontdesk/tape_chart_forms.py apps/frontdesk/tape_chart_views.py apps/frontdesk/tape_chart_urls.py apps/frontdesk/tests/test_tape_chart_moves.py apps/properties/tests/test_permission_matrix.py`: passed.
- `docker compose exec -T web python manage.py check`: no issues.
- `docker compose exec -T web python manage.py makemigrations --check --dry-run`: no changes.
- `git diff --check`: passed.

These are endpoint/service regression tests, not browser drag-and-drop acceptance.
The original validation above predates the merge of Clean/Dirty PR #140.


### Compatibility review after PRs #140, #141 and #142 merged

Updated the uncommitted #107 branch to main `5225fdd` (2026-10-31). Only this
document had a merge conflict; both the cleanliness and room-move sections were
preserved. The endpoint/service/form/route and permission matrix implementation
were unchanged. The move remains pre-check-in only and preserves financial data.

Added two Folio integration cases: a paid confirmed booking moves with unchanged
night prices, totals, VAT and payments while Folio shows the new room number;
a checked-in booking with a closed bill refuses the move and preserves its Receipt.
The shared rejection snapshot now also checks Receipt rows.

Verification against the merged code:
- `docker compose run --rm --no-deps web pytest --create-db -q`: **1255 passed**
  in 317.32s. This run included the original 50 move cases and all main tests;
  the fresh test database applied the new Payment/Receipt migration from #142.
- After adding the Folio cases, `docker compose run --rm --no-deps web pytest apps/frontdesk/tests/test_tape_chart_moves.py -q`: **52 passed** in 14.74s.
- `docker compose run --rm --no-deps web python manage.py check`: no issues.
- `docker compose run --rm --no-deps web python manage.py makemigrations --check --dry-run`: no changes.
- Ruff on the six changed Python files and `git diff --check`: passed.

No endpoint changes or new migrations were needed for compatibility. Test databases
were used; the demo database was not reset or migrated by these verification commands.

## Sprint 3: drag-to-move UI (D, #106)

Built on main `fab90a5`, using the merged #107 endpoint. Alpine pointer controls
move a BookingRoom between rows; keyboard users select the handle and a destination
button. Escape/pointer cancellation aborts selection. Only CONFIRMED and unexpired
PENDING_PAYMENT stays have handles. Destination checks cover active rooms of the
same type and whole-stay occupancy, including clipped dates outside the 14-day view.
Dirty destinations are allowed. The server still validates every POST.

The chart service adds one batched occupancy query when movable stays are visible
(three chart queries instead of two, with no per-room query). JSON metadata uses
Django json_script escaping and property-scoped queries. Each request sends only
`target_room_id` with CSRF to the BookingRoom-specific URL. Dates and financial
snapshots are not editable. No route, permission, model, migration or library changes.

Bars remain in their original position until the authoritative panel is reloaded.
Duplicate submissions and filter requests during saving are blocked. Server errors
are shown in Thai; uncertain network results trigger a refresh, and failed refreshes
disable further moves until reload. Filters remain in the URL across refreshes.

Files changed for review:
- `apps/frontdesk/services/tape_chart.py`
- `apps/frontdesk/tests/test_tape_chart.py`
- `apps/frontdesk/tests/test_tape_chart_drag.py`
- `apps/frontdesk/tests/tape_chart_drag.test.cjs`
- `static/frontdesk/tape_chart.js`
- `static/frontdesk/tape_chart.css`
- `templates/frontdesk/tape_chart.html`
- `templates/frontdesk/_tape_chart_panel.html`
- `docs/project/TAPE_CHART.md`

Validation (2026-11-01):
- `docker compose exec -T web pytest apps/frontdesk/tests apps/properties/tests/test_permission_matrix.py apps/accounts/tests/test_isolation_sweep.py -q`: **446 passed**, 115.35s.
- `node --test apps/frontdesk/tests/tape_chart_drag.test.cjs`: **11 passed**. Uses Node built-ins, no added dependencies.
- `.venv/Scripts/ruff.exe check apps/frontdesk/services/tape_chart.py apps/frontdesk/tests/test_tape_chart.py apps/frontdesk/tests/test_tape_chart_drag.py`: passed.
- `docker compose exec -T web python manage.py check`: no issues.
- `git diff --check`: passed.
- Logged-in local browser with a dedicated synthetic property: pointer drag from
  101 to Dirty 102 persisted, blocked occupied 103, keyboard move back succeeded,
  and a second move after an HTMX floor filter preserved the filter and reinitialized
  controls. Database verification confirmed the destination and unchanged Dirty state.
  The dedicated QA data was removed afterward. No physical touch-device test was run.

Performance acceptance for 90 rooms x 60 days remains #108; this PR retains 14 days.

### Updated to main after Sprint 3 integrations (2026-11-03)

Fast-forwarded `codex/tape-chart-drag-drop` from `fab90a5` to main `b7cceaf`
and restored all nine uncommitted #106 files. The chart query-count change merged
cleanly with main's `close_bill()` import/call before check-out in the Clean/Dirty
test. The endpoint and UI behavior needed no further implementation changes.

Rebuilt the real development image and recreated the web container:
- `docker compose build web`: passed.
- `docker compose up -d web`: passed; existing PostgreSQL volume retained.
- Container imports confirmed `fpdf2 2.8.9` and `uharfbuzz 0.56.4` from the rebuilt
  image, without the temporary dependency path used during the earlier main review.
- HTTP smoke checks: `/accounts/login/` and `/static/frontdesk/tape_chart.js` returned 200.

Validation against main `b7cceaf` plus the uncommitted #106 diff:
- `docker compose exec -T web pytest apps/frontdesk/tests apps/accounts/tests apps/properties/tests/test_permission_matrix.py apps/bookings/tests/test_receipt_pdf.py -q`: **562 passed**, 94.42s.
- `node --test apps/frontdesk/tests/tape_chart_drag.test.cjs`: **11 passed**.
- `.venv/Scripts/ruff.exe check apps/frontdesk/services/tape_chart.py apps/frontdesk/tests/test_tape_chart.py apps/frontdesk/tests/test_tape_chart_drag.py`: passed.
- `docker compose exec -T web python manage.py check`: no issues.
- `docker compose exec -T web python manage.py makemigrations --check --dry-run`: no changes.
- `git diff --check`: passed.

No new migrations. Browser drag acceptance remains the 2026-11-01 run described
above; this update ran automated regression and HTTP smoke checks. Changes remain
uncommitted for the user's diff review.
