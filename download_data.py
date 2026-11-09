"""Step 1 - Get the data: downloading the NYC TLC files into the data/ folder.

Files needed for the project: Parquet files of yellow-taxi trips for months April-July, the taxi-zone lookup
(CSV) and the taxi-zone shapes (zip, for the map).
"""
import os
import time
import urllib.request

DATA_DIR = os.environ.get("DATA_DIR", "data")

# The months to download, separated by commas (format: YYYY-MM).
# Allows to change the months we choose to download without changing the code.
MONTHS = os.environ.get("TAXI_MONTHS", "2026-04,2026-05,2026-06,2026-07").split(",")

BASE_URL = "https://d37ci6vzurychx.cloudfront.net"


def trips_file(month):
    """The name of the TLC trips file for one month."""
    return f"yellow_tripdata_{month}.parquet"


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    print(f"Downloading NYC TLC data for {', '.join(MONTHS)} into {DATA_DIR}/")

    # file name on disk -> where to download it from
    files = {}
    for month in MONTHS:
        files[trips_file(month)] = f"{BASE_URL}/trip-data/{trips_file(month)}"
    files["taxi_zone_lookup.csv"] = f"{BASE_URL}/misc/taxi_zone_lookup.csv"
    files["taxi_zones.zip"] = f"{BASE_URL}/misc/taxi_zones.zip"

    for name, url in files.items():
        path = os.path.join(DATA_DIR, name)

        if os.path.exists(path):
            print(f"  {name}: already there, skipped")
            continue

        t = time.perf_counter()
        # Save to a ".part" file first and rename it only at the end,
        # so a half-finished download is never taken for a complete file.
        # If the download fails, Python stops here with an error.
        urllib.request.urlretrieve(url, path + ".part")
        os.rename(path + ".part", path)

        size = round(os.path.getsize(path) / 1024**2, 1)
        print(f"  {name}: {size} MB in {round(time.perf_counter() - t, 2)} s")

    print("Download done.")


if __name__ == "__main__":
    main()