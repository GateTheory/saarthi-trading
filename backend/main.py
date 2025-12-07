# backend/main.py
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from dotenv import load_dotenv

# Import database initialization
from backend.database import init_db

# Import routers
from backend.routes.auth import router as auth_router
from backend.routes.trading import router as trading_router
from backend.routes.user_orders import router as user_orders_router

load_dotenv()

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s in %(module)s: %(message)s'
)
logger = logging.getLogger("backend")

# Application metadata
APP_NAME = os.getenv("APP_NAME", "Saarthi Trading Platform")
APP_VERSION = os.getenv("APP_VERSION", "2.0.0")
DEBUG = os.getenv("DEBUG", "false").lower() == "true"

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan events.
    Runs on startup and shutdown.
    """
    # Startup
    logger.info(f"🚀 Starting {APP_NAME} v{APP_VERSION}")
    logger.info("📊 Initializing database...")
    init_db()
    logger.info("✅ Database initialized")
    
    yield
    
    # Shutdown
    logger.info("👋 Shutting down application...")

# Create FastAPI application
app = FastAPI(
    title=APP_NAME,
    version=APP_VERSION,
    description="Advanced crypto futures trading platform with authentication and risk management",
    lifespan=lifespan,
    docs_url="/api/docs" if DEBUG else None,
    redoc_url="/api/redoc" if DEBUG else None,
)

# ---------------- CORS CONFIG (unchanged) ----------------
origins = [
    "http://127.0.0.1:8080",
    "http://localhost:8080",
    "http://127.0.0.1:5500",
    "http://localhost:5500",
    "http://127.0.0.1:8000",
    "http://localhost:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Custom exception handlers
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error(f"Validation error: {exc.errors()}")
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": exc.errors(),
            "body": exc.body,
        },
    )

@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    logger.exception(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal server error"},
    )

# Include routers
app.include_router(
    auth_router,
    prefix="/api/auth",
    tags=["Authentication"]
)

# 🔴 CHANGED: prefix from "/api/trade" -> "/trade"
app.include_router(
    trading_router,
    prefix="/trade",
    tags=["Trading"]
)

app.include_router(
    user_orders_router,
    prefix="/api/orders",
    tags=["Orders"]
)

# Health check endpoints
@app.get("/")
async def root():
    return {
        "name": APP_NAME,
        "version": APP_VERSION,
        "status": "operational",
        "endpoints": {
            "docs": "/api/docs" if DEBUG else "disabled",
            "health": "/health",
            "auth": "/api/auth",
            "trading": "/trade",
            "orders": "/api/orders"
        }
    }

@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "version": APP_VERSION,
        "timestamp": "2024-01-01T00:00:00Z"
    }

@app.get("/api/status")
async def api_status():
    return {
        "api": "operational",
        "version": APP_VERSION,
        "environment": "production" if not DEBUG else "development",
        "features": {
            "authentication": "enabled",
            "trading": "enabled",
            "websocket": "enabled",
            "database": "enabled"
        }
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=DEBUG,
        log_level="info"
    )
