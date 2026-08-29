from fastapi import Depends
from sqlmodel import create_engine, SQLModel, Session
from typing import Annotated
from dotenv import load_dotenv
import os
import re

load_dotenv()


def _normalize_database_url(url: str) -> str:
    # Render/Heroku provide postgres://; SQLAlchemy expects postgresql://
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def _expand_env_vars(value: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        return os.getenv(match.group(1), match.group(0))

    return re.sub(r"\$\{([^}]+)\}", replacer, value)


def _resolve_database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        url = _expand_env_vars(url.strip())
        if url and "${" not in url:
            return _normalize_database_url(url)

    user = os.getenv("POSTGRES_USER")
    password = os.getenv("POSTGRES_PASSWORD")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB")

    if host == "postgres" and not os.getenv("DATABASE_URL"):
        raise RuntimeError(
            "POSTGRES_HOST is set to 'postgres' (a Docker Compose service name), "
            "but DATABASE_URL is not configured. On Render, create/link a "
            "PostgreSQL database and set DATABASE_URL from the Render dashboard."
        )

    if not all([user, password, db]):
        raise RuntimeError(
            "Database configuration incomplete. Set DATABASE_URL or "
            "POSTGRES_USER, POSTGRES_PASSWORD, and POSTGRES_DB."
        )

    return _normalize_database_url(
        f"postgresql://{user}:{password}@{host}:{port}/{db}"
    )


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