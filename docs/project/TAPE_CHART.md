# Tape Chart — Sprint 2, D, PR 1

URL: `/p/<property_slug>/frontdesk/tape-chart/`; URL name: `tape_chart:index`.

Read-only CSS Grid showing fourteen days starting today in the property's timezone.
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

## Validation

- Docker: `pytest --create-db -q`: 620 passed after merging main 89e8051.
- Regression tests cover guest names, HTML escaping and cross-property guest relations.
- Ruff 0.17.1: `ruff check .`: passed.
- `manage.py check`: passed; `makemigrations --check --dry-run`: no changes.
- Edge: demo data renders guest names; scrolling vertically keeps date headers visible.
- Earlier desktop/mobile layout checks passed; no real guest data was used.

No migrations or new application dependencies. Filters/date navigation/HTMX belong
to PR 2. Navbar integration and refresh controls belong to PR 3. Dragging is outside
this PR. No payment/staging end-to-end demo was performed.
