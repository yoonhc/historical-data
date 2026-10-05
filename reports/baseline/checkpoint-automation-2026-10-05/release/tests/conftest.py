import csv
import io
import zipfile
from datetime import timedelta

import pytest
from marketdata.domain import micros, sha256, utc
from marketdata.store import Store


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "store")
    yield instance
    instance.close()


def kline(ts="2024-01-01", *, unit="ms", price="100"):
    ts = utc(ts)
    divisor = 1000 if unit == "ms" else 1
    return [
        str(micros(ts) // divisor),
        price,
        "105",
        "95",
        "101",
        "2",
        str(micros(ts + timedelta(minutes=1)) // divisor - 1),
        "200",
        "3",
        "1",
        "100",
        "0",
    ]


def zip_csv(rows, member="BTCUSDT-1m-2024-01-01.csv"):
    text = io.StringIO()
    csv.writer(text).writerows(rows)
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(member, text.getvalue())
    return blob.getvalue()


def source_file(tmp_path, rows, name="BTCUSDT-1m-2024-01-01.zip"):
    path = tmp_path / name
    blob = zip_csv(rows, name[:-4] + ".csv")
    path.write_bytes(blob)
    path.with_suffix(path.suffix + ".CHECKSUM").write_text(f"{sha256(blob)}  {name}\n")
    return path
