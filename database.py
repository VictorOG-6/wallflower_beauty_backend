from fastapi import Depends
from sqlmodel import create_engine, SQLModel, Session
from typing import Annotated
from dotenv import load_dotenv
import os
import re

load_dotenv()


def _normalize_database_url(url: str) -> str:
    # Railway/Heroku provide postgres://; SQLAlchemy expects postgresql://
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def _expand_env_vars(value: str) -> str:
    def replacer(match: re.Match[str]) -> str:
        return os.getenv(match.group(1), match.group(0))

    return re.sub(r"\$\{([^}]+)\}", replacer, value)


def _resolve_database_url() -> str:
    for env_var in ("DATABASE_PRIVATE_URL", "DATABASE_URL"):
        url = os.getenv(env_var)
        if url:
            url = _expand_env_vars(url.strip())
            if url and "${" not in url:
                return _normalize_database_url(url)

    user = os.getenv("PGUSER") or os.getenv("POSTGRES_USER")
    password = os.getenv("PGPASSWORD") or os.getenv("POSTGRES_PASSWORD")
    host = os.getenv("PGHOST") or os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("PGPORT") or os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("PGDATABASE") or os.getenv("POSTGRES_DB")

    if host == "postgres" and not any(
        os.getenv(name) for name in ("DATABASE_URL", "DATABASE_PRIVATE_URL", "PGHOST")
    ):
        raise RuntimeError(
            "POSTGRES_HOST is set to 'postgres' (a Docker Compose service name), "
            "but no Railway database URL is configured. Add a PostgreSQL service "
            "in Railway and link it to this service so DATABASE_URL is injected."
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