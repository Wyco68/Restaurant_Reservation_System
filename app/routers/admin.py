"""Admin router - runtime control of the guest_name migration.

GET reports the phase and the live schema state; PUT moves to another phase.
A PUT that the schema or data cannot support yet returns 409 with the
reason, so the migration cannot be driven out of order. See app/migration.py.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app import migration
from app.db.postgres import engine
from app.migration import Phase
from app.models.user import UserRole
from app.security import require_role

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_role(UserRole.ADMIN))],
    responses={
        401: {"description": "Missing or invalid token"},
        403: {"description": "Not an admin"},
    },
)


class MigrationState(BaseModel):
    phase: Phase
    writes: list[str]
    reads: list[str]
    guest_name_present: bool
    guest_name_nullable: bool
    new_columns_present: bool
    awaiting_backfill: int | None
    mismatched: int | None
    missing_legacy: int | None
    allowed_phases: list[Phase]


class PhaseChange(BaseModel):
    phase: Phase


async def _state() -> MigrationState:
    async with engine.connect() as conn:
        s = await migration.schema_state(conn)
    return MigrationState(
        phase=migration.current(),
        writes=list(migration.write_columns()),
        reads=list(migration.read_columns()),
        guest_name_present=s.guest_name_present,
        guest_name_nullable=s.guest_name_nullable,
        new_columns_present=s.new_columns_present,
        awaiting_backfill=s.awaiting_backfill,
        mismatched=s.mismatched,
        missing_legacy=s.missing_legacy,
        allowed_phases=[p for p in Phase if migration.refusal(p, s) is None],
    )


@router.get("/migration", response_model=MigrationState)
async def get_migration() -> MigrationState:
    return await _state()


@router.put(
    "/migration",
    response_model=MigrationState,
    responses={409: {"description": "Schema or data not ready for that phase"}},
)
async def put_migration(change: PhaseChange) -> MigrationState:
    async with engine.connect() as conn:
        reason = migration.refusal(change.phase, await migration.schema_state(conn))
    if reason:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Cannot enter {change.phase.value}: {reason}")
    migration.set_phase(change.phase)
    return await _state()
