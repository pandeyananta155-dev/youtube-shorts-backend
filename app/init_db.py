from app.config import get_settings
from app.database import Base, create_database_engine
from app import models  # noqa: F401 - register model metadata before table creation


def main() -> None:
    engine = create_database_engine(get_settings().database_url)
    Base.metadata.create_all(bind=engine)
    engine.dispose()
    print("Database initialized.")


if __name__ == "__main__":
    main()