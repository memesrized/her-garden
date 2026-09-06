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
]


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
    """A completed action or observation, optionally replacing an earlier event."""

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
        return self


class InventoryEvent(StrictModel):
    """Record supply activity and the reported amount remaining, without guessing."""

    event_type: Literal["purchase", "usage", "observation", "update", "void"]
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
        return self
