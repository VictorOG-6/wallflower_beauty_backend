from fastapi import Depends
from sqlmodel import create_engine, SQLModel, Session
from typing import Annotated
from dotenv import load_dotenv
import os

load_dotenv()


def _resolve_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url and "${" not in url:
        return url

    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB")

    if not all([user, password, db]):
        raise RuntimeError(
            "Database configuration incomplete. Set DATABASE_URL or "
            "POSTGRES_USER, POSTGRES_PASSWORD, and POSTGRES_DB."
        )

    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


DATABASE_URL = _resolve_database_url()

SQL_ECHO = os.getenv("SQL_ECHO", "false").lower() in ("1", "true", "yes")

engine = create_engine(
    DATABASE_URL,
    echo=SQL_ECHO,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

def create_db_and_tables():
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session

SessionDep = Annotated[Session, Depends(get_session)]