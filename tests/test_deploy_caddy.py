"""The VM's Caddy is a pinned release without the 2.11.6 keep-alive regression."""

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "deploy" / "oracle" / "docker-compose.yaml"


def test_caddy_is_pinned_to_a_good_release():
    image = yaml.safe_load(COMPOSE.read_text())["services"]["caddy"]["image"]
    name, _, tag = image.partition(":")
    assert name == "caddy"
    version = tuple(int(part) for part in tag.removesuffix("-alpine").split("."))
    # A floating tag (2-alpine) would pick up whatever deploy.sh pulls next.
    assert len(version) == 3, image
    # 2.11.6 answers 499 on keep-alive connections after a 60 s proxied call.
    assert version != (2, 11, 6), image
