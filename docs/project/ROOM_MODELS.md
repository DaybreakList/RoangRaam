# Room models — Sprint 1 / D

## Scope

Models, initial migration and model tests only. CRUD endpoints, role permissions,
calendar UI and date-range price updates are separate board tasks.

The implementation follows the supplied ER and constraints:

| Model | Unique fields |
| --- | --- |
| RoomType | property, name |
| Room | property, number |
| Amenity | property, name |
| RoomTypeAmenity | property, room_type, amenity |
| DailyRate | property, room_type, date |

All six models inherit PropertyScopedModel through ValidatedRoomData.
RoomTypeImage stores images using the shared properties/<property_id>/<filename> path.

## Implementation choices where the ER is silent

- Names and bed_type: 100 characters; room number: 20 characters (supports B-001).
- max_occupancy and size_sqm are required positive integers. Floor is required and
  allows negative values for basement floors. Description may be empty.
- New rooms are active and DIRTY until explicitly marked CLEAN.
- DailyRate.price uses Decimal with 12 digits and 2 decimal places; zero is allowed,
  negative values and float inputs are rejected. No default/fallback rate is invented.
- Room and DailyRate protect their referenced RoomType from deletion. Image and
  amenity links cascade with their parent. Deleting an image record does not remove
  the stored file automatically (standard Django storage behavior).
- Property cannot be reassigned after a record exists. Related RoomType and Amenity
  must belong to the same property, including when supplied as raw FK IDs.
- save() runs full_clean(). QuerySet.update() and bulk_update() are disabled because
  they bypass save(); bulk_create() validates each instance and disallows conflict
  updates. Future bulk price services should use validated saves inside atomic().
- The M2M relation uses an explicit scoped through model. Example:
  room_type.amenities.add(amenity, through_defaults={'property': room_type.property}).
- Image uploads must pass through a ModelForm or DRF serializer for content
  validation; model validation alone checks the extension, not the image bytes.

These are reviewable defaults, not claims that the team has approved new business rules.
Booking foreign keys and deletion protection for booked rooms belong to A's later work.

## Isolation boundary

Use Model.scoped.for_property(request.property) for view/service queries. The
model tests check scoped lookup returns 404 via get_object_or_404 and that scoped
delete cannot affect another property. They do not claim HTTP GET/POST/PATCH/DELETE
or role coverage: those tests must accompany the future CRUD endpoints.

ORM validation prevents invalid parent links through the supported model managers.
Direct SQL, save_base(), or manually constructing a plain Django QuerySet can bypass
it; cross-table property equality is not a database CHECK constraint. Database
unique and numeric/status CHECK constraints still apply independently of save().

## Reproduce validation with the project stack

After setting up .env and Docker:

```sh
docker compose up -d --build
docker compose exec web python manage.py migrate
docker compose exec web python manage.py makemigrations --check --dry-run
docker compose exec web python manage.py check
docker compose exec web pytest --create-db
```

Use a fresh disposable database when verifying all migrations from zero. Do not
reset an existing database or delete Compose volumes just to run these checks.

## Validation performed on 13 October 2026

- Django 5.2.18 system check: no issues.
- All migrations applied successfully to a new PostgreSQL 16.15 database.
- makemigrations --check --dry-run: no changes detected.
- Full pytest suite with --create-db: 75 passed.
- Dependencies installed unchanged from requirements.txt in the ignored .venv.
- Docker was unavailable. Validation used a disposable localhost-only PostgreSQL
  instance from EDB's portable binaries; the Compose startup itself was not tested.
- Portable binaries source: https://www.enterprisedb.com/download-postgresql-binaries
