from __future__ import annotations

from datetime import date
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator


class CredentialsInput(BaseModel):
    api_id: int = Field(gt=0)
    api_hash: str = Field(min_length=16, max_length=128)

    @field_validator("api_hash")
    @classmethod
    def normalize_hash(cls, value: str) -> str:
        value = value.strip()
        if any(character.isspace() for character in value):
            raise ValueError("api_hash cannot contain whitespace")
        return value


class PhoneInput(BaseModel):
    phone: str = Field(min_length=5, max_length=32)


class CodeInput(BaseModel):
    code: str = Field(min_length=3, max_length=16)


class PasswordInput(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class OutputRootInput(BaseModel):
    path: str = Field(min_length=1, max_length=2048)


class ExportJobInput(BaseModel):
    account_id: str
    dialog_id: str
    output_root_id: str
    date_from: date | None = None
    date_to: date | None = None
    time_zone: str = Field(default="UTC", min_length=1, max_length=128)
    max_file_size_mb: int | None = Field(default=4096, ge=0, le=4_194_304)
    media_types: list[Literal["photo", "video", "file"]] = Field(
        default_factory=lambda: ["photo", "video", "file"]
    )

    @field_validator("date_to")
    @classmethod
    def date_order(cls, value: date | None, info):
        start = info.data.get("date_from")
        if value is not None and start is not None and value < start:
            raise ValueError("date_to must be on or after date_from")
        return value

    @field_validator("time_zone")
    @classmethod
    def valid_time_zone(cls, value: str) -> str:
        value = value.strip()
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("time_zone must be a valid IANA time zone") from exc
        return value

    @model_validator(mode="after")
    def complete_range_and_media(self):
        if (self.date_from is None) != (self.date_to is None):
            raise ValueError("date_from and date_to must both be set or both be null")
        if not self.media_types:
            raise ValueError("at least one media type must be selected")
        if len(set(self.media_types)) != len(self.media_types):
            raise ValueError("media_types cannot contain duplicates")
        return self
