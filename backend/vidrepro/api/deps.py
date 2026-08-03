from typing import Generator

from sqlalchemy.orm import Session

from vidrepro.db.session import get_sessionmaker


def get_db() -> Generator[Session, None, None]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
