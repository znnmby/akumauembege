"""Application error helpers."""

from typing import NoReturn

from fastapi import HTTPException


def fail(status: int, message: str, code: str) -> NoReturn:
    raise HTTPException(status_code=status, detail={"message": message, "code": code})
