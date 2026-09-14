"""Typed event payloads shared by the MCP interface and projection logic."""

from datetime import UTC, datetime
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

ShortText = Annotated[str, StringConstraints(min_length=1, max_length=200, strip_whitespace=True)]
Note = Annotated[str, Field(min_length=1, max_length=4000)]
PlantAction = Literal[
    "watering",
    "fertilizing",
    "repotting",
    "moving",
    "pruning",
    "treatment",
    "propagation",
    "observation",
    "update",
    "void",
    "archive",
    "restore",
]
LocationAction = Literal["rename", "archive", "restore"]


class StrictModel(BaseModel):
    """Reject misspelled fields instead of silently losing user information."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class PlantState(StrictModel):
    """User-reported plant attributes; explicit null clears optional fields."""

    name: ShortText | None = None
    species: ShortText | None = None
    aliases: list[ShortText] | None = Field(default=None, max_length=30)
    location_id: UUID | None = None
    pot: ShortText | None = None
    substrate: ShortText | None = None
    status: Literal["active", "dormant", "dead", "given_away"] | None = None

    @model_validator(mode="after")
    def validate_required_values(self) -> Self:
        """Keep name and status meaningful whenever supplied."""
        for field in ("name", "status", "aliases"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        return self


class PlantEvent(StrictModel):
    """A completed action, observation, or lifecycle change."""

    event_type: PlantAction
    occurred_at: AwareDatetime
    note: Note
    changes: PlantState = Field(default_factory=PlantState)
    supersedes_event_id: UUID | None = None

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        """Reject future completions and incomplete state-changing events."""
        if self.occurred_at > datetime.now(UTC):
            raise ValueError("Only completed actions or observations may be recorded")
        changes = self.changes.model_fields_set
        if self.event_type == "moving" and "location_id" not in changes:
            raise ValueError("Moving requires changes.location_id")
        if self.event_type == "observation" and changes:
            raise ValueError("Observations belong in note; use update for attribute changes")
        if self.event_type == "void" and (not self.supersedes_event_id or changes):
            raise ValueError("Void requires a superseded event and no changes")
        if self.event_type in {"archive", "restore"} and (
            self.supersedes_event_id or changes
        ):
            raise ValueError("Archive and restore events cannot change or supersede facts")
        return self


class LocationEvent(StrictModel):
    """Rename, archive, or restore a location without removing its history."""

    event_type: LocationAction
    occurred_at: AwareDatetime
    note: Note
    name: ShortText | None = None

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        """Require completed events and a name only when renaming."""
        if self.occurred_at > datetime.now(UTC):
            raise ValueError("Cannot record a future location event")
        if self.event_type == "rename" and not self.name:
            raise ValueError("Rename requires name")
        if self.event_type != "rename" and "name" in self.model_fields_set:
            raise ValueError("Archive and restore events cannot rename a location")
        return self


class InventoryEvent(StrictModel):
    """Record supply activity, corrections, and lifecycle changes without guessing."""

    event_type: Literal[
        "purchase", "usage", "observation", "update", "void", "archive", "restore"
    ]
    occurred_at: AwareDatetime
    note: Note
    name: ShortText | None = None
    category: ShortText | None = None
    remaining: ShortText | None = Field(
        default=None, description="Amount AFTER this event, e.g. half a bag; null means unknown"
    )
    supersedes_event_id: UUID | None = None

    @model_validator(mode="after")
    def validate_event(self) -> Self:
        """Require completed events and valid voids."""
        if self.occurred_at > datetime.now(UTC):
            raise ValueError("Cannot record a future inventory event")
        if self.event_type == "void" and not self.supersedes_event_id:
            raise ValueError("Void requires supersedes_event_id")
        lifecycle_fields = {"name", "category", "remaining"} & self.model_fields_set
        if self.event_type in {"archive", "restore"} and (
            self.supersedes_event_id or lifecycle_fields
        ):
            raise ValueError("Archive and restore events cannot change or supersede facts")
        return self
