from datetime import datetime

from pydantic import BaseModel


class PlantOut(BaseModel):
    plant_id: str
    plant_code: str
    plant_name: str
    location: str | None
    is_active: bool
    created_at: datetime
