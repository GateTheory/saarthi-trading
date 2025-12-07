# backend/database.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from backend.models.database import Base
from dotenv import load_dotenv

load_dotenv()

# Default SQLite file database
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./saarthi.db")

# Create SQLAlchemy engine depending on database type
if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},  # required for SQLite multithreading
        future=True,
    )
else:
    # For PostgreSQL / MySQL etc. (not MongoDB)
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,
        future=True,
    )

# Session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine
)

# Dependency for routes
def get_db():
    """
    Dependency injection for FastAPI routes.
    Usage: db: Session = Depends(get_db)
    """
    db: Session = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Create tables at startup
def init_db():
    """
    Initialize database schema
    """
    Base.metadata.create_all(bind=engine)
    print("✅ SQLite database initialized and tables created")

def drop_db():
    """
    Drop all tables - development use only
    """
    Base.metadata.drop_all(bind=engine)
    print("⚠️ All tables dropped")
