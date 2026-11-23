"""Parse a model file once per content. One ingest request parsed the same YAML ten times (0.75 s each, 22 Nov 2026).

`load_yaml` returns a private copy, so a caller may change what it gets."""
import copy
from functools import lru_cache

import yaml


@lru_cache(maxsize=8)
def _parse(raw: bytes):
    return yaml.safe_load(raw)


def load_yaml(raw):
    return copy.deepcopy(_parse(raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)))
