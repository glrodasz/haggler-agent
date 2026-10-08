"""Adapter id -> implementation (imported lazily so one broken adapter cannot break the rest)."""

from __future__ import annotations

import importlib
from functools import cache

from haggler.sources.base import Adapter

ADAPTERS: dict[str, str] = {
    "netflix_help": "haggler.sources.netflix_help:NetflixHelpAdapter",
    "tompec_netflix": "haggler.sources.tompec:TompecNetflixAdapter",
    "appstore_iap": "haggler.sources.appstore_iap:AppStoreIapAdapter",
    "disney_web": "haggler.sources.disney:DisneyWebAdapter",
    "spotify_premium": "haggler.sources.spotify:SpotifyPremiumAdapter",
    "icloud_support": "haggler.sources.icloud_support:ICloudSupportAdapter",
    "apple_marketing": "haggler.sources.apple_marketing:AppleMarketingAdapter",
    "ms_displaycatalog": "haggler.sources.ms_displaycatalog:MsDisplayCatalogAdapter",
}


@cache
def get_adapter(adapter_id: str) -> Adapter:
    target = ADAPTERS[adapter_id]
    module_name, cls_name = target.split(":")
    cls = getattr(importlib.import_module(module_name), cls_name)
    return cls()
