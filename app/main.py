from fastapi import FastAPI

from app.api.v1.auth import router as auth_router


app = FastAPI(
    title="ERP System API",
    version="1.0.0",
)


app.include_router(
    auth_router,
    prefix="/api/v1",
)


@app.get("/")
async def root():
    return {"message": "ERP API is running"}


@app.get("/health")
async def health():
    return {"status": "ok"}