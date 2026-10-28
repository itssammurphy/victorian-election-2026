import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import CensusData


class CensusDataTests(unittest.TestCase):
    def test_sal_formats_match_without_changing_official_code(self):
        for value in ("SAL20001", "20001", 20001, 20001.0):
            with self.subTest(value=value):
                self.assertEqual(CensusData.normalizeSAL(value), "20001")

    def test_victoria_query_follows_transfer_flag_on_short_pages(self):
        responses = []
        for code, name, more in (
            ("20001", "Abbeyard", True),
            ("20002", "Abbotsford (Vic.)", False),
        ):
            responses.append(Mock(json=Mock(return_value={
                "features": [{"attributes": {
                    "sal_code_2021": code, "sal_name_2021": name,
                }}],
                "exceededTransferLimit": more,
            })))
        with patch.object(CensusData.requests, "get", side_effect=responses) as get:
            result = CensusData.getGeographyLookup()
        self.assertEqual(result["SAL_CODE_2021"].tolist(), ["20001", "20002"])
        self.assertEqual(result["suburb"].tolist(), ["Abbeyard", "Abbotsford"])
        self.assertEqual(get.call_count, 2)
        for call in get.call_args_list:
            self.assertEqual(call.kwargs["params"]["where"], "state_code_2021 = '2'")
        self.assertEqual(get.call_args_list[1].kwargs["params"]["resultOffset"], 1)

    def test_join_preserves_values_and_rejects_missing_records_on_either_side(self):
        geography = pd.DataFrame({
            "SAL_CODE_2021": ["20001", "20002"],
            "suburb": ["Abbeyard", "Abbotsford"],
        })
        census = pd.DataFrame({
            "SAL_CODE_2021": ["20002", "20001"],
            "Tot_P_P": [9088, 4],
        })
        with patch.object(CensusData, "getGeographyLookup", return_value=geography), \
                patch.object(CensusData, "getCensusTables", return_value=census):
            result = CensusData.getSuburbDemographics()
        self.assertEqual(dict(zip(result.suburb, result.Tot_P_P)),
                         {"Abbeyard": 4, "Abbotsford": 9088})

        for geo, data, message in (
            (geography, census.iloc[:1], "1 geography records without Census data"),
            (geography.iloc[:1], census, "1 Census records without geography"),
        ):
            with self.subTest(message=message), \
                    patch.object(CensusData, "getGeographyLookup", return_value=geo), \
                    patch.object(CensusData, "getCensusTables", return_value=data):
                with self.assertRaisesRegex(ValueError, message):
                    CensusData.getSuburbDemographics()


if __name__ == "__main__":
    unittest.main()
