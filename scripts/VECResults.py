from io import StringIO
from pathlib import Path
from urllib.parse import urljoin
import pandas as pd
import requests
from bs4 import BeautifulSoup

URL_BY_YEAR = {
    2018: "https://itsitecoreblobvecprd01.blob.core.windows.net/public-files/historical-results/state2018/essendondistrict.html",
    2022: "https://www.vec.vic.gov.au/results/state-election-results/2022-state-election-results/results-by-district/essendon-district-results"
}

EXPECTED_TOTALS = {
    2018: {"formal": 41472, "informal": 2067, "total": 43539},
    2022: {"formal": 41867, "informal": 1761, "total": 43628}
}

SUMMARY_LABELS = {
    "ordinary votes total": "ordinary",
    "absent votes": "absent",
    "early votes": "early",
    "marked as voted votes": "marked_as_voted",
    "postal votes": "postal",
    "provisional votes": "provisional",
    "total": "total"
}


def getPage(url):
    response = requests.get(
        url,
        timeout=60,
        headers={
            "User-Agent": "SamMurphy2026/0.1 (research)"
        }
    )

    response.raise_for_status()

    return BeautifulSoup(response.text, "html.parser")


def cleanText(value):
    return " ".join(str(value).replace("\xa0", " ").split()).strip()


def asInteger(value):
    text = cleanText(value).replace(",", "")
    if not text or text in {"-", "-"}:
        return 0
    return int(text)


def findTable(soup, requiredTerms):
    for table in soup.find_all("table"):
        content = cleanText(table.get_text(" ", strip=True)).lower()

        if all(term.lower() in content for term in requiredTerms):
            return table

    raise ValueError(f"Could not identify VEC table containing {requiredTerms}")


def tableRows(table):
    rows = []
    for tr in table.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)

        if cells:
            rows.append([cleanText(cell.get_text(" ", strip=True))
                        for cell in cells])

    return rows


def getResultsPage(year):
    if year not in URL_BY_YEAR:
        raise ValueError("Need year to be 2018 or 2022")

    return getPage(URL_BY_YEAR[year])


def getCandidates(year):
    soup = getResultsPage(year)
    table = findTable(soup, ["candidate", "party", "1st pref"])

    records = []

    for cells in tableRows(table):
        if len(cells) < 4:
            continue

        candidate, party, votes, percentage = cells[:4]

        if not votes.replace(",", "").isdigit():
            continue

        records.append({
            "year": year,
            "electorate": "Essendon",
            "candidate": candidate,
            "party": party or "Independent",
            "votes": asInteger(votes),
            "percentage": float(percentage.replace("%", ""))
        })

    if not records:
        raise ValueError(f"No candidate results for {year}")

    return pd.DataFrame(records)


def getVotingCentreURL(year):
    soup = getResultsPage(year)

    for link in soup.find_all("a", href=True):
        label = cleanText(link.get_text(" ", strip=True)).lower()

        if ("recheck results by voting centre" in label):
            return urljoin(URL_BY_YEAR[year], link["href"])

    raise ValueError(
        f"Could not locate first-pref voting-centre page for {year}")


