"""Synchronise Bazarr's library tables from Plex.

Plex uses a metadata rating key for an item and a globally unique media-part
identifier for a physical file.  The latter deliberately backs the old Arr
file IDs: it lets the rest of Bazarr continue to operate on one file at a time.
"""
import logging
import os
from datetime import datetime

from app.config import settings
from app.database import (TableEpisodes, TableLanguagesProfiles, TableMovies, TableShows, database, delete, insert,
                          select, update)
from app.event_handler import event_stream
from app.jobs_queue import jobs_queue
from languages.get_languages import language_from_alpha2, language_from_alpha3
from subtitles.indexer.movies import store_subtitles_movie
from subtitles.indexer.series import store_subtitles
from subtitles.mass_download import episode_download_subtitles, movies_download_subtitles
from utilities.library_source import lock_library_source, using_plex_library
from utilities.path_mappings import path_mappings
from utilities.video_analyzer import embedded_audio_reader

from .operations import get_plex_server

logger = logging.getLogger(__name__)


def _as_list(value):
    if isinstance(value, list):
        return value
    return [value] if value else []


def _value(obj, name, default=None):
    return getattr(obj, name, default)


def _guids(video):
    result = {}
    guids = [_value(guid, 'id', guid) for guid in (_value(video, 'guids', []) or [])]
    # Older Plex agents expose external IDs only in the legacy GUID
    # attributes (for example ``com.plexapp.agents.thetvdb://76894?lang=fr``)
    # instead of as nested ``<Guid id="tvdb://76894">`` elements.
    guids.extend(_value(video, attribute) for attribute in ('guid', 'parentGuid', 'grandparentGuid'))
    for guid in guids:
        if not isinstance(guid, str) or '://' not in guid:
            continue
        source, value = guid.split('://', 1)
        source = {
            'com.plexapp.agents.thetvdb': 'tvdb',
            'com.plexapp.agents.themoviedb': 'tmdb',
            'com.plexapp.agents.imdb': 'imdb',
        }.get(source, source)
        # Agent GUIDs for seasons and episodes append their numbers after the
        # series ID, and may carry a language query parameter.
        value = value.split('?', 1)[0].split('/', 1)[0]
        if source in ('imdb', 'tmdb', 'tvdb'):
            # Prefer current Plex GUID elements when both formats are present.
            result.setdefault(source, value)
    return result


def _labels(video):
    return [_value(label, 'tag', str(label)) for label in (_value(video, 'labels', []) or [])]


def _hydrate(item):
    """Load details when Plex returned a partial listing object."""
    try:
        if hasattr(item, 'isFullObject') and not item.isFullObject():
            item.reload()
    except Exception:
        logger.debug('Unable to hydrate Plex item %s; using listing metadata', _value(item, 'ratingKey', '?'))
    return item


def _profile(labels, media_type, is_new):
    default_enabled = settings.general.movie_default_enabled if media_type == 'movie' \
        else settings.general.serie_default_enabled
    default_profile = settings.general.movie_default_profile if media_type == 'movie' \
        else settings.general.serie_default_profile
    profile = default_profile if is_new and default_enabled and default_profile != '' else None
    tags_enabled = settings.general.movie_tag_enabled if media_type == 'movie' else settings.general.serie_tag_enabled
    if tags_enabled:
        profiles = database.execute(
            select(TableLanguagesProfiles.profileId, TableLanguagesProfiles.tag)).all()
        for profile_id, tag in profiles:
            if tag and tag in labels:
                profile = profile_id
                break
        if set(labels) & set(settings.general.remove_profile_tags):
            profile = None
    # Existing items keep manually assigned/default profiles unless label
    # matching is enabled, exactly like the Arr parsers.
    return profile if (is_new or tags_enabled) else None


def _parts(video):
    for media in _value(video, 'media', []) or []:
        for part in _value(media, 'parts', []) or []:
            path = _value(part, 'file')
            part_id = _value(part, 'id')
            if not path or part_id is None:
                continue
            # Plex has already identified this as a physical media part.  Do
            # not apply Arr's minimum-size heuristic: short films, specials,
            # and alternate cuts are still library media the user selected.
            if _value(part, 'exists', True) is False:
                continue
            if int(_value(part, 'size', 0) or 0) > 0 or \
                    (settings.general.enable_strm_support and path.lower().endswith('.strm')):
                yield media, part


