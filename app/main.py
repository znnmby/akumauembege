import os

from fastapi import FastAPI

app = FastAPI(title=os.getenv("APP_NAME", "Buzzcut"))


@app.get("/")
async def root() -> dict[str, str]:
    return {"message": f"{app.title} API"}
