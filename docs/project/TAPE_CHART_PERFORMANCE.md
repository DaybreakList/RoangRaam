# TASK-53 / #108 — Tape Chart performance

Owner: D · Sprint 3 · measured 2026-11-03.
Base: `88af0da` (PR #176). Branch: `codex/tape-chart-performance`.
This includes the approved first optimization pass and the approved virtualization pass.
No dependency, permission, model, endpoint, or migration was added.

## Behavior and implementation

- Default 14 days; explicit 60-day selector and bounded server validation. Hotel-local dates,
  filters, history, navigation and reset preserve the chosen window.
- Filter stays by floor/type in SQL and project only needed columns. Format each distinct
  date once per response. Reuse displayed full ranges and query additional scoped holds,
  including clipped-out stays and expired active reservations. Three chart queries remain
  independent of room count. The existing move endpoint validates under database locks.
- The JSON payload contains all scoped rooms, bookings, collision ranges and display data.
  Initial HTML includes only eight rows. The client mounts visible rows plus three rows of
  overscan at either end, reuses retained nodes, and removes rows outside that range.
  Top/bottom spacers preserve the full scroll height. Rows are 112 px; the sticky header is
  64 px. Long room/type labels truncate visually and retain their title text.
- The selected booking and status message live outside the recycled DOM. Destination
  eligibility is cached on selection/refresh/expiry across all rooms, including offscreen
  rooms. Alpine ignores the static grid; immutable data and DOM references are not reactive.
- Delegated native controls support pointer selection and Enter/Space. Arrow Up/Down,
  Home/End and Page Up/Down mount and focus other room rows. Tab/Shift-Tab crosses row
  boundaries. Keyboard focus is made visible horizontally. If a manually scrolled row
  containing focus is removed, focus moves to the chart and keyboard navigation remains.
- Pointer movement and scroll/resize rendering are coalesced with requestAnimationFrame.
  Edge auto-scroll continues while stationary. Release synchronously mounts the destination
  window before hit-testing. Escape/cancel/refresh/destroy clear scheduled pointer work.
- Saving preserves vertical/horizontal scroll. After the authoritative HTMX refresh,
  focus returns to the moved booking when its row is visible; otherwise it returns to the
  chart. Busy/stale controls are disabled. Offscreen pending bookings expire on the same
  monotonic server clock and render disabled if mounted afterward.
- DOM creation uses textContent and fixed attribute names, not HTML interpolation. Clean/
  Dirty, inactive rooms, checked-in/out history and full-stay collision rules are preserved.
- Without JavaScript, only the initial eight rows are shown, with a Thai instruction to
  enable JavaScript for the complete scrollable chart. Physical touch/tablet QA is pending.

## Original #177 controlled comparison (144 px rows)

The original baseline is `88af0da` plus only 14/60-day selector/correctness changes. It was
captured before query, markup and JavaScript optimizations. Comparing unmodified 14-day
main to 60 days would not be comparable. Original snapshot SHA-256:
`A3FCC19F80E8B75B19EE0169BC381C9923F382CBEBE4EA7A81EB04FDF8B5BA6D`.
The same snapshot was run again for this comparison. Only the measurement harness was
updated to report rendered versus total row/bar counts; the baseline application is unchanged.

Both datasets have 90 rooms, three floors/types, mixed Clean/Dirty and a 60-day window
starting 2026-11-03. Typical: 1,000 three-night stays with gaps and a vacant destination.
Dense: 5,400 consecutive one-night stays, one per room per day. Benchmark bookings are all
CONFIRMED to stress movable controls. Mixed statuses, multiple-room bookings, expiry,
isolation and races are covered separately in tests.

Environment: Windows host / Intel i5-13500HX; Docker 29.9.1, 20 CPUs exposed / 8.18 GB RAM;
Python 3.12.16, PostgreSQL 16, DEBUG=False. Backend runs were sequential with no pytest or
other benchmark running. One warmup plus 20 requests per dataset/filter; nearest-rank p95.
Full GET uses Django Client, includes auth/middleware/queries/rendering, and excludes
production HTTP/network latency. Diagnostic query counts/row counts are sampled separately.

Browser: Codex in-app browser, 1265×712 viewport, same loopback fixture server and CDN
assets. Five warm reloads for each dense version. Ready is alpine:initialized plus two
animation frames. With only five samples, nearest-rank p95 equals the maximum; median and
maximum are reported without treating them as a production tail-latency guarantee.

## Original #177 results after virtualization (144 px rows)

Times in milliseconds; each cell is median / p95 across 20 requests.

| Dataset | Service baseline | Service virtual | Full GET baseline | Full GET virtual |
| --- | ---: | ---: | ---: | ---: |
| Typical, all / 1,000 bars | 71.79 / 112.78 | 47.91 / 91.80 | 184.15 / 219.84 | 63.92 / 108.91 |
| Typical, floor 1 / 351 bars | 48.05 / 86.44 | 20.29 / 21.47 | 93.39 / 127.68 | 33.53 / 36.39 |
| Dense, all / 5,400 bars | 434.77 / 459.14 | 188.38 / 221.54 | 974.83 / 999.43 | 294.61 / 320.83 |
| Dense, floor 1 / 1,800 bars | 261.35 / 293.95 | 73.82 / 123.73 | 443.00 / 471.81 | 105.87 / 166.41 |

| Dataset | HTML bytes baseline → virtual | HTML elements baseline → virtual | SSR bars baseline → virtual |
| --- | ---: | ---: | ---: |
| Typical, all | 1,500,211 → 546,393 | 5,829 → 650 | 1,000 → 96 |
| Typical, floor 1 | 530,621 → 237,211 | 2,163 → 650 | 351 → 96 |
| Dense, all | 7,680,950 → 2,803,491 | 27,828 → 2,186 | 5,400 → 480 |
| Dense, floor 1 | 2,565,902 → 1,128,302 | 9,408 → 2,186 | 1,800 → 480 |

The chart remains 3 queries and the full GET 10 queries in these fixtures. Dense floor 1's
first booking query returns 1,800 instead of 5,400 rows. The additional occupancy query
returns 0 extra rows in these fixtures because all relevant stays were already read; it
still retrieves holds outside the display or hidden by guest isolation. JSON now also
carries offscreen display data: dense payload 2,515,359 bytes (baseline 1,405,403).

Dense browser ready samples, in milliseconds:

| Version | Five samples | Median | Maximum |
| --- | --- | ---: | ---: |
| Baseline rerun | 4041.0, 4246.9, 3854.3, 3855.7, 3769.2 | 3855.7 | 4246.9 |
| Virtualization | 834.4, 562.4, 662.3, 550.4, 713.2 | 662.3 | 834.4 |

After initialization on this viewport, dense live DOM has **6 rows / 360 bars / 1,693
nodes**, versus **90 rows / 5,400 bars / 27,832 nodes** in the baseline. All **90 rooms /
5,400 bookings remain in the model**. Mounted rows increase/decrease with viewport and
scroll position; the row bound is viewport rows plus overscan, not a fixed count of six.

The proposed local targets (full GET p95 ≤ 500 ms, ready ≤ 1.5 s) pass for the dense fixture:
320.83 ms and a maximum of 834.4 ms over five reloads. The earlier nonvirtual optimization
pass measured dense GET p95 517.5 ms and browser median/max 2753.9/3592.0 ms. These are
separate rounds, not interchangeable baselines.

One functional native drag sample contained roughly 1,006 ms frame gaps without a matching
long task; this instrumented background-browser sample cannot establish a stable FPS claim.
No 60-FPS claim is made. A filter swap plus immediate keyboard navigation sampled 583.4 ms;
it is exploratory rather than a repeated interaction p95. Physical touch devices and
production bandwidth/concurrency still need team validation.

## Repeat locally

`scripts/benchmark_tape_chart.py` creates and tears down a dedicated disposable database,
`test_tape_benchmark_<port>`, using factory_boy and synthetic guests. It does not seed/reset
the demo database. The fixture server supplies an authenticated test client and only allows
chart/static requests and synthetic moves. Publish on host loopback only; do not deploy it.
CSRF is covered by application tests, not this fixture server.

```sh
docker compose run --rm --no-deps web python scripts/benchmark_tape_chart.py --output /tmp/chart.json
docker compose run --rm --no-deps -p 127.0.0.1:8011:8011 web python scripts/benchmark_tape_chart.py --output /tmp/chart.json --serve --host 0.0.0.0 --port 8011
```

Open `http://localhost:8011/p/bench-dense/frontdesk/tape-chart/?start=2026-11-03&days=60`.
Use `bench-typical` for the typical dataset. The visible `#benchmark-results` reports ready,
long tasks, frame intervals, swaps, live DOM counts and total model counts. Save the JSON
before stopping the container. Ctrl+C tears down its dedicated DB. Compare snapshots with
the same image, idle machine, browser and viewport; use distinct ports. No wall-clock
performance threshold is asserted in CI.

## Original #177 verification

- `docker compose exec -T web pytest -q`: **1,408 passed** in 177.94 s.
- `node --test apps/frontdesk/tests/tape_chart_drag.test.cjs`: **28 passed**; Node v24.22.1
  locally. CI runs these tests on Node 22 alongside pytest.
- `.venv/Scripts/ruff.exe check .`: passed.
- `docker compose exec -T web python manage.py check`: no issues.
- `docker compose exec -T web python manage.py makemigrations --check --dry-run`: no changes.
- `git diff --check`: passed. No migration command is needed.
- Node regressions cover bounded DOM, overscan, resize, safe text insertion, keyboard across
  recycled rows, focus on removal, final-release hit-test, edge scrolling, refresh restoring
  scroll/focus, offscreen expiry, stale controls and cleanup. Python covers server query
  bounds, all-model versus initial-DOM counts, foreign IDs and offscreen payload isolation.
- Browser: Home/End reach 001/090 while retaining bounded DOM. Select a booking in 003,
  End to 090 removes the source row, move to same-type Dirty 090 succeeds, Dirty stays,
  and focus returns to the moved booking after refresh. Keyboard navigation back to 003 and
  the reverse move succeed. Native drag rejects a different room type in Thai. A floor 1
  HTMX refresh updates the model to 30 rooms / 1,800 stays and End reaches room 030. No
  console errors/warnings were reported. Synthetic moves were restored to their original room.
- Physical touch/tablet and production-load tests were not run.

## Original #177 files

| File | Change |
| --- | --- |
| `apps/frontdesk/services/tape_chart.py` | Bounded window, scoped projections, render payload and initial rows |
| `apps/frontdesk/tape_chart_forms.py` | 14/60 selector and date bounds |
| `apps/frontdesk/tape_chart_views.py` | Window-preserving navigation/reset |
| `templates/frontdesk/tape_chart.html` | Delegated input and resize handlers |
| `templates/frontdesk/_tape_chart_panel.html` | Initial viewport, spacers and native controls |
| `static/frontdesk/tape_chart.css` | Fixed row/header geometry and control styling |
| `static/frontdesk/tape_chart.js` | Row lifecycle, keyboard, collision cache, expiry, pointer frames and refresh |
| `apps/frontdesk/tests/test_tape_chart_performance.py` | Window/query/DOM/offscreen isolation regressions |
| `apps/frontdesk/tests/tape_chart_drag.test.cjs` | Pointer/keyboard/virtualization/expiry regressions |
| `.github/workflows/ci.yml` | Node tests in CI |
| `scripts/benchmark_tape_chart.py` | Isolated, repeatable backend/HTML measurements |
| `scripts/tape_chart_metrics.js` | Fixture-only browser instrumentation |
| `docs/project/TAPE_CHART_PERFORMANCE.md` | Procedure, evidence and limitations |

## Compact rows follow-up — D, Sprint 4 (2026-11-03)

Base: main `bb2edd4c898c5768fef76e481e856723bba0370f` (#177 merged).
Branch: `codex/tape-chart-compact-rows`. The earlier results above describe #177 at
144 px, not the compact layout measured here.

Rows are now a fixed 112 px (22.2% shorter) in server metadata, SSR CSS variables,
CSS fallback and JS fallback. The header stays 64 px and overscan stays three rows.
Room closure shares the floor/type line, preserving the full type in its title.
Hidden destination buttons take no layout space; when selected they are 44 px high.
Selecting/cancelling never changes the row height. Both SSR and recycled room rows
use the same compact structure. Touch gesture/drag-handle changes belong to B's
separate tablet work; they are not included in this follow-up.

### Repeat comparison and measurements

The same existing benchmark script, Docker image, PostgreSQL instance, synthetic
90-room/60-day fixture and DEBUG=False settings were used. Main was extracted from
`git archive origin/main` inside a separate container, rather than switching the
working checkout. The main archive SHA256 was
`56260A5A670EEC34F340B2E97B487812C93067A3C48948390746B2B1576395DE`.
Each container used its own disposable database (`test_tape_benchmark_8012` / `8013`)
and loopback port. No demo reset was performed.

Backend: 20 warm repetitions per version, run sequentially after pytest finished.
Browser: five reloads per version in the same in-app browser with a requested
1265 x 712 viewport (actual reported CSS viewport 1266 x 712). No tests or backend
benchmark loops ran during browser timing. These are local samples, not a production
SLA or statistical proof of equal speed.

| Dense, 5,400 bars | Main, 144 px | Compact, 112 px |
| --- | ---: | ---: |
| Full room rows fitting below the sticky header | 2 | 3 |
| Initially mounted rows / bars | 6 / 360 | 7 / 420 |
| Live DOM nodes | 1,693 | 1,948 |
| Browser ready median / max, ms | 679.4 / 760.6 | 775.8 / 793.4 |
| Service median / p95, ms | 284.41 / 318.33 | 226.92 / 248.72 |
| Full GET median / p95, ms | 263.56 / 286.91 | 329.42 / 356.09 |
| Initial HTML bytes | 2,803,491 | 2,803,746 |
| Payload rooms / stays | 90 / 5,400 | 90 / 5,400 |
| Chart queries / full GET queries | 3 / 10 | 3 / 10 |

Browser ready samples, ms:
- Main: 760.6, 679.4, 721.1, 551.4, 644.5.
- Compact: 677.9, 775.8, 644.0, 780.6, 793.4.

More visible rows require more mounted bars: ready median in this sample increased
14.2%, while all reloads remained below the existing 1.5 s local target. The backend
service/GET split varied despite identical query counts and returned row counts.
To investigate the slower GET sample, a second loop made 20 loopback HTTP GETs per
version, alternating main/compact order each pair, after warmup with no other test
workload. HTTP median / p95 was **294.26 / 350.01 ms** for main and
**298.62 / 351.60 ms** for compact. This includes the fixture HTTP transport and is
a separate measurement from Django test-client GET. Both remained below the existing
500 ms local GET p95 target; no backend speedup is claimed.

### Functional verification

- `node --test apps/frontdesk/tests/tape_chart_drag.test.cjs`: 30 passed. Regression
  coverage includes both 112/144 px metadata, total spacer height, keyboard page size,
  selection/cancel, row 90, bounded DOM, edge scrolling and final-release hit-tests.
- `docker compose run --rm --no-deps web pytest -q apps/frontdesk/tests/test_tape_chart.py apps/frontdesk/tests/test_tape_chart_moves.py apps/frontdesk/tests/test_tape_chart_performance.py apps/properties/tests/test_permission_matrix.py`:
  248 passed in 45.13 s, including role and cross-property read/write checks.
- `docker compose run --rm --no-deps web pytest -q apps/frontdesk/tests/test_tape_chart_drag.py`:
  18 passed in 4.62 s.
- Browser: measured every mounted row at 112 px and destination controls at 44 px;
  selecting/cancelling did not change geometry. Keyboard selection in room 003,
  scrolling until the source unmounted, moving to room 090 and moving back succeeded;
  HTMX restored focus to the moved booking. Native drag to a different type was
  rejected in Thai. Home/End reached the first/last rows and bottom spacer reached zero.
- Browser at 768 x 1024 and 1024 x 768: long synthetic Thai guest/type/room labels and
  an inactive room retained their status, truncated type title and destination control
  without vertical overflow. Console had no errors/warnings. Additional QA labels were
  applied only to the disposable typical fixture after timing, not to demo data.
- Actual iPad/Android touch hardware remains untested. No FPS claim is made.
- No dependency, permission, model or migration changes are needed.
