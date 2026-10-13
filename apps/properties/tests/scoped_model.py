import pytest
from django.db import connection, models

from apps.properties.models import PropertyScopedModel


class ScopedThing(PropertyScopedModel):
    """Concrete model used only to exercise PropertyScopedModel in tests."""

    name = models.CharField(max_length=50)

    class Meta:
        app_label = 'properties'


@pytest.fixture
def scoped_thing_table(db):
    # PostgreSQL DDL is transactional: the table is dropped by the test's rollback.
    with connection.schema_editor() as editor:
        editor.create_model(ScopedThing)
    return ScopedThing
