"""Library-source selection and its one-way persistence.

The configured source is intentionally not authoritative after first use: users
can edit YAML, but they must not be able to mix Arr and Plex identities in one
database.
"""
import logging

from sqlalchemy.exc import SQLAlchemyError

from app.config import settings

logger = logging.getLogger(__name__)


def selected_library_source():
    """Return the locked source when present, otherwise the setup selection."""
    from app.database import System, database, select

    try:
        system = database.execute(select(System)).scalars().first()
        # SQLAlchemy builds the mapped result from the database's actual
        # columns. During startup an old schema can therefore return a row
        # without this newly-added column, even though the Python model has
        # it. Reading it raises AttributeError until Alembic has upgraded.
        source = system.library_source if system else None
    except (SQLAlchemyError, AttributeError):
        # Scheduler/SignalR modules are imported before Alembic has upgraded an
        # old database during startup.  The configuration is the only safe
        # answer in that short window.
        return settings.general.library_source
    if source:
        return source
    return settings.general.library_source


def using_plex_library():
    return selected_library_source() == 'plex'


def lock_library_source(source=None):
    """Persist a source on first sync and reject an incompatible later source."""
    from app.database import System, database, update, select

    source = source or settings.general.library_source
    if source not in ('arr', 'plex'):
        raise ValueError(f'Unknown library source: {source}')
    system = database.execute(select(System)).scalars().first()
    if not system:
        return source
    if system.library_source and system.library_source != source:
        raise ValueError(f"BAZARR library source is locked to {system.library_source}")
    if not system.library_source:
        database.execute(update(System).values(library_source=source))
        logger.info('BAZARR library source locked to %s', source)
    return source


def validate_library_source_change(source):
    """Called before saving settings so UI/API changes fail rather than mix data."""
    from app.database import System, database, select

    system = database.execute(select(System)).scalars().first()
    if system and system.library_source and system.library_source != source:
        raise ValueError(f"Library source is already locked to {system.library_source}")
