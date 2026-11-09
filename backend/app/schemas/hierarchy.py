from pydantic import BaseModel, Field, model_validator

Code = dict(min_length=1, max_length=50, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.\- ]*$")
Name = dict(min_length=1, max_length=200)


class Out(BaseModel):
    model_config = {"extra": "allow"}


class PlantCreate(BaseModel):
    plant_code: str = Field(**Code)
    plant_name: str = Field(**Name)
    location: str | None = Field(None, max_length=200)


class PlantUpdate(BaseModel):
    plant_code: str | None = Field(None, **Code)
    plant_name: str | None = Field(None, **Name)
    location: str | None = Field(None, max_length=200)
    is_active: bool | None = None


class LineCreate(BaseModel):
    plant_id: str = Field(min_length=1, max_length=64)
    line_code: str = Field(**Code)
    line_name: str = Field(**Name)


class LineUpdate(BaseModel):
    line_code: str | None = Field(None, **Code)
    line_name: str | None = Field(None, **Name)
    is_active: bool | None = None


class AssetCreate(BaseModel):
    line_id: str = Field(min_length=1, max_length=64)
    asset_code: str = Field(**Code)
    asset_name: str = Field(**Name)
    asset_type: str | None = Field(None, max_length=100)


class AssetUpdate(BaseModel):
    asset_code: str | None = Field(None, **Code)
    asset_name: str | None = Field(None, **Name)
    asset_type: str | None = Field(None, max_length=100)
    is_active: bool | None = None


class _Limits(BaseModel):
    lsl: float | None = None
    usl: float | None = None
    target: float | None = None

    @model_validator(mode="after")
    def _limits(self):
        if self.lsl is not None and self.usl is not None and self.lsl >= self.usl:
            raise ValueError("LSL must be less than USL")
        if self.target is not None and ((self.lsl is not None and self.target < self.lsl) or (self.usl is not None and self.target > self.usl)):
            raise ValueError("Target must lie within the specification limits")
        return self


class TagCreate(_Limits):
    asset_id: str = Field(min_length=1, max_length=64)
    tag_name: str = Field(**Code)
    parameter: str = Field(min_length=1, max_length=100)
    unit: str | None = Field(None, max_length=30)


class TagUpdate(BaseModel):
    tag_name: str | None = Field(None, **Code)
    parameter: str | None = Field(None, min_length=1, max_length=100)
    unit: str | None = Field(None, max_length=30)
    lsl: float | None = None
    usl: float | None = None
    target: float | None = None
    is_active: bool | None = None
