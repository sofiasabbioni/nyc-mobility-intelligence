import io
import urllib.request
from pathlib import Path

import yaml

import download_data

ROOT = Path(__file__).resolve().parents[1]


def test_nightlife_compose_configuration_and_pandas_pin():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    services = compose["services"]
    loader = services["loader"]
    app = services["app"]

    assert loader["environment"]["WITH_ZONE_SHAPES"] == "1"
    assert loader["environment"]["POSTGRES_HOST"] == "db"
    assert "./data/raw:/data/raw" in loader["volumes"]
    assert loader["environment"]["DATA_DIR"] == "/data"
    assert "./data/raw:/data/raw:ro" in app["volumes"]
    assert app["environment"]["DATA_DIR"] == "/data/raw"
    requirements = (ROOT / "loader" / "requirements.txt").read_text().splitlines()
    assert "pandas==3.1.1" in requirements


def test_enabled_downloader_requests_only_synthetic_zone_shapes(
    raw_data, monkeypatch
):
    shapes = raw_data / "raw" / "taxi_zones.zip"
    assert not shapes.exists()
    monkeypatch.setenv("WITH_ZONE_SHAPES", "1")
    requested = []
    synthetic_archive = b"synthetic taxi-zone archive"

    def fake_urlopen(request, timeout):
        requested.append((request.full_url, timeout))
        return io.BytesIO(synthetic_archive)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    assert download_data.main() == 0
    assert requested == [
        (f"{download_data.BASE_URL}/misc/taxi_zones.zip", 120)
    ]
    assert shapes.read_bytes() == synthetic_archive
