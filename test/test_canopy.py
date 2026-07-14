from shaduw_langs_de_snelweg.canopy import (
    META_ZOOM,
    eth_tile_label,
    eth_tile_labels_for_bounds,
    latlon_to_tilexy,
    meta_quadkeys_for_bounds,
    tile_urls,
    tilexy_to_quadkey,
)
from shaduw_langs_de_snelweg.config import CanopyConfig


def test_eth_tile_label_nl():
    assert eth_tile_label(52.09, 4.95) == "N51E003"
    assert eth_tile_label(51.83, 4.61) == "N51E003"


def test_eth_tile_label_edges():
    assert eth_tile_label(0.5, 0.5) == "N00E000"
    assert eth_tile_label(-0.5, -0.5) == "S03W003"
    assert eth_tile_label(48.1, 11.5) == "N48E009"  # Munich


def test_eth_bounds_spanning_two_tiles():
    labels = eth_tile_labels_for_bounds((5.9, 51.9, 6.1, 52.1))
    assert labels == {"N51E003", "N51E006"}


def test_quadkey_bing_docs_example():
    # Bing tile system documentation: tile (3, 5) at zoom 3 -> "213"
    assert tilexy_to_quadkey(3, 5, 3) == "213"


def test_latlon_to_tilexy_nl():
    x, y = latlon_to_tilexy(52.16, 5.54, META_ZOOM)
    assert (x, y) == (263, 168)
    assert tilexy_to_quadkey(x, y, META_ZOOM) == "120202111"


def test_meta_quadkeys_for_small_aoi():
    quadkeys = meta_quadkeys_for_bounds((5.54, 52.16, 5.55, 52.17))
    assert quadkeys == {"120202111"}
    assert all(len(q) == META_ZOOM for q in quadkeys)


def test_tile_urls_meta_default():
    urls = tile_urls(CanopyConfig(), (5.54, 52.16, 5.55, 52.17))
    assert urls == [
        "https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/"
        "alsgedi_global_v6_float/chm/120202111.tif"
    ]


def test_tile_urls_respects_override_template():
    cfg = CanopyConfig(source="eth", url_template="file:///tiles/{tile}.tif")
    urls = tile_urls(cfg, (4.6, 51.8, 4.7, 51.9))
    assert urls == ["file:///tiles/N51E003.tif"]


def test_tile_urls_local(tmp_path):
    chm = tmp_path / "chm.tif"
    chm.touch()
    cfg = CanopyConfig(source="local", local_path=chm)
    assert tile_urls(cfg, (0, 0, 1, 1)) == [str(chm)]
