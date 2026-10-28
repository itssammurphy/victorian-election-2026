
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

from CensusData import normalizeSuburb

SAL_SERVICE = "https://geo.abs.gov.au/arcgis/rest/services/ASGS2021/SAL/MapServer"

SED_SERVICE = "https://geo.abs.gov.au/arcgis/rest/services/ASGS2022/SED/MapServer"


AREA_CRS = "EPSG:7855"

GEOGRAPHIC_DIR = Path("data/geographic")


def getJSON(url, params=None):
    response = requests.get(url, params=params, timeout=120)
    response.raise_for_status()

    data = response.json()

    if "error" in data:
        raise RuntimeError(
            f"ArcGIS request failed: {data['error']}"
        )

    return data


def getLayer(service, layerName):
    metadata = getJSON(service, {"f": "json"})

    matches = [
        layer for layer in metadata["layers"]
        if layer["name"].upper() == layerName.upper()
    ]

    if len(matches) != 1:
        raise ValueError(
            f"Could not uniquely find {layerName} in {service}"
        )

    return f"{service}/{matches[0]['id']}"


def getNameField(layer, prefix):
    metadata = getJSON(layer, {"f": "json"})

    names = [
        field["name"]
        for field in metadata["fields"]
    ]

    for name in names:
        if name.upper().startswith(prefix.upper()):
            return name

    raise ValueError(
        f"No field beginning with {prefix}: {names}"
    )


def queryFeatures(layer, parameters):
    params = {
        "f": "geojson",
        "outFields": "*",
        "returnGeometry": "true",
        "outSR": 4326,
        **parameters,
    }

    response = requests.get(
        f"{layer}/query",
        params=params,
        timeout=180,
    )

    response.raise_for_status()
    data = response.json()

    if "error" in data:
        raise RuntimeError(data["error"])

    features = data.get("features", [])

    if not features:
        raise ValueError(
            f"Geographic query returned no features from {layer}: {parameters}"
        )

    if data.get("exceededTransferLimit") or data.get("properties", {}).get(
        "exceededTransferLimit"
    ):
        raise ValueError(
            "ArcGIS result limit exceeded; query must be paginated"
        )

    result = gpd.GeoDataFrame.from_features(
        features,
        crs="EPSG:4326",
    )

    if result.geometry.isna().any():
        raise ValueError("Missing geometries in ArcGIS response")

    if result.geometry.is_empty.any() or not result.geometry.is_valid.all():
        raise ValueError("Empty or invalid geometries in ArcGIS response")

    if not result.geom_type.isin(["Polygon", "MultiPolygon"]).all():
        raise ValueError("Expected polygon boundaries in ArcGIS response")

    return result


def normaliseSAL(value):
    code = str(value).strip()

    if code.upper().startswith("SAL"):
        code = code[3:]

    if code.endswith(".0"):
        code = code[:-2]

    if not code.isdigit():
        raise ValueError(f"Invalid SAL code: {value}")

    return str(int(code)).zfill(5)


def getEssendonBoundary(force=False):
    destination = GEOGRAPHIC_DIR / "essendon_sed_2022.geojson"

    if destination.exists() and not force:
        return gpd.read_file(destination)

    layer = getLayer(SED_SERVICE, "SED")
    nameField = getNameField(layer, "SED_NAME")
    stateField = getNameField(layer, "STATE_CODE")

    results = queryFeatures(
        layer,
        {
            "where": (
                f"{stateField} = '2' AND "
                f"{nameField} = 'Essendon (Northern Metropolitan)'"
            ),
        },
    )

    if len(results) != 1:
        raise ValueError(
            f"Expected one Essendon SED, received {len(results)}"
        )

    if results.geometry.iloc[0].is_empty:
        raise ValueError("Essendon boundary is empty")

    GEOGRAPHIC_DIR.mkdir(parents=True, exist_ok=True)
    results.to_file(destination, driver="GeoJSON")

    return results