def _as_values(value):
    """Return a Plex property or method result as a list."""
    try:
        value = value() if callable(value) else value
    except Exception:
        return []
    if value is None:
        return []
    return value if isinstance(value, (list, tuple)) else [value]


def _audio_language_name(stream):
    """Normalize Plex's language/name/code variants to Bazarr language names."""
    for attribute in ('language', 'languageTag', 'languageCode'):
        value = _value(stream, attribute)
        if not isinstance(value, str) or not value.strip():
            continue
        value = value.strip()
        normalized = value.replace('_', '-').split('-', 1)[0]
        if len(normalized) == 2:
            language = language_from_alpha2(normalized.lower())
            if language:
                return language
        if len(normalized) == 3:
            language = language_from_alpha3(normalized.lower())
            if language:
                return language
        # Plex's `language` is normally the Bazarr display name (for example
        # "Japanese"). Preserve it when Plex supplied a human-readable value.
        if len(value) > 3:
            return value
    return None


def _plex_audio_languages(video, part):
    """Read audio languages from full Plex Media/MediaPart stream metadata."""
    streams = []
    for media in _value(video, 'media', []) or []:
        parts = _as_values(_value(media, 'parts', []))
        if part not in parts and _value(part, 'id') not in {_value(item, 'id') for item in parts}:
            continue
        streams.extend((stream, True) for stream in _as_values(_value(media, 'audioStreams')))
        streams.extend((stream, False) for stream in _as_values(_value(media, 'streams')))
    streams.extend((stream, True) for stream in _as_values(_value(part, 'audioStreams')))
    streams.extend((stream, False) for stream in _as_values(_value(part, 'streams')))

    languages = []
    for stream, known_audio_stream in streams:
        stream_type = _value(stream, 'streamType') or _value(stream, 'type')
        if not known_audio_stream and stream_type not in (2, '2', 'audio'):
            continue
        language = _audio_language_name(stream)
        if language and language not in languages:
            languages.append(language)
    return languages


def _audio_languages(video, part, media_type):
    """Prefer Plex streams; use local deep analysis when explicitly enabled."""
    languages = _plex_audio_languages(video, part)
    if languages or not settings.general.parse_embedded_audio_track:
        return languages

    mapped_path = path_mappings.path_replace_movie(_value(part, 'file')) if media_type == 'movie' \
        else path_mappings.path_replace(_value(part, 'file'))
    if not os.path.exists(mapped_path):
        return languages
    try:
        return embedded_audio_reader(
            mapped_path,
            file_size=int(_value(part, 'size', 0) or 0),
            movie_file_id=int(_value(part, 'id')) if media_type == 'movie' else None,
            episode_file_id=int(_value(part, 'id')) if media_type == 'series' else None,
            use_cache=True,
        )
    except Exception:
        logger.debug('Unable to read embedded audio tracks for Plex part %s', _value(part, 'id'), exc_info=True)
        return []


def _part_row(video, part, show, library_id, server_id):
    """Return the common Plex provenance carried by every file row."""
    return {
        'plex_server_id': str(server_id),
        'plex_library_id': int(library_id),
        'plex_item_id': int(_value(video, 'ratingKey')),
        'plex_part_id': int(_value(part, 'id')),
        'path': _value(part, 'file'),
        'file_size': int(_value(part, 'size', 0) or 0),
        'format': _value(part, 'container') or _value(_value(video, 'media', [None])[0], 'container'),
        'video_codec': _value(_value(video, 'media', [None])[0], 'videoCodec'),
        'audio_codec': _value(_value(video, 'media', [None])[0], 'audioCodec'),
        'audio_language': str(_audio_languages(video, part, 'movie' if show is None else 'series')),
        'monitored': 'True',
    }


