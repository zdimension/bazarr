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
