"""Strict provider boundary models for menu selection."""

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LLMCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    id: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(min_length=1, max_length=2000)


class LLMSelectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    region_name: str = Field(min_length=2, max_length=150)
    geographic_description: str = Field(min_length=10, max_length=2000)
    candidates: list[LLMCandidate] = Field(min_length=1)

    @field_validator("region_name", "geographic_description", mode="before")
    @classmethod
    def trim_context(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("candidates", mode="before")
    @classmethod
    def require_candidate_list(cls, value: object) -> object:
        if not isinstance(value, list):
            raise ValueError("candidates harus berupa daftar")
        return value

    @model_validator(mode="after")
    def require_unique_candidate_ids(self) -> "LLMSelectionRequest":
        candidate_ids = [candidate.id for candidate in self.candidates]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate id harus unik")
        return self


class LLMSelectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    menu_id: str | None = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    selection_reason: str = Field(min_length=1, max_length=1000)

    @field_validator("menu_id")
    @classmethod
    def validate_menu_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not 1 <= len(value) <= 64:
            raise ValueError("menu_id harus berisi 1 sampai 64 karakter")
        if not all(character.isascii() and (character.isalnum() or character in "_-") for character in value):
            raise ValueError("menu_id memiliki format yang tidak valid")
        return value

    @field_validator("selection_reason", mode="before")
    @classmethod
    def trim_selection_reason(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value