def _mapped_part_is_available(part, media_type):
    """Whether Bazarr can currently reach Plex's media part locally.

    Database paths deliberately remain Plex's original paths.  Every
    filesystem operation uses Bazarr's established media-type-specific path
    mappings, which also lets reverse mapping put downloaded subtitle paths
    back in the same stored namespace.
    """
    original_path = _value(part, 'file')
    mapped_path = path_mappings.path_replace_movie(original_path) if media_type == 'movie' \
        else path_mappings.path_replace(original_path)
    return os.path.exists(mapped_path)


def _upsert_movie(movie, part, library_id, server_id):
    ids = _guids(movie)
    part_id = int(_value(part, 'id'))
    labels = _labels(movie)
    existing = database.execute(select(TableMovies).where(TableMovies.radarrId == part_id)).scalar_one_or_none()
    fallback_tmdb = f'plex-{server_id}-{library_id}-{_value(movie, "ratingKey")}-{part_id}'
    row = _part_row(movie, part, None, library_id, server_id)
    row.update({
        'radarrId': part_id,
        'movie_file_id': part_id,
        'title': _value(movie, 'title', 'Unknown'),
        'sortTitle': _value(movie, 'titleSort') or _value(movie, 'title', 'Unknown'),
        'year': str(_value(movie, 'year', '') or ''),
        'tmdbId': ids.get('tmdb', fallback_tmdb),
        'imdbId': ids.get('imdb'),
        'overview': _value(movie, 'summary', '') or '',
        'poster': _value(movie, 'thumb', '') or '',
        'fanart': _value(movie, 'art', '') or '',
        'alternativeTitles': '[]',
        'sceneName': None,
        'resolution': str(_value(_value(movie, 'media', [None])[0], 'videoResolution') or ''),
        'tags': str(labels),
        'originalLanguage': None,
    })
    if existing:
        if settings.general.movie_tag_enabled:
            row['profileId'] = _profile(labels, 'movie', False)
        database.execute(update(TableMovies).values(**row, updated_at_timestamp=datetime.now())
                         .where(TableMovies.radarrId == part_id))
        changed = existing.path != row['path'] or existing.file_size != row['file_size']
    else:
        row['profileId'] = _profile(labels, 'movie', True)
        row['created_at_timestamp'] = datetime.now()
        database.execute(insert(TableMovies).values(row))
        changed = True
    if changed:
        store_subtitles_movie(part_id)
        event_stream(type='movie', action='update', payload=part_id)
        if _mapped_part_is_available(part, 'movie'):
            _queue_movie_download(part_id, row['title'])
        else:
            logger.info('Deferring subtitle download for Plex movie until mapped path is available: %s', row['path'])
    return part_id


def _upsert_show(show, library_id, server_id, path):
    show_id = int(_value(show, 'ratingKey'))
    ids = _guids(show)
    labels = _labels(show)
    existing = database.execute(select(TableShows).where(TableShows.sonarrSeriesId == show_id)).scalar_one_or_none()
    # Plex does not expose a show filesystem path.  The first managed episode
    # directory keeps old series-level consumers working without inventing a
    # non-filesystem URI.
    row = {
        'sonarrSeriesId': show_id, 'plex_server_id': str(server_id), 'plex_library_id': int(library_id),
        'plex_item_id': show_id, 'title': _value(show, 'title', 'Unknown'),
        'sortTitle': _value(show, 'titleSort') or _value(show, 'title', 'Unknown'), 'path': path,
        'tvdbId': int(ids['tvdb']) if ids.get('tvdb', '').isdigit() else None, 'imdbId': ids.get('imdb'),
        'year': str(_value(show, 'year', '') or ''), 'overview': _value(show, 'summary', '') or '',
        'poster': _value(show, 'thumb', '') or '', 'fanart': _value(show, 'art', '') or '',
        'alternativeTitles': '[]', 'tags': str(labels), 'monitored': 'True', 'ended': 'False',
        'seriesType': 'Standard', 'lastAired': None, 'originalLanguage': None, 'audio_language': '[]',
    }
    if existing:
        if settings.general.serie_tag_enabled:
            row['profileId'] = _profile(labels, 'series', False)
        database.execute(update(TableShows).values(**row, updated_at_timestamp=datetime.now())
                         .where(TableShows.sonarrSeriesId == show_id))
    else:
        row['profileId'] = _profile(labels, 'series', True)
        row['created_at_timestamp'] = datetime.now()
        database.execute(insert(TableShows).values(row))
        event_stream(type='series', action='update', payload=show_id)
    return show_id


