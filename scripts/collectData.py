
from pprint import pprint

from CensusData import saveSuburbDemographics
from VECResults import saveElectionResults


def main():
    print("Victoria Votes 2026")
    print("Historical data collection")
    print("=" * 45)

    for year in (2018, 2022):
        print(f"\nCollecting Essendon {year} election results...")

        report = saveElectionResults(year)

        pprint(report)

        print(f"{year} election data validated.")

    print("\nCollecting ABS 2021 Census data...")

    censusReport = saveSuburbDemographics()

    pprint(censusReport)

    print("\nData collection completed successfully.")


if __name__ == "__main__":
    main()
