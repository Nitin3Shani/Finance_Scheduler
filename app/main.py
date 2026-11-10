from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.database import Base, engine
from app.routers import alert_router, auth, product_router
from app.services.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    start_scheduler()
    yield
    stop_scheduler()
    await engine.dispose()


app = FastAPI(
    title="PricePulse — E-Commerce Price Tracker API",
    description="Async Amazon & Flipkart price comparison, historical analytics, and automated email alerts.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix=f"/{app.version}")
app.include_router(product_router.router, prefix=f"/{app.version}")
app.include_router(alert_router.router, prefix=f"/{app.version}")

@app.get("/health", tags=["System"])
async def health_check():
    return {"status": "ok"}