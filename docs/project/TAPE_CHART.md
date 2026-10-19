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
and shared navbar are untouched. The accounts isolation sweep discovers the new
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

No migrations or new application dependencies. Navbar integration and refresh
controls remain for PR 3 after coordinating with B. Dragging is outside Sprint 2.
No payment/staging end-to-end demo was performed.
