
from pathlib import Path
from pprint import pprint

import pandas as pd

from geography import saveGeographicData


DATA_DIR = Path("data/processed")


def validateHistoricalData():
    reports = []

    for year in (2018, 2022):
        candidatePath = (
            DATA_DIR / f"essendon_{year}_candidates.csv"
        )
        votesPath = (
            DATA_DIR / f"essendon_{year}_votes.csv"
        )
        locationsPath = (
            DATA_DIR / f"essendon_{year}_locations.csv"
        )

        for path in (
            candidatePath,
            votesPath,
            locationsPath,
        ):
            if not path.exists():
                raise FileNotFoundError(
                    f"Missing {path}. Run Commit 02 collection first."
                )

        candidates = pd.read_csv(candidatePath)
        votes = pd.read_csv(votesPath)
        locations = pd.read_csv(locationsPath)

        if not (
            (candidates["year"] == year).all()
            and (votes["year"] == year).all()
            and (locations["year"] == year).all()
        ):
            raise ValueError(
                f"{year}: incorrect election year in data"
            )

        totalRows = votes[
            (votes["rowType"] == "summary")
            & (votes["category"] == "total")
        ]

        published = (
            candidates.set_index("candidate")["votes"]
            .sort_index()
        )

        reconstructed = (
            totalRows.set_index("candidate")["votes"]
            .sort_index()
        )

        if not published.equals(reconstructed):
            raise ValueError(
                f"{year}: candidate totals do not reconcile"
            )

        ordinary = locations[
            locations["rowType"] == "votingCentre"
        ]

        reports.append({
            "year": year,
            "candidates": len(candidates),
            "ordinaryVotingCentres": len(ordinary),
            "formalVotes": int(published.sum()),
            "validation": "passed",
        })

    return reports


def validateCensusJoin():
    crosswalk = pd.read_csv(
        DATA_DIR / "essendonSalCrosswalk.csv",
        dtype={"SAL_CODE_2021": str},
    )

    demographics = pd.read_csv(
        DATA_DIR / "vic_2021_suburb_demographics.csv",
        dtype={"SAL_CODE_2021": str},
        low_memory=False,
    )

    if demographics["SAL_CODE_2021"].duplicated().any():
        raise ValueError("Duplicate SAL IDs in Census dataset")

    comparison = crosswalk[
        ["SAL_CODE_2021", "suburb"]
    ].merge(
        demographics[["SAL_CODE_2021"]],
        on="SAL_CODE_2021",
        how="left",
        validate="one_to_one",
        indicator=True,
    )

    missing = comparison[
        comparison["_merge"] != "both"
    ]

    if not missing.empty:
        raise ValueError(
            "Missing Census records for: "
            + ", ".join(missing["suburb"])
        )

    return {
        "intersectingSuburbs": len(crosswalk),
        "matchedCensusRecords": len(comparison),
        "validation": "passed",
    }


def main():
    print("Victoria Votes 2026")
    print("Essendon geography and validation")
    print("=" * 45)

    print("\nChecking historical electoral data...")
    pprint(validateHistoricalData())

    print("\nConstructing geographic crosswalk...")
    pprint(saveGeographicData())

    print("\nChecking Census geographic join...")
    pprint(validateCensusJoin())

    print("\nAll geographic checks passed.")


if __name__ == "__main__":
    main()
