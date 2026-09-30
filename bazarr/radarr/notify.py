# coding=utf-8

import logging
import requests

from app.config import settings
from radarr.info import url_api_radarr
from constants import HEADERS


def notify_radarr(radarr_id):
    from utilities.library_source import using_plex_library
    if using_plex_library():
        return
    try:
        url = f"{url_api_radarr()}command?apikey={settings.radarr.apikey}"
        data = {
            'name': 'RescanMovie',
            'movieId': int(radarr_id)
        }
        requests.post(url, json=data, timeout=int(settings.radarr.http_timeout), verify=False, headers=HEADERS)
    except Exception:
        logging.exception('BAZARR cannot notify Radarr')
