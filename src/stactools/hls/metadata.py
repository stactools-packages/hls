import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

import rasterio
from dateutil.parser import parse
from pystac.utils import datetime_to_str
from shapely.geometry import box, mapping
from stactools.core.io import ReadHrefModifier
from stactools.core.projection import epsg_from_utm_zone_number, reproject_geom

from stactools.hls import constants, utils


class IncorrectAssetHref(Exception):
    """An HREF to a EO data band (not an Fmask, azimuth, or zenith band) COG is
    required.
    """


class MissingUtmZone(Exception):
    """ "Unable to parse the UTM zone from CRS WKT string."""


@dataclass
class Metadata:
    """Structure to hold metadata about an HLS granule."""

    id: str
    product: str
    version: str
    acquisition_datetime: datetime
    start_end_datetime: Optional[Dict[str, str]]
    platform: str
    instrument: List[str]
    processing_datetime: str
    cloud_cover: Optional[int]
    sun_azimuth: float
    azimuth: float
    shape: List[int]
    transform: List[float]
    epsg: int
    mgrs: Dict[str, Any]
    geometry: Dict[str, Any]

    @classmethod
    def from_cog(
        cls,
        cog_href: str,
        read_href_modifier: Optional[ReadHrefModifier] = None,
    ) -> "Metadata":
        """Extracts granule metadata from a COG file.

        Args:
            cog_href (str): HREF to the COG file
            read_href_modifier (ReadHrefModifier, optional): An
                optional function to modify the href (e.g. to add a token to a
                url)

        Returns:
            Metadata: A Metadata dataclass
        """
        read_cog_href = utils.modify_href(cog_href, read_href_modifier)
        with rasterio.open(read_cog_href) as dataset:
            transform = dataset.transform[0:6]
            shape = dataset.shape
            tags = dataset.tags()
            bbox = list(dataset.bounds)
            wkt = dataset.crs.wkt

        pattern = re.compile(r"UTM Zone (\d+)", re.I)
        search = pattern.search(wkt)
        if search:
            utm_zone = int(search.group(1))
            epsg = epsg_from_utm_zone_number(utm_zone, south=False)
        else:
            raise MissingUtmZone(
                f"Unable to parse UTM zone number from WKT string: {wkt}"
            )

        cloud_cover = int(tags["cloud_coverage"])
        sun_azimuth = round(float(tags["MEAN_SUN_AZIMUTH_ANGLE"]), 1)
        azimuth = round(float(tags["MEAN_VIEW_AZIMUTH_ANGLE"]), 1)
        processing_datetime = parse(tags["HLS_PROCESSING_TIME"])
        sensing_time = [parse(dt) for dt in tags["SENSING_TIME"].split(";")]

        acquisition_datetime = min(sensing_time)
        start_end_datetime = None
        if len(sensing_time) > 1:
            start_end_datetime = {
                "start_datetime": datetime_to_str(min(sensing_time)),
                "end_datetime": datetime_to_str(max(sensing_time)),
            }

        id = utils.id_from_href(cog_href)
        version = utils.version_from_href(cog_href)
        product = utils.product_from_href(cog_href)
        tile_id = utils.tile_id_from_href(cog_href)

        # Handles multiple platforms in a single granule; unknown if that actually occurs
        platforms = set()
        if product == "S30":
            for datastrip_id in tags["DATASTRIP_ID"].split(";"):
                platforms.add(f"sentinel-2{datastrip_id.strip().lower()[2]}")
        else:
            for product_id in tags["LANDSAT_PRODUCT_ID"].split(";"):
                platforms.add(f"landsat-{product_id.strip()[3]}")
        platform = ", ".join(platforms)

        mgrs = {
            "mgrs:utm_zone": int(tile_id[1:3]),
            "mgrs:latitude_band": tile_id[3],
            "mgrs:grid_square": tile_id[4:],
        }

        geometry = reproject_geom(f"EPSG:{epsg}", "EPSG:4326", mapping(box(*bbox)))

        return Metadata(
            id=id,
            product=product,
            version=version,
            acquisition_datetime=acquisition_datetime,
            start_end_datetime=start_end_datetime,
            platform=platform,
            instrument=constants.INSTRUMENT[product],
            processing_datetime=processing_datetime,
            cloud_cover=cloud_cover,
            sun_azimuth=sun_azimuth,
            azimuth=azimuth,
            shape=list(shape),
            transform=list(transform),
            epsg=epsg,
            mgrs=mgrs,
            geometry=geometry,
        )


def hls_metadata(
    cog_href: str,
    read_href_modifier: Optional[ReadHrefModifier] = None,
) -> Metadata:
    """Checks COG HREF validity and returns metadata derived from the COG file.

    Args:
        cog_href (str): HREF to a single COG asset of an HLS granule. The COG
            must contain EO data.
        read_href_modifier (ReadHrefModifier, optional): An optional
                function to modify the href (e.g. to add a token to a url).
                Defaults to None.

    Returns:
        Metadata: a dataclass containing metadata generated from the COG HREF.
    """
    product = utils.product_from_href(cog_href)
    band_name = utils.band_name_from_href(cog_href)
    if band_name not in constants.BANDS[product]:
        raise IncorrectAssetHref(
            f"A STAC Item can not be created from an Fmask, SAA, SZA, VAA, or "
            f"VZA COG HREF. A '{band_name}' COG HREF was supplied."
        )

    return Metadata.from_cog(cog_href, read_href_modifier)
