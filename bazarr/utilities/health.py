# coding=utf-8

import json

from sqlalchemy import func

from app.config import settings
from app.database import (TableShowsRootfolder, TableMoviesRootfolder, TableLanguagesProfiles, database, select,
                          TableShows, TableMovies)
from app.event_handler import event_stream
from app.jobs_queue import jobs_queue
from utilities.path_mappings import path_mappings
from sonarr.rootfolder import check_sonarr_rootfolder
from radarr.rootfolder import check_radarr_rootfolder
from utilities.library_source import using_plex_library


_plex_health_issue = None


def check_plex_library_health():
    """Validate the configured Plex server/sections without doing a full sync."""
    global _plex_health_issue
    try:
        from plex.operations import get_plex_server
        plex = get_plex_server()
        def as_list(value):
            return value if isinstance(value, list) else [value] if value else []
        section_ids = as_list(settings.plex.get('movie_library_ids', [])) + \
            as_list(settings.plex.get('series_library_ids', []))
        if not section_ids:
            section_ids = as_list(settings.plex.get('movie_library', [])) + \
                as_list(settings.plex.get('series_library', []))
        if not section_ids:
            _plex_health_issue = 'No Plex movie or series library is selected.'
            return
        for section_id in section_ids:
            try:
                plex.library.sectionByID(int(section_id))
            except (TypeError, ValueError):
                plex.library.section(section_id)
    except Exception as error:
        _plex_health_issue = f'Cannot access configured Plex libraries: {error}'
    else:
        _plex_health_issue = None


def check_health(job_id=None, wait_for_completion=False):
    if not job_id:
        jobs_queue.add_job_from_function("Checking Health", is_progress=False, wait_for_completion=wait_for_completion)
        return

    if not using_plex_library() and settings.general.use_sonarr:
        check_sonarr_rootfolder()
    if not using_plex_library() and settings.general.use_radarr:
        check_radarr_rootfolder()
    if using_plex_library():
        check_plex_library_health()
    event_stream(type='badges')

    from .backup import backup_rotation
    backup_rotation()

    jobs_queue.update_job_name(job_id=job_id, new_job_name="Checked Health")


def get_health_issues():
    # this function must return a list of dictionaries consisting of to keys: object and issue
    health_issues = []

    if using_plex_library() and _plex_health_issue:
        health_issues.append({'object': 'Plex library', 'issue': _plex_health_issue})

    # get Sonarr rootfolder issues
    if not using_plex_library() and settings.general.use_sonarr:
        rootfolder = database.execute(
            select(TableShowsRootfolder.path,
                   TableShowsRootfolder.accessible,
                   TableShowsRootfolder.error)
            .where(TableShowsRootfolder.accessible == 0)) \
            .all()
        for item in rootfolder:
            health_issues.append({'object': path_mappings.path_replace(item.path),
                                  'issue': item.error})

    # get Radarr rootfolder issues
    if not using_plex_library() and settings.general.use_radarr:
        rootfolder = database.execute(
            select(TableMoviesRootfolder.path,
                   TableMoviesRootfolder.accessible,
                   TableMoviesRootfolder.error)
            .where(TableMoviesRootfolder.accessible == 0)) \
            .all()
        for item in rootfolder:
            health_issues.append({'object': path_mappings.path_replace_movie(item.path),
                                  'issue': item.error})

    # get languages profiles duplicate ids issues when there's a cutoff set
    languages_profiles = database.execute(
        select(TableLanguagesProfiles.items, TableLanguagesProfiles.name, TableLanguagesProfiles.cutoff)).all()
    for languages_profile in languages_profiles:
        if not languages_profile.cutoff:
            # ignore profiles that don't have a cutoff set
            continue
        languages_profile_ids = []
        for items in json.loads(languages_profile.items):
            if items['id'] in languages_profile_ids:
                health_issues.append({'object': languages_profile.name,
                                      'issue': 'This languages profile has duplicate IDs. You need to edit this profile'
                                               ' and make sure to select the proper cutoff if required.'})
                break
            else:
                languages_profile_ids.append(items['id'])

    # check if there's at least one languages profile created
    languages_profiles_count = database.execute(select(func.count(TableLanguagesProfiles.profileId))).scalar()
    series_with_profile = database.execute(select(func.count(TableShows.sonarrSeriesId))
                                           .where(TableShows.profileId.is_not(None))).scalar()
    movies_with_profile = database.execute(select(func.count(TableMovies.radarrId))
                                           .where(TableMovies.profileId.is_not(None))).scalar()
    default_series_profile_empty = settings.general.serie_default_enabled and settings.general.serie_default_profile == ''
    default_movies_profile_empty = settings.general.movie_default_enabled and settings.general.movie_default_profile == ''
    plex_series_enabled = using_plex_library() and bool(settings.plex.get('series_library_ids', []))
    plex_movies_enabled = using_plex_library() and bool(settings.plex.get('movie_library_ids', []))
    if languages_profiles_count == 0:
        health_issues.append({'object': 'Missing languages profile',
                              'issue': 'You must create at least one languages profile and assign it to your content.'})
    elif languages_profiles_count > 0 and (((plex_series_enabled or settings.general.use_sonarr) and
                                             series_with_profile == 0 and default_series_profile_empty) or
                                            ((plex_movies_enabled or settings.general.use_radarr) and
                                             movies_with_profile == 0 and default_movies_profile_empty)):
        health_issues.append({'object': 'No assigned languages profile',
                              'issue': 'Although you have created at least one languages profile, you must assign it '
                                       'to your content.'})

    return health_issues
