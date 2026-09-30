"""add Plex library source metadata

Revision ID: a6d7e8f9012a
Revises: 537e9b4d10e3
"""
from alembic import op
import sqlalchemy as sa

revision = 'a6d7e8f9012a'
down_revision = '537e9b4d10e3'
branch_labels = None
depends_on = None


def _columns(table):
    return {column['name'] for column in sa.inspect(op.get_bind()).get_columns(table)}


def _add(table, column):
    if column.name not in _columns(table):
        with op.batch_alter_table(table) as batch:
            batch.add_column(column)


def _bigint(table, column):
    columns = {item['name']: item for item in sa.inspect(op.get_bind()).get_columns(table)}
    if column not in columns or isinstance(columns[column]['type'], sa.BigInteger):
        return
    with op.batch_alter_table(table) as batch:
        batch.alter_column(column, existing_type=columns[column]['type'], type_=sa.BigInteger())


def upgrade():
    _add('system', sa.Column('library_source', sa.Text(), nullable=True))
    # A populated system row predates this feature.  It is an Arr database and
    # must not silently become a Plex database on upgrade.
    op.execute("UPDATE system SET library_source = 'arr' WHERE library_source IS NULL")
    for table in ('table_movies', 'table_episodes'):
        _add(table, sa.Column('plex_server_id', sa.Text(), nullable=True))
        _add(table, sa.Column('plex_library_id', sa.Integer(), nullable=True))
        _add(table, sa.Column('plex_item_id', sa.BigInteger(), nullable=True))
        _add(table, sa.Column('plex_part_id', sa.BigInteger(), nullable=True))
    for column in (
        sa.Column('plex_server_id', sa.Text(), nullable=True),
        sa.Column('plex_library_id', sa.Integer(), nullable=True),
        sa.Column('plex_item_id', sa.BigInteger(), nullable=True),
    ):
        _add('table_shows', column)
    # Plex media-part IDs are 64-bit.  Widen child keys before their parents
    # so PostgreSQL retains valid foreign keys as well as SQLite's batch copy.
    for table, column in (
        ('table_blacklist', 'sonarr_episode_id'), ('table_blacklist', 'sonarr_series_id'),
        ('table_history', 'sonarrEpisodeId'), ('table_history', 'sonarrSeriesId'),
        ('table_episodes_subtitles', 'sonarrEpisodeId'), ('table_episodes_subtitles', 'sonarrSeriesId'),
        ('table_blacklist_movie', 'radarr_id'), ('table_history_movie', 'radarrId'),
        ('table_movies_subtitles', 'radarrId'), ('table_episodes', 'episode_file_id'),
        ('table_episodes', 'sonarrEpisodeId'), ('table_episodes', 'sonarrSeriesId'),
        ('table_movies', 'movie_file_id'), ('table_movies', 'radarrId'), ('table_shows', 'sonarrSeriesId'),
    ):
        _bigint(table, column)
    # Radarr has one row per TMDb title, while Plex can have several versions
    # of the same title.  Keep the actual TMDb identifier on every part.
    constraints = sa.inspect(op.get_bind()).get_unique_constraints('table_movies')
    for constraint in constraints:
        if constraint.get('column_names') == ['tmdbId'] and constraint.get('name'):
            with op.batch_alter_table('table_movies') as batch:
                batch.drop_constraint(constraint['name'], type_='unique')
            break


def downgrade():
    pass