def _upsert_episode(episode, show, part, library_id, server_id, show_id):
    part_id = int(_value(part, 'id'))
    existing = database.execute(select(TableEpisodes).where(TableEpisodes.sonarrEpisodeId == part_id)).scalar_one_or_none()
    row = _part_row(episode, part, show, library_id, server_id)
    row.update({
        'sonarrEpisodeId': part_id, 'sonarrSeriesId': show_id, 'episode_file_id': part_id,
        'title': _value(episode, 'title', 'Unknown'), 'season': int(_value(episode, 'parentIndex', 0) or 0),
        'episode': int(_value(episode, 'index', 0) or 0), 'absoluteEpisode': _value(episode, 'absoluteIndex'),
        'tvdbId': int(_guids(episode).get('tvdb')) if _guids(episode).get('tvdb', '').isdigit() else None,
        'sceneName': None,
    })
    if existing:
        database.execute(update(TableEpisodes).values(**row, updated_at_timestamp=datetime.now())
                         .where(TableEpisodes.sonarrEpisodeId == part_id))
        changed = existing.path != row['path'] or existing.file_size != row['file_size']
    else:
        row['created_at_timestamp'] = datetime.now()
        database.execute(insert(TableEpisodes).values(row))
        changed = True
    if changed:
        store_subtitles(part_id)
        event_stream(type='episode', action='update', payload=part_id)
        if _mapped_part_is_available(part, 'series'):
            _queue_episode_download(part_id, _value(show, 'title', 'Unknown'))
        else:
            logger.info('Deferring subtitle download for Plex episode until mapped path is available: %s', row['path'])
    return part_id


def _queue_movie_download(part_id, title):
    jobs_queue.feed_jobs_pending_queue(job_name=f'Downloading missing subtitles for {title}',
                                       module='subtitles.mass_download.movies', func='movies_download_subtitles',
                                       args=[], kwargs={'no': part_id})


def _queue_episode_download(part_id, show_title):
    jobs_queue.feed_jobs_pending_queue(job_name=f'Downloading missing subtitles for {show_title}',
                                       module='subtitles.mass_download.series', func='episode_download_subtitles',
                                       args=[], kwargs={'no': part_id})


def _section(plex, library_id):
    try:
        return plex.library.sectionByID(int(library_id))
    except (AttributeError, TypeError, ValueError):
        return plex.library.section(library_id)


def _chunks(values, size=500):
    """Yield bind-safe batches for SQLite and PostgreSQL deletes."""
    values = list(values)
    for start in range(0, len(values), size):
        yield values[start:start + size]


def _delete_missing_parts(model, identifier, server_id, section_id, seen):
    """Reconcile one fully scanned Plex section without a giant NOT IN query.

    SQLite has a finite number of bind variables per statement. A large TV
    library can exceed it with `identifier.not_in(seen)`, so obtain the small
    difference first and delete only the genuinely removed rows in batches.
    """
    scope = (model.plex_server_id == str(server_id), model.plex_library_id == section_id)
    existing = database.execute(select(identifier).where(*scope)).scalars().all()
    missing = set(existing) - set(seen)
    for batch in _chunks(missing):
        database.execute(delete(model).where(*scope, identifier.in_(batch)))


def _sync_movies(plex, library_ids, server_id, seen):
    for library_id in library_ids:
        section = _section(plex, library_id)
        section_id = int(_value(section, 'key', library_id))
        section_seen = set()
        for movie in section.all():
            movie = _hydrate(movie)
            for _, part in _parts(movie):
                part_id = _upsert_movie(movie, part, section_id, server_id)
                seen.add(part_id)
                section_seen.add(part_id)
        # Only remove rows after this entire section completed successfully.
        _delete_missing_parts(TableMovies, TableMovies.radarrId, server_id, section_id, section_seen)


