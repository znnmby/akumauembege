"""Liveness endpoint with no downstream dependency checks."""

from typing import Literal

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health", response_model=dict[str, Literal["ok"]])
async def health() -> dict[str, Literal["ok"]]:
    return {"status": "ok"}
