import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import geopandas as gpd
from shapely.geometry import Polygon, mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import geography


class GeographyTests(unittest.TestCase):
    def setUp(self):
        self.polygon = Polygon([(144, -38), (145, -38), (145, -37), (144, -38)])
        self.feature = {
            "type": "Feature", "properties": {"sal_code_2021": "20001"},
            "geometry": mapping(self.polygon),
        }

    def test_essendon_query_uses_abs_name_and_state(self):
        boundary = gpd.GeoDataFrame(geometry=[self.polygon], crs="EPSG:4326")
        with tempfile.TemporaryDirectory() as directory, \
                patch.object(geography, "GEOGRAPHIC_DIR", Path(directory)), \
                patch.object(geography, "getLayer", return_value="service/0"), \
                patch.object(geography, "getNameField", side_effect=[
                    "sed_name_2022", "state_code_2021"]), \
                patch.object(geography, "queryFeatures", return_value=boundary) as query:
            geography.getEssendonBoundary()
        self.assertEqual(query.call_args.args[1]["where"],
                         "state_code_2021 = '2' AND "
                         "sed_name_2022 = 'Essendon (Northern Metropolitan)'")

    def test_geojson_preserves_polygon_and_requests_wgs84(self):
        response = Mock(json=Mock(return_value={
            "type": "FeatureCollection", "features": [self.feature],
        }))
        with patch.object(geography.requests, "get", return_value=response) as get:
            result = geography.queryFeatures("service/0", {"where": "1=1"})
        self.assertTrue(result.geometry.iloc[0].equals_exact(self.polygon, 0))
        self.assertEqual(result.crs.to_epsg(), 4326)
        self.assertEqual(get.call_args.kwargs["params"]["outSR"], 4326)
        self.assertEqual(get.call_args.kwargs["params"]["f"], "geojson")

    def test_rejects_truncated_and_missing_geometry_responses(self):
        for data, message in (
            ({"features": [self.feature], "exceededTransferLimit": True}, "limit exceeded"),
            ({"features": [self.feature], "properties": {
                "exceededTransferLimit": True}}, "limit exceeded"),
            ({"features": []}, "no features"),
            ({"features": [{**self.feature, "geometry": None}]}, "Missing geometries"),
        ):
            with self.subTest(message=message), patch.object(
                geography.requests, "get", return_value=Mock(json=Mock(return_value=data))
            ):
                with self.assertRaisesRegex(ValueError, message):
                    geography.queryFeatures("service/0", {"where": "1=1"})

    def test_sal_codes_match_census_including_legacy_cache_padding(self):
        for value in ("SAL20001", "20001", "0020001", 20001.0):
            self.assertEqual(geography.normaliseSAL(value), "20001")
        with self.assertRaisesRegex(ValueError, "Invalid SAL"):
            geography.normaliseSAL(None)


if __name__ == "__main__":
    unittest.main()
