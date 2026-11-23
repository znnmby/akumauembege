import os
import logging
import time 

from fastapi import FastAPI

app = FastAPI(title=os.getenv("APP_NAME", "Cafe_MerahPutih"))


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": f"{app.title} API"}
