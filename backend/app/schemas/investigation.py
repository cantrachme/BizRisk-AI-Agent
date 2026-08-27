from pydantic import BaseModel, Field


class InvestigationCreate(BaseModel):
    business_name: str | None = Field(default=None, max_length=255)
    gstin: str | None = Field(default=None, max_length=15)
    cin: str | None = Field(default=None, max_length=21)
    website: str | None = Field(default=None, max_length=500)
    location: str | None = Field(default=None, max_length=255)
    additional_information: str | None = None
    user_id: str | None = Field(default=None, max_length=100)
