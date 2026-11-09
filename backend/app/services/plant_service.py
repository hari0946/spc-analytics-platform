"""Read-only plant listing; establishes the service-layer / pagination pattern (CRUD arrives in Phase 19)."""
from app.core import snowflake as sf
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
from app.schemas.common import PageParams
from app.schemas.plant import PlantOut

_COLS = "PLANT_ID, PLANT_CODE, PLANT_NAME, LOCATION, IS_ACTIVE, CREATED_AT"


def _table() -> str:
    # database name comes from configuration, never from request input
    return f"{get_settings().snowflake_database or 'SPC_PLATFORM'}.METADATA.PLANTS"


def _to_out(r: tuple) -> PlantOut:
    return PlantOut(plant_id=r[0], plant_code=r[1], plant_name=r[2], location=r[3], is_active=r[4], created_at=r[5])


def list_plants(p: PageParams) -> tuple[list[PlantOut], int]:
    total = sf.execute(f"SELECT COUNT(*) FROM {_table()}")[0][0]
    rows = sf.execute(
        f"SELECT {_COLS} FROM {_table()} ORDER BY PLANT_CODE LIMIT %s OFFSET %s", (p.page_size, p.offset)
    )
    return [_to_out(r) for r in rows], total


def get_plant(plant_id: str) -> PlantOut:
    rows = sf.execute(f"SELECT {_COLS} FROM {_table()} WHERE PLANT_ID = %s", (plant_id,))
    if not rows:
        raise NotFoundError(f"Plant '{plant_id}' not found")
    return _to_out(rows[0])
