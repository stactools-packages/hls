import re
from datetime import datetime
from typing import Any, Dict, Optional

import fsspec
import rasterio
import untangle
from dateutil.parser import parse
from pystac.utils import datetime_to_str
from shapely.geometry import MultiPolygon, Polygon
from shapely.geometry.polygon import orient
from stactools.core.io import ReadHrefModifier
from stactools.core.projection import epsg_from_utm_zone_number

from stactools.hls import constants, utils


class IncorrectAssetHref(Exception):
    """An HREF to a EO data band (not an Fmask, azimuth, or zenith band) COG is required."""


class MissingUtmZone(Exception):
    """Unable to parse the UTM zone from CRS WKT string."""


class InvalidGeometry(Exception):
    """Unable to parse polygon geometry from XML file."""


class Metadata:
    """Structure to hold metadata about an HLS granule."""

    def __init__(
        self, cog_href: str, read_href_modifier: Optional[ReadHrefModifier] = None
    ) -> None:
        """Extracts granule metadata from COG and XML files.

        Args:
            cog_href (str): HREF to the COG file
            read_href_modifier (ReadHrefModifier, optional): An
                optional function to modify the href (e.g. to add a token to a
                url)
        """
        self.cog_href = cog_href
        self.read_href_modifier = read_href_modifier

        read_cog_href = utils.modify_href(cog_href, read_href_modifier)
        with rasterio.open(read_cog_href) as dataset:
            self.transform = list(dataset.transform[0:6])
            self.shape = list(dataset.shape)
            self.tags = dataset.tags()
            self.wkt = dataset.crs.wkt

        self.sensing_time = [parse(dt) for dt in self.tags["SENSING_TIME"].split(";")]

    @property
    def epsg(self) -> int:
        pattern = re.compile(r"UTM Zone (\d+)", re.I)
        search = pattern.search(self.wkt)
        if search:
            utm_zone = int(search.group(1))
            return epsg_from_utm_zone_number(utm_zone, south=False)
        else:
            raise MissingUtmZone(
                f"Unable to parse UTM zone number from WKT string: {self.wkt}"
            )

    @property
    def cloud_cover(self) -> int:
        return int(self.tags["cloud_coverage"])

    @property
    def sun_azimuth(self) -> float:
        return round(float(self.tags["MEAN_SUN_AZIMUTH_ANGLE"]), 1)

    @property
    def azimuth(self) -> float:
        return round(float(self.tags["MEAN_VIEW_AZIMUTH_ANGLE"]), 1)

    @property
    def processing_datetime(self) -> datetime:
        return parse(self.tags["HLS_PROCESSING_TIME"])

    @property
    def acquisition_datetime(self) -> datetime:
        return min(self.sensing_time)

    @property
    def start_end_datetime(self) -> Dict[str, str]:
        se_datetime = None
        if len(self.sensing_time) > 1:
            se_datetime = {
                "start_datetime": datetime_to_str(min(self.sensing_time)),
                "end_datetime": datetime_to_str(max(self.sensing_time)),
            }
        return se_datetime

    @property
    def platform(self) -> str:
        # Handles multiple platforms in a single granule; unknown if that actually occurs
        platforms = set()
        product = utils.product_from_href(self.cog_href)
        if product == "S30":
            for datastrip_id in self.tags["DATASTRIP_ID"].split(";"):
                platforms.add(f"sentinel-2{datastrip_id.strip().lower()[2]}")
        else:
            for product_id in self.tags["LANDSAT_PRODUCT_ID"].split(";"):
                platforms.add(f"landsat-{product_id.strip()[3]}")
        platform = ", ".join(platforms)
        return platform

    @property
    def mgrs(self) -> Dict[str, Any]:
        tile_id = utils.tile_id_from_href(self.cog_href)
        mgrs = {
            "mgrs:utm_zone": int(tile_id[1:3]),
            "mgrs:latitude_band": tile_id[3],
            "mgrs:grid_square": tile_id[4:],
        }
        return mgrs

    @property
    def geometry(self):
        parts = self.cog_href.split(".")[:-2]
        self.xml_href = f"{'.'.join(parts)}.cmr.xml"
        read_xml_href = utils.modify_href(self.xml_href, self.read_href_modifier)

        with fsspec.open(read_xml_href) as file:
            cmr = untangle.parse(file)
            granule = cmr.Granule

        xml_geometry = self._get_geometry(granule)

        return xml_geometry

    def _get_geometry(self, granule):
        polygons = []
        for poly in granule.Spatial.HorizontalSpatialDomain.Geometry.GPolygon:
            ring = []
            for point in poly.Boundary.Point:
                geojson_point = (
                    float(point.PointLongitude.cdata),
                    float(point.PointLatitude.cdata),
                )
                ring.append(geojson_point)
            polygons.append(tuple(ring))

        if len(polygons) == 1:
            geometry = Polygon(polygons[0])
        elif len(polygons) > 1:
            geometry = MultiPolygon(polygons)
        else:
            raise InvalidGeometry(
                f"Unable to parse geometry from XML file: {self.xml_href}"
            )

        return orient(geometry)


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

    return Metadata(cog_href, read_href_modifier)