def _sync_shows(plex, library_ids, server_id, seen):
    for library_id in library_ids:
        section = _section(plex, library_id)
        section_id = int(_value(section, 'key', library_id))
        section_seen = set()
        section_episode_seen = set()
        for show in section.all():
            show = _hydrate(show)
            episodes = [_hydrate(episode) for episode in show.episodes()]
            first_path = next((os.path.dirname(_value(part, 'file')) for episode in episodes
                               for _, part in _parts(episode)), None)
            if not first_path:
                continue
            show_id = _upsert_show(show, section_id, server_id, first_path)
            for episode in episodes:
                for _, part in _parts(episode):
                    part_id = _upsert_episode(episode, show, part, section_id, server_id, show_id)
                    seen.add(part_id)
                    section_episode_seen.add(part_id)
                    section_seen.add(show_id)
        _delete_missing_parts(TableEpisodes, TableEpisodes.sonarrEpisodeId, server_id, section_id,
                              section_episode_seen)
        _delete_missing_parts(TableShows, TableShows.sonarrSeriesId, server_id, section_id, section_seen)


def sync_plex_library(job_id=None, wait_for_completion=False):
    """Synchronise all selected sections.  It is safe to run from polling or a webhook."""
    if not job_id:
        jobs_queue.add_job_from_function('Syncing library with Plex', is_progress=True,
                                         wait_for_completion=wait_for_completion)
        return
    if not using_plex_library():
        jobs_queue.update_job_name(job_id=job_id, new_job_name='Skipped Plex sync (Arr library mode)')
        return
    plex = get_plex_server()
    server_id = settings.plex.get('server_machine_id') or _value(plex, 'machineIdentifier', 'plex')
    movie_ids = _as_list(settings.plex.get('movie_library_ids', [])) or \
        _as_list(settings.plex.get('movie_library', []))
    show_ids = _as_list(settings.plex.get('series_library_ids', [])) or \
        _as_list(settings.plex.get('series_library', []))
    if not movie_ids and not show_ids:
        logger.warning('BAZARR Plex library mode is selected but no Plex libraries are configured.')
        jobs_queue.update_job_name(job_id=job_id, new_job_name='Skipped Plex sync (no libraries selected)')
        return
    jobs_queue.update_job_progress(job_id=job_id, progress_max=len(movie_ids) + len(show_ids) or 1)
    try:
        _sync_movies(plex, movie_ids, server_id, set())
        _sync_shows(plex, show_ids, server_id, set())
    except Exception:
        logger.exception('BAZARR Plex library sync failed; incomplete sections were not reconciled')
        raise
    lock_library_source('plex')
    jobs_queue.update_job_progress(job_id=job_id, progress_value='max')
    jobs_queue.update_job_name(job_id=job_id, new_job_name='Synced library with Plex')


def sync_plex_item(rating_key, library_id=None, job_id=None):
    """Refresh one webhook item without treating the webhook as a deletion signal."""
    if not using_plex_library():
        return
    plex = get_plex_server()
    item = _hydrate(plex.fetchItem(int(rating_key)))
    server_id = settings.plex.get('server_machine_id') or _value(plex, 'machineIdentifier', 'plex')
    library_id = library_id or _value(item, 'librarySectionID')
    if _value(item, 'type') == 'movie':
        for _, part in _parts(item):
            _upsert_movie(item, part, library_id, server_id)
        lock_library_source('plex')
        return
    if _value(item, 'type') == 'episode':
        show = item.show()
        first = next(_parts(item), None)
        if first:
            _, part = first
            show_id = _upsert_show(show, library_id, server_id, os.path.dirname(_value(part, 'file')))
            _upsert_episode(item, show, part, library_id, server_id, show_id)
            lock_library_source('plex')
        return
    if _value(item, 'type') == 'show':
        episodes = [_hydrate(episode) for episode in item.episodes()]
        first = next((part for episode in episodes for _, part in _parts(episode)), None)
        if not first:
            return
        show_id = _upsert_show(item, library_id, server_id, os.path.dirname(_value(first, 'file')))
        for episode in episodes:
            for _, part in _parts(episode):
                _upsert_episode(episode, item, part, library_id, server_id, show_id)
        lock_library_source('plex')
