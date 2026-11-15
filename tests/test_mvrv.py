"""MVRV Z-Score: Coin Metrics yanıtının ayrıştırılması, Z-Score matematiği, önbellek ve hata yolları (internet gerekmez).

DİKKAT: Sahte yanıtlar Coin Metrics Community API'nin bilinen biçimini (data[] satırları, sayılar metin olarak, next_page_url)
taklit eder. Bu testler ayrıştırma ve akış mantığını doğrular; servisin bugünkü yanıt biçimini DOĞRULAMAZ."""
import io
import json
import statistics
import sys
import tempfile
import urllib.error
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core import mvrv

URL = "https://community-api.coinmetrics.io/v4/timeseries/asset-metrics"


def rows(n=1200, start="2020-01-01", missing_mvrv=50):
    out = []
    for i, day in enumerate(pd.date_range(start, periods=n, freq="D", tz="UTC")):
        row = {"asset": "btc", "time": f"{day:%Y-%m-%d}T00:00:00.000000000Z", "CapMrktCurUSD": f"{1e11 + 3e8 * i:.4f}"}
        if i >= missing_mvrv:
            row["CapMVRVCur"] = f"{1.2 + 0.001 * i:.6f}"
        out.append(row)
    return out


class Response:
    def __init__(self, payload):
        self.body = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self, *a):
        return self.body.read(*a)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(pages, requests):
    queue = iter(pages)

    def urlopen(request, timeout=None):
        requests.append(request.full_url)
        item = next(queue)
        if isinstance(item, Exception):
            raise item
        return Response(item)
    return urlopen


def test_parse_handles_strings_missing_metrics_and_duplicates():
    data = rows(5, missing_mvrv=2)
    data.append(dict(data[4], CapMrktCurUSD="999"))  # aynı gün tekrar: sonuncusu geçerli
    data.append({"asset": "btc", "CapMrktCurUSD": "1"})  # zamansız satır atlanır
    data.append({"asset": "btc", "time": "2020-01-09T00:00:00.000000000Z", "CapMrktCurUSD": "abc", "CapMVRVCur": None})
    frame = mvrv._parse(data)
    assert list(frame.columns) == ["mcap", "mvrv"] and str(frame.index.tz) == "UTC" and frame.index.is_monotonic_increasing
    assert len(frame) == 6 and frame["mvrv"].iloc[:2].isna().all() and frame["mvrv"].iloc[2:5].notna().all()
    assert frame["mcap"].loc["2020-01-05"] == 999.0 and np.isnan(frame["mcap"].iloc[-1]), "metin sayıya çevrilir, bozuk değer NaN"
    assert frame.index[0] == pd.Timestamp("2020-01-01", tz="UTC")


def test_zscore_matches_hand_calculation_and_independent_reference():
    frame = pd.DataFrame({"mcap": [100.0, 200.0, 300.0], "mvrv": [2.0, 2.0, 1.5]},
                         index=pd.date_range("2024-01-01", periods=3, freq="D", tz="UTC"))
    z = mvrv.zscore(frame)
    assert np.isnan(z.iloc[0]), "tek günlük geçmişte standart sapma tanımsız"
    assert np.isclose(z.iloc[1], (200 - 100) / statistics.pstdev([100, 200]))            # gerçekleşmiş değer = 200 / 2 = 100
    assert np.isclose(z.iloc[2], (300 - 200) / statistics.pstdev([100, 200, 300]))       # gerçekleşmiş değer = 300 / 1.5 = 200
    # uzun seride bağımsız (saf Python) referans
    big = mvrv._parse(rows(400, missing_mvrv=0))
    got = mvrv.zscore(big)
    expected = []
    for i in range(len(big)):
        if i == 0:
            expected.append(float("nan"))
            continue
        mcap, ratio = big["mcap"].iloc[i], big["mvrv"].iloc[i]
        expected.append((mcap - mcap / ratio) / statistics.pstdev(list(big["mcap"].iloc[:i + 1])))
    np.testing.assert_allclose(got.to_numpy(), expected, rtol=1e-9, equal_nan=True)
    # geleceğe bakmaz: kesilmiş veriyle hesaplanan, tam veriyle hesaplananın aynı günleriyle aynı
    pd.testing.assert_series_equal(mvrv.zscore(big.iloc[:200]), got.iloc[:200])