def getLocalSuburbBoundaries(force=False):
    destination = GEOGRAPHIC_DIR / "essendon_sal_2021.geojson"

    if destination.exists() and not force:
        suburbs = gpd.read_file(destination)
        suburbs["SAL_CODE_2021"] = suburbs["SAL_CODE_2021"].map(normaliseSAL)
        suburbs["suburb"] = suburbs["suburb"].map(normalizeSuburb)
        return suburbs

    district = getEssendonBoundary()

    xmin, ymin, xmax, ymax = district.total_bounds

    layer = getLayer(SAL_SERVICE, "SAL")
    stateField = getNameField(layer, "STATE_CODE")

    suburbs = queryFeatures(
        layer,
        {
            "where": f"{stateField} = '2'",
            "geometry": f"{xmin},{ymin},{xmax},{ymax}",
            "geometryType": "esriGeometryEnvelope",
            "inSR": 4326,
            "spatialRel": "esriSpatialRelIntersects",
        },
    )

    codeColumn = next(
        (
            column for column in suburbs.columns
            if column.upper().startswith("SAL_CODE")
        ),
        None,
    )

    nameColumn = next(
        (
            column for column in suburbs.columns
            if column.upper().startswith("SAL_NAME")
        ),
        None,
    )

    if codeColumn is None or nameColumn is None:
        raise ValueError(
            "ABS SAL identifier or name fields not found"
        )

    suburbs = suburbs.rename(
        columns={
            codeColumn: "SAL_CODE_2021",
            nameColumn: "suburb",
        }
    )

    suburbs["SAL_CODE_2021"] = (
        suburbs["SAL_CODE_2021"].map(normaliseSAL)
    )
    suburbs["suburb"] = suburbs["suburb"].map(normalizeSuburb)

    if suburbs["SAL_CODE_2021"].duplicated().any():
        raise ValueError("Duplicate SAL codes in spatial dataset")

    GEOGRAPHIC_DIR.mkdir(parents=True, exist_ok=True)

    suburbs.to_file(destination, driver="GeoJSON")

    return suburbs


def getGeographicCrosswalk():
    district = getEssendonBoundary().to_crs(AREA_CRS)
    suburbs = getLocalSuburbBoundaries().to_crs(AREA_CRS)

    districtShape = district.geometry.iloc[0]

    if not districtShape.is_valid:
        raise ValueError("Invalid Essendon district geometry")

    if not suburbs.geometry.is_valid.all():
        raise ValueError("Invalid SAL geometry")

    records = []

    for row in suburbs.itertuples():
        suburbShape = row.geometry

        intersection = suburbShape.intersection(
            districtShape
        )

        intersectionArea = intersection.area

        if intersectionArea <= 0:
            continue

        suburbArea = suburbShape.area
        districtArea = districtShape.area

        records.append({
            "SAL_CODE_2021": row.SAL_CODE_2021,
            "suburb": row.suburb,
            "suburbAreaKm2": suburbArea / 1e6,
            "areaInEssendonKm2": intersectionArea / 1e6,
            "suburbCoverage": intersectionArea / suburbArea,
            "districtAreaShare": intersectionArea / districtArea,
        })

    crosswalk = pd.DataFrame(records)

    if crosswalk.empty:
        raise ValueError("No SAL intersections with Essendon")

    crosswalk = crosswalk.sort_values(
        "areaInEssendonKm2",
        ascending=False,
    ).reset_index(drop=True)

    return crosswalk, suburbs, district


def validateCrosswalk(crosswalk, district, tolerance=0.005):
    coveredArea = crosswalk["areaInEssendonKm2"].sum()
    districtArea = district.geometry.iloc[0].area / 1e6

    coverage = coveredArea / districtArea

    if not 1 - tolerance <= coverage <= 1 + tolerance:
        raise ValueError(
            f"SAL coverage of Essendon is {coverage:.2%}. Check missing polygons, geographic definitions or overlapping source features.")

    if not crosswalk["suburbCoverage"].between(
        0, 1.000001
    ).all():
        raise ValueError("Invalid suburb coverage proportions")

    return {
        "districtAreaKm2": districtArea,
        "salIntersectionAreaKm2": coveredArea,
        "districtCoverage": coverage,
        "intersectingSuburbs": len(crosswalk),
    }


def saveGeographicData(directory="data/processed"):
    crosswalk, suburbs, district = getGeographicCrosswalk()

    report = validateCrosswalk(crosswalk, district)

    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)

    crosswalk.to_csv(
        output / "essendonSalCrosswalk.csv",
        index=False,
    )

    return report
