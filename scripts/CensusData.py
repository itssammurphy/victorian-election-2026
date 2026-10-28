
from io import BytesIO
from pathlib import Path
import re
from zipfile import ZipFile

import pandas as pd
import requests


CENSUS_URL = "https://www.abs.gov.au/census/find-census-data/datapacks/download/2021_GCP_SAL_for_VIC_short-header.zip"

GEOGRAPHY_URL = "https://geo.abs.gov.au/arcgis/rest/services/ASGS2021/SAL/MapServer/1/query"

ESSENDON_SUBURBS = [
    "Essendon",
    "Essendon North",
    "Essendon West",
    "Moonee Ponds",
    "Strathmore",
    "Aberfeldie",
    "Ascot Vale",
    "Flemington",
]


def getCensusArchive(directory="data/raw"):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)

    destination = directory / "2021_GCP_SAL_VIC.zip"

    if not destination.exists():
        response = requests.get(CENSUS_URL, timeout=180)
        response.raise_for_status()

        archive = BytesIO(response.content)

        if not ZipFile(archive).testzip() is None:
            raise ValueError("Downloaded Census archive is corrupt")

        destination.write_bytes(response.content)

    return destination


def normalizeSAL(value):
    value = str(value).strip()

    if value.startswith("SAL"):
        value = value[3:]

    if value.endswith(".0"):
        value = value[:-2]

    return value.zfill(5) if value.isdigit() else value


def normalizeSuburb(value):
    value = str(value).strip()
    value = re.sub(r"\s*\(Vic\.\)\s*$", "", value)
    return value


def getGeographyLookup():
    records = []
    offset = 0
    pageSize = 1000

    while True:
        parameters = {
            "where": "state_code_2021 = '2'",
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": pageSize,
            "orderByFields": "SAL_CODE_2021",
        }

        response = requests.get(
            GEOGRAPHY_URL,
            params=parameters,
            timeout=90,
        )
        response.raise_for_status()

        data = response.json()

        if "error" in data:
            raise RuntimeError(data["error"])

        if "features" not in data:
            raise ValueError("ABS geography response has no features field")

        features = data["features"]

        if not features:
            if data.get("exceededTransferLimit"):
                raise ValueError("ABS geography pagination returned an empty page")
            break

        records.extend(
            feature["attributes"] for feature in features
        )

        offset += len(features)

        if not data.get("exceededTransferLimit", len(features) == pageSize):
            break

    if not records:
        raise ValueError("ABS geography query returned no records")

    lookup = pd.DataFrame(records)

    codeColumn = next(
        (
            c for c in lookup.columns
            if c.upper() in {"SAL_CODE_2021", "SAL_CODE"}
        ),
        None,
    )

    nameColumn = next(
        (
            c for c in lookup.columns
            if c.upper() in {"SAL_NAME_2021", "SAL_NAME"}
        ),
        None,
    )

    if codeColumn is None or nameColumn is None:
        raise ValueError(
            "Could not identify SAL code and name fields. "
            f"Available columns: {lookup.columns.tolist()}"
        )

    result = lookup[[codeColumn, nameColumn]].copy()

    result.columns = ["SAL_CODE_2021", "suburb"]

    result["SAL_CODE_2021"] = (
        result["SAL_CODE_2021"].map(normalizeSAL)
    )

    result["suburb"] = result["suburb"].map(normalizeSuburb)

    if result["SAL_CODE_2021"].duplicated().any():
        raise ValueError("Duplicate SAL identifiers in ABS geography response")

    result = (
        result.sort_values("SAL_CODE_2021")
        .reset_index(drop=True)
    )

    return result


def readCensusTable(archive, tableCode):
    pattern = re.compile(
        rf"2021Census_{re.escape(tableCode)}_VIC_SAL\.csv$",
        re.IGNORECASE,
    )

    matches = [
        name for name in archive.namelist()
        if pattern.search(Path(name).name)
    ]

    if len(matches) != 1:
        raise ValueError(
            f"Expected one Census table {tableCode}, "
            f"found {len(matches)}"
        )

    with archive.open(matches[0]) as file:
        frame = pd.read_csv(file, low_memory=False)

    codeColumn = next(
        (
            c for c in frame.columns
            if "SAL_CODE" in c.upper()
            or c.upper() in {"SAL_CODE_2021", "SAL_CODE"}
        ),
        None,
    )

    if codeColumn is None:
        raise ValueError(
            f"No SAL code column found in table {tableCode}"
        )

    frame = frame.rename(
        columns={codeColumn: "SAL_CODE_2021"}
    )

    frame["SAL_CODE_2021"] = (
        frame["SAL_CODE_2021"].map(normalizeSAL)
    )

    return frame


def getCensusTables(tableCodes=("G01",)):
    archivePath = getCensusArchive()

    with ZipFile(archivePath) as archive:
        frames = []

        for code in tableCodes:
            frame = readCensusTable(archive, code)

            duplicateCodes = frame[
                "SAL_CODE_2021"
            ].duplicated()

            if duplicateCodes.any():
                raise ValueError(
                    f"Duplicate SAL identifiers in {code}"
                )

            frames.append((code, frame))

    if not frames:
        raise ValueError("No Census tables requested")

    result = frames[0][1]

    for code, frame in frames[1:]:
        result = result.merge(
            frame,
            on="SAL_CODE_2021",
            how="outer",
            validate="one_to_one",
            suffixes=("", f"_{code}"),
        )

    return result


def getSuburbDemographics(
    suburbs=None,
    tableCodes=("G01",),
):
    geography = getGeographyLookup()
    census = getCensusTables(tableCodes)

    demographics = geography.merge(
        census,
        on="SAL_CODE_2021",
        how="outer",
        validate="one_to_one",
        indicator=True,
    )

    missing = demographics[
        demographics["_merge"] != "both"
    ]

    if not missing.empty:
        geographyOnly = missing.loc[
            missing["_merge"] == "left_only", "SAL_CODE_2021"
        ].tolist()
        censusOnly = missing.loc[
            missing["_merge"] == "right_only", "SAL_CODE_2021"
        ].tolist()
        raise ValueError(
            f"Victoria SAL coverage mismatch: {len(geographyOnly)} geography "
            f"records without Census data (sample: {geographyOnly[:5]}); "
            f"{len(censusOnly)} Census records without geography "
            f"(sample: {censusOnly[:5]})"
        )

    demographics = demographics.drop(columns="_merge")

    if suburbs is not None:
        target = {s.casefold() for s in suburbs}

        demographics = demographics[
            demographics["suburb"].str.casefold().isin(target)
        ].copy()

        found = set(
            demographics["suburb"].str.casefold()
        )

        absent = target - found

        if absent:
            raise ValueError(
                f"Suburbs not found: {sorted(absent)}"
            )

    return demographics.reset_index(drop=True)


def saveSuburbDemographics(
    directory="data/processed",
    tableCodes=("G01",),
):
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)

    demographics = getSuburbDemographics(
        tableCodes=tableCodes
    )

    local = demographics[
        demographics["suburb"].isin(ESSENDON_SUBURBS)
    ].copy()

    missing = set(ESSENDON_SUBURBS) - set(
        local["suburb"]
    )

    if missing:
        raise ValueError(
            f"Missing expected Essendon-area suburbs: {missing}"
        )

    demographics.to_csv(
        output / "vic_2021_suburb_demographics.csv",
        index=False,
    )

    local.to_csv(
        output / "essendon_2021_suburb_demographics.csv",
        index=False,
    )

    return {
        "all_suburbs": len(demographics),
        "essendon_area_suburbs": len(local),
        "variables": len(demographics.columns) - 2,
    }