def test_zscore_invalid_inputs_become_nan():
    index = pd.date_range("2024-01-01", periods=6, freq="D", tz="UTC")
    frame = pd.DataFrame({"mcap": [100.0, 200.0, np.nan, 400.0, 500.0, 600.0], "mvrv": [2.0, 0.0, 2.0, np.nan, -1.0, 2.0]}, index=index)
    z = mvrv.zscore(frame)
    assert z.iloc[[1, 2, 3, 4]].isna().all(), "MVRV <= 0, NaN veya piyasa değeri NaN olan günlerde Z-Score olmaz"
    assert np.isfinite(z.iloc[5])
    flat = pd.DataFrame({"mcap": [100.0] * 5, "mvrv": [2.0] * 5}, index=index[:5])
    assert mvrv.zscore(flat).isna().all(), "standart sapma 0 ise tanımsız"
    assert mvrv.zscore(frame).name == "mvrv_z"


def test_download_pagination_parameters_and_validation():
    data = rows(1200)
    requests = []
    pages = [{"data": data[:700], "next_page_url": URL + "?page=2"}, {"data": data[700:]}]
    with mock.patch("urllib.request.urlopen", fake_urlopen(pages, requests)):
        frame = mvrv._download(mvrv.URL, 5)
    assert len(frame) == 1200 and len(requests) == 2 and requests[1] == URL + "?page=2"
    for part in ("assets=btc", "metrics=CapMrktCurUSD%2CCapMVRVCur", "frequency=1d", "page_size=10000"):
        assert part in requests[0], (part, requests[0])
    with mock.patch("urllib.request.urlopen", fake_urlopen([{"data": rows(50)}], [])):
        try:
            mvrv._download(mvrv.URL, 5)
            assert False
        except ValueError as e:
            assert "az veri" in str(e)
    with mock.patch("urllib.request.urlopen", fake_urlopen([{"data": rows(1200, missing_mvrv=1100)}], [])):
        try:
            mvrv._download(mvrv.URL, 5)
            assert False, "MVRV değeri neredeyse hiç yoksa yanıt kabul edilmemeli"
        except ValueError:
            pass


def test_fetch_writes_cache_retries_and_falls_back_to_a_fresh_cache_only():
    with tempfile.TemporaryDirectory() as tmp, mock.patch.object(mvrv, "CACHE_FILE", Path(tmp) / "cache" / "mvrv.csv"), \
            mock.patch.object(mvrv.time, "sleep", lambda s: None):
        # 1) ilk deneme düşer, ikincisi başarılı; önbellek yazılır
        requests = []
        with mock.patch("urllib.request.urlopen", fake_urlopen([urllib.error.URLError("dns"), {"data": rows(1200)}], requests)):
            frame = mvrv.fetch_mvrv_data()
        assert len(frame) == 1200 and len(requests) == 2 and mvrv.CACHE_FILE.exists()
        # 2) API tamamen çöker, önbellek BAYAT: hata verir (eski veriyle sahte skor üretilmez)
        down = [urllib.error.URLError("dns")] * 3
        with mock.patch("urllib.request.urlopen", fake_urlopen(down, [])):
            try:
                mvrv.fetch_mvrv_data()
                assert False
            except RuntimeError as e:
                assert "MVRV verisi alınamadı" in str(e)
        # 3) API çöker, önbellek TAZE: önbellek kullanılır
        today = pd.Timestamp.now(tz="UTC").normalize()
        fresh = mvrv._parse(rows(1200, start=f"{(today - pd.Timedelta(days=1199)):%Y-%m-%d}"))
        fresh.rename_axis("date").to_csv(mvrv.CACHE_FILE)
        with mock.patch("urllib.request.urlopen", fake_urlopen(down, [])):
            cached = mvrv.fetch_mvrv_data()
        assert len(cached) == 1200 and cached.index.max() == today and str(cached.index.tz) == "UTC"
        pd.testing.assert_series_equal(mvrv.zscore(cached), mvrv.zscore(fresh), check_freq=False, check_index_type=False)  # us/ns farkı önemsiz
        # 4) bozuk yanıt biçimi de hata sayılır
        with mock.patch("urllib.request.urlopen", fake_urlopen([{"oops": 1}] * 3, [])):
            mvrv.CACHE_FILE.unlink()
            try:
                mvrv.fetch_mvrv_data()
                assert False
            except RuntimeError:
                pass


def test_all():
    test_parse_handles_strings_missing_metrics_and_duplicates()
    test_zscore_matches_hand_calculation_and_independent_reference()
    test_zscore_invalid_inputs_become_nan()
    test_download_pagination_parameters_and_validation()
    test_fetch_writes_cache_retries_and_falls_back_to_a_fresh_cache_only()


if __name__ == "__main__":
    test_all()
    print("MVRV testleri geçti ✔")