def getVotingCentres(year=2022):
    candidates = getCandidates(year)
    url = getVotingCentreURL(year)
    soup = getPage(url)

    table = findTable(
        soup,
        ["voting centres", "informal votes", "total votes polled"]
    )

    rows = tableRows(table)

    candidateNames = candidates["candidate"].tolist()

    n = len(candidateNames)

    records = []
    totals = []
    for cells in rows:
        if len(cells) < n + 3:
            continue

        label = cleanText(cells[0])
        normalised = label.lower()

        if not label:
            continue

        if normalised.startswith("percentage of formal"):
            continue

        if normalised in {"voting centres", "voting centre"}:
            continue

        numbers = cells[1:n+3]

        if not all(value.replace(",", "").isdigit() for value in numbers):
            continue

        counts = [asInteger(v) for v in numbers]
        candidateVotes = counts[:n]
        informal = counts[n]
        total = counts[n+1]

        if sum(candidateVotes) + informal != total:
            raise ValueError(
                f"{year} {label}: candidate votes plus informal do not equal total.")

        if normalised in SUMMARY_LABELS:
            rowType = "summary"
            category = SUMMARY_LABELS[normalised]

        else:
            rowType = "votingCentre"
            category = "ordinary"

        totals.append({
            "year": year,
            "electorate": "Essendon",
            "location": label,
            "rowType": rowType,
            "category": category,
            "formalVotes": sum(candidateVotes),
            "informalVotes": informal,
            "totalVotes": total
        })

        for candidate, votes in zip(candidateNames, candidateVotes):
            party = candidates.loc[
                candidates["candidate"] == candidate, "party"
            ].iloc[0]

            records.append({
                "year": year,
                "electorate": "Essendon",
                "location": label,
                "rowType": rowType,
                "category": category,
                "candidate": candidate,
                "party": party,
                "votes": votes,
            })

    votes = pd.DataFrame(records)
    locations = pd.DataFrame(totals)

    if votes.empty or locations.empty:
        raise ValueError(f"no voting centre records for year {year}")

    return votes, locations


def getElectionResults(year=2022):
    candidates = getCandidates(year)
    votes, locations = getVotingCentres(year)

    return {
        "candidates": candidates,
        "votes": votes,
        "locations": locations
    }


def validateResults(results, year):
    candidates = results["candidates"]
    votes = results["votes"]
    locations = results["locations"]

    expected = EXPECTED_TOTALS[year]

    def check(condition, message):
        if not condition:
            raise ValueError(f"{year}: {message}")

    overall = locations[
        (locations["rowType"] == "summary")
        & (locations["category"] == "total")
    ]

    check(
        len(overall) == 1,
        "Expected exactly one overall totals row",
    )

    overall = overall.iloc[0]

    check(
        overall["formalVotes"] == expected["formal"],
        "Formal votes do not match official total",
    )
    check(
        overall["informalVotes"] == expected["informal"],
        "Informal votes do not match official total",
    )
    check(
        overall["totalVotes"] == expected["total"],
        "Total votes do not match official total",
    )

    check(
        candidates["votes"].sum() == expected["formal"],
        "Candidate first-preference totals do not reconcile",
    )

    totalCandidateVotes = (
        votes[
            (votes["rowType"] == "summary")
            & (votes["category"] == "total")
        ]
        .set_index("candidate")["votes"]
    )

    published = candidates.set_index("candidate")["votes"]

    check(
        totalCandidateVotes.sort_index().equals(
            published.sort_index()
        ),
        "Voting-centre candidate totals differ from published totals",
    )

    ordinary = locations[
        locations["rowType"] == "votingCentre"
    ]

    ordinarySummary = locations[
        (locations["rowType"] == "summary")
        & (locations["category"] == "ordinary")
    ]

    check(
        len(ordinarySummary) == 1,
        "Expected exactly one ordinary-votes summary",
    )

    ordinarySummary = ordinarySummary.iloc[0]

    for column in [
        "formalVotes",
        "informalVotes",
        "totalVotes",
    ]:
        check(
            ordinary[column].sum() == ordinarySummary[column],
            f"Ordinary voting-centre {column} do not reconcile",
        )

    categories = locations[
        (locations["rowType"] == "summary")
        & (locations["category"] != "total")
    ]

    for column in [
        "formalVotes",
        "informalVotes",
        "totalVotes",
    ]:
        check(
            categories[column].sum() == overall[column],
            f"Voting categories {column} do not reconcile",
        )

    return {
        "year": year,
        "electorate": "Essendon",
        "candidates": len(candidates),
        "votingCentres": len(ordinary),
        "formalVotes": int(overall["formalVotes"]),
        "informalVotes": int(overall["informalVotes"]),
        "totalVotes": int(overall["totalVotes"]),
        "validated": True,
    }


def saveElectionResults(year, directory="data/processed"):
    results = getElectionResults(year)
    report = validateResults(results, year)

    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)

    for name, frame in results.items():
        frame.to_csv(
            output / f"essendon_{year}_{name}.csv",
            index=False,
        )

    return report
