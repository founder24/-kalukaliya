"""Migration registry - all migrations listed in order."""

from app.db.migrations.versions.v001_initial_indexes import up as v001_up

# Ordered list of all migrations. Add new migrations at the end.
MIGRATIONS = [
    {
        "version": "v001",
        "description": "Initial indexes baseline - establishes schema_versions tracking",
        "up": v001_up,
    },
]
