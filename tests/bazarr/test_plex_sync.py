from types import SimpleNamespace


def test_plex_guid_and_label_normalisation():
    from plex.sync import _guids, _labels

    item = SimpleNamespace(
        guids=[
            SimpleNamespace(id='imdb://tt1234567'),
            SimpleNamespace(id='tmdb://42'),
            SimpleNamespace(id='tvdb://99'),
            SimpleNamespace(id='plex://internal-id'),
        ],
        labels=[SimpleNamespace(tag='french'), SimpleNamespace(tag='no-auto-subs')],
    )

    assert _guids(item) == {'imdb': 'tt1234567', 'tmdb': '42', 'tvdb': '99'}
    assert _labels(item) == ['french', 'no-auto-subs']


def test_plex_guid_normalisation_supports_legacy_agent_attributes():
    from plex.sync import _guids

    # Plex's legacy TVDB agent places the show ID in grandparentGuid rather
    # than returning a nested <Guid id="tvdb://..."> element.
    episode = SimpleNamespace(
        guids=[],
        guid='com.plexapp.agents.thetvdb://76894/1/223?lang=fr',
        parentGuid='com.plexapp.agents.thetvdb://76894/1?lang=fr',
        grandparentGuid='com.plexapp.agents.thetvdb://76894?lang=fr',
    )

    assert _guids(episode) == {'tvdb': '76894'}


def test_database_refiner_allows_a_series_without_a_tvdb_id(monkeypatch):
    from subliminal import Episode
    from subtitles.refiners import database as database_refiner

    row = SimpleNamespace(
        seriesTitle='Yu-Gi-Oh!', season=1, episode=1, absoluteEpisode=None,
        episodeTitle='The Heart of the Cards', year='2000', tvdbId=None,
        alternativeTitles='[]', format=None, resolution=None, video_codec=None,
        audio_codec=None, imdbId=None, sonarrSeriesId=1, sonarrEpisodeId=2,
    )
    monkeypatch.setattr(database_refiner.database, 'execute',
                        lambda statement: SimpleNamespace(first=lambda: row))
    monkeypatch.setattr(database_refiner.path_mappings, 'path_replace_reverse', lambda path: path)
    video = Episode('episode.mkv', series='Guessed series', season=1, episode=1, series_tvdb_id=123)

    database_refiner.refine_from_db('episode.mkv', video)

    assert video.series_tvdb_id == 123


def test_plex_parts_keeps_each_valid_physical_part():
    from plex import sync

    movie = SimpleNamespace(
        type='movie',
        media=[SimpleNamespace(parts=[
            SimpleNamespace(id=101, file='/movies/one.mkv', size=1024, container='mkv'),
            SimpleNamespace(id=102, file='/movies/two.mkv', size=2048, container='mkv'),
        ])],
    )
    assert [part.id for _, part in sync._parts(movie)] == [101, 102]


def test_plex_audio_stream_metadata_is_normalized():
    from plex import sync

    part = SimpleNamespace(id=101, file='/movies/one.mkv', size=1024)
    movie = SimpleNamespace(
        media=[SimpleNamespace(
            parts=[part],
            audioStreams=[SimpleNamespace(language='Japanese')],
        )],
    )

    assert sync._plex_audio_languages(movie, part) == ['Japanese']


def test_plex_reconciliation_chunks_sqlite_bind_values():
    from plex.sync import _chunks

    batches = list(_chunks(range(1_001), size=500))

    assert [len(batch) for batch in batches] == [500, 500, 1]
