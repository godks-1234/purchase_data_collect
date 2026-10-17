# -*- coding: utf-8 -*-
"""Collect KIS Korea stock 1-minute bars and intraday foreign/institution estimates.

Read-only collector: it does not contain any order/trading API calls.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
TOKEN_CACHE_PATH = ROOT / ".token_cache.json"
KST = timezone(timedelta(hours=9))

PRICE_PATH = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
FLOW_PATH = "/uapi/domestic-stock/v1/quotations/investor-trend-estimate"
PRICE_TR_ID = "FHKST03010230"  # 주식일별분봉조회
FLOW_TR_ID = "HHPTJ04160200"   # 종목별 외인기관 추정가집계


def kst_now() -> datetime:
    return datetime.now(KST)


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"{CONFIG_PATH} 파일이 없습니다. config.example.json을 config.json으로 복사한 뒤 API 키를 입력하세요."
        )
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = json.load(f)

    for key in ("appkey", "appsecret", "watchlist"):
        if not cfg.get(key):
            raise ValueError(f"config.json의 {key} 값을 입력하세요.")
    if not isinstance(cfg["watchlist"], list) or not cfg["watchlist"]:
        raise ValueError("watchlist는 종목코드와 이름을 담은 리스트여야 합니다.")
    cfg.setdefault("base_url", "https://openapi.koreainvestment.com:9443")
    cfg.setdefault("poll_seconds", 300)
    return cfg


class KISClient:
    def __init__(self, appkey: str, appsecret: str, base_url: str) -> None:
        self.appkey = appkey
        self.appsecret = appsecret
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.token: str | None = None

    def authenticate(self, force_refresh: bool = False) -> None:
        # Reuse a still-valid token locally; KIS may restrict issuing duplicate tokens frequently.
        if not force_refresh and TOKEN_CACHE_PATH.exists():
            try:
                with TOKEN_CACHE_PATH.open("r", encoding="utf-8") as f:
                    cached = json.load(f)
                expires_at = datetime.fromisoformat(cached.get("expires_at", ""))
                if cached.get("appkey") == self.appkey and cached.get("access_token") and expires_at > kst_now() + timedelta(minutes=5):
                    self.token = cached["access_token"]
                    logging.info("로컬 캐시의 유효한 API 토큰 사용")
                    return
            except (ValueError, OSError, TypeError):
                pass

        url = f"{self.base_url}/oauth2/tokenP"
        response = self.session.post(
            url,
            json={
                "grant_type": "client_credentials",
                "appkey": self.appkey,
                "appsecret": self.appsecret,
            },
            headers={"content-type": "application/json; charset=utf-8"},
            timeout=20,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"토큰 응답을 JSON으로 읽지 못했습니다: HTTP {response.status_code}") from exc
        if not response.ok or "access_token" not in payload:
            msg = payload.get("error_description") or payload.get("msg1") or payload
            raise RuntimeError(f"API 인증 실패(HTTP {response.status_code}): {msg}")
        self.token = payload["access_token"]
        expires_raw = payload.get("access_token_token_expired", "")
        try:
            expires_at = datetime.strptime(expires_raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        except (ValueError, TypeError):
            expires_at = kst_now() + timedelta(hours=20)
        # This cache stays local and is excluded from version control by .gitignore.
        with TOKEN_CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump({"appkey": self.appkey, "access_token": self.token, "expires_at": expires_at.isoformat()}, f)
        logging.info("KIS API 인증 완료")

    def get(self, path: str, tr_id: str, params: dict[str, str]) -> dict[str, Any]:
        if not self.token:
            self.authenticate()
        assert self.token is not None
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token}",
            "appkey": self.appkey,
            "appsecret": self.appsecret,
            "tr_id": tr_id,
            "custtype": "P",
        }
        response = self.session.get(
            f"{self.base_url}{path}", headers=headers, params=params, timeout=20
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"API 응답을 JSON으로 읽지 못했습니다: HTTP {response.status_code}") from exc

        # Access tokens normally last about a day. Refresh once if the server rejects it.
        if response.status_code == 401:
            self.authenticate(force_refresh=True)
            headers["authorization"] = f"Bearer {self.token}"
            response = self.session.get(
                f"{self.base_url}{path}", headers=headers, params=params, timeout=20
            )
            payload = response.json()

        if not response.ok or ("rt_cd" in payload and str(payload["rt_cd"]) != "0"):
            message = payload.get("msg1") or payload.get("error_description") or payload
            code = payload.get("msg_cd", "")
            raise RuntimeError(f"API 조회 실패 {code}: {message}")
        return payload


def get_array(payload: dict[str, Any], field: str) -> list[dict[str, Any]]:
    data = payload.get(field, [])
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    return []


def save_rows(path: Path, rows: list[dict[str, Any]], dedupe_keys: list[str] | None = None) -> int:
    if not rows:
        return 0
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    new_df = pd.DataFrame(rows)
    if path.exists():
        try:
            old_df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        except (pd.errors.EmptyDataError, UnicodeDecodeError):
            old_df = pd.DataFrame()
        df = pd.concat([old_df, new_df], ignore_index=True, sort=False)
    else:
        df = new_df
    if dedupe_keys and all(k in df.columns for k in dedupe_keys):
        df = df.drop_duplicates(subset=dedupe_keys, keep="last")
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return len(new_df)


def collect_price(client: KISClient, symbol: str, name: str, now: datetime) -> int:
    payload = client.get(
        PRICE_PATH,
        PRICE_TR_ID,
        {
            "FID_COND_MRKT_DIV_CODE": "J",  # KRX
            "FID_INPUT_ISCD": symbol,
            "FID_INPUT_HOUR_1": now.strftime("%H%M%S"),
            "FID_INPUT_DATE_1": now.strftime("%Y%m%d"),
            "FID_PW_DATA_INCU_YN": "Y",
            "FID_FAKE_TICK_INCU_YN": "",
        },
    )
    rows = get_array(payload, "output2")
    if not rows:
        logging.warning("%s(%s): 분봉 결과가 비어 있습니다. 휴장일/장외시간/종목코드를 확인하세요.", name, symbol)
        return 0
    collected_at = now.isoformat(timespec="seconds")
    for row in rows:
        row["collected_at_kst"] = collected_at
        row["symbol"] = symbol
        row["name"] = name
    path = DATA_DIR / f"prices_{now:%Y%m%d}.csv"
    # Most reliable uniqueness key is stock + date + minute time from the API response.
    keys = ["symbol", "stck_bsop_date", "stck_cntg_hour"]
    written = save_rows(path, rows, dedupe_keys=keys)
    logging.info("%s(%s): 분봉 %d건 수신(파일 신규행 기준)", name, symbol, len(rows))
    return written


def collect_flow(client: KISClient, symbol: str, name: str, now: datetime) -> int:
    payload = client.get(
        FLOW_PATH,
        FLOW_TR_ID,
        {"MKSC_SHRN_ISCD": symbol},
    )
    rows = get_array(payload, "output2")
    if not rows:
        logging.warning("%s(%s): 수급 추정 결과가 비어 있습니다.", name, symbol)
        return 0
    collected_at = now.isoformat(timespec="seconds")
    for row in rows:
        row["collected_at_kst"] = collected_at
        row["symbol"] = symbol
        row["name"] = name
    path = DATA_DIR / f"flows_{now:%Y%m%d}.csv"
    # Keep every snapshot, including repeated values. Comparing snapshots reveals when values changed.
    written = save_rows(path, rows)
    logging.info("%s(%s): 수급 추정 스냅샷 %d행 기록", name, symbol, len(rows))
    return written


def in_regular_session(now: datetime) -> bool:
    # Basic weekday/session guard only; it does not include the Korean exchange holiday calendar.
    return now.weekday() < 5 and (now.hour, now.minute) >= (9, 0) and (now.hour, now.minute) <= (15, 35)


def collect_once(client: KISClient, watchlist: list[dict[str, str]], force: bool = False) -> None:
    now = kst_now()
    if not force and not in_regular_session(now):
        logging.info("정규장 수집 시간이 아닙니다. KST 09:00~15:35, 평일에 대기합니다.")
        return
    logging.info("수집 주기 시작: %s", now.isoformat(timespec="seconds"))
    for item in watchlist:
        symbol = str(item["code"]).strip().zfill(6)
        name = str(item.get("name", symbol))
        try:
            collect_price(client, symbol, name, now)
        except Exception as exc:  # Keep collecting other tickers if one endpoint fails.
            logging.exception("%s(%s) 가격 수집 오류: %s", name, symbol, exc)
        try:
            collect_flow(client, symbol, name, now)
        except Exception as exc:
            logging.exception("%s(%s) 수급 수집 오류: %s", name, symbol, exc)
        # Be polite to the API and avoid firing requests back-to-back.
        time.sleep(0.3)


def main() -> None:
    parser = argparse.ArgumentParser(description="KIS 장중 수급 추정치 및 1분봉 수집기(조회 전용)")
    parser.add_argument("--once", action="store_true", help="장외시간에도 한 번 조회하여 연결을 점검")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler()],
    )
    cfg = load_config()
    client = KISClient(cfg["appkey"], cfg["appsecret"], cfg["base_url"])
    client.authenticate()
    watchlist = cfg["watchlist"]

    if args.once:
        collect_once(client, watchlist, force=True)
        return

    poll_seconds = max(60, int(cfg.get("poll_seconds", 300)))
    logging.info("수집기 시작: 감시종목 %d개, 주기 %d초. 종료하려면 Ctrl+C", len(watchlist), poll_seconds)
    while True:
        cycle_start = time.monotonic()
        try:
            collect_once(client, watchlist, force=False)
        except Exception:
            logging.exception("수집 주기 오류")
        elapsed = time.monotonic() - cycle_start
        time.sleep(max(5, poll_seconds - elapsed))


if __name__ == "__main__":
    main()
