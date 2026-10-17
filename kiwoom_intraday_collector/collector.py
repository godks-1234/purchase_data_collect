# -*- coding: utf-8 -*-
"""Kiwoom REST API collector for Korean stock minute bars and intraday investor flow.

Read-only: this program does not call any order/trading endpoints.
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
CHART_PATH = "/api/dostk/chart"


def kst_now() -> datetime:
    return datetime.now(KST)


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"{CONFIG_PATH}가 없습니다. config.example.json을 config.json으로 복사한 뒤 키를 입력하세요."
        )
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        cfg = json.load(f)
    for key in ("appkey", "secretkey", "watchlist"):
        if not cfg.get(key):
            raise ValueError(f"config.json의 {key} 값을 입력하세요.")
    if not isinstance(cfg["watchlist"], list) or not cfg["watchlist"]:
        raise ValueError("watchlist에는 종목코드와 이름을 담은 항목이 하나 이상 있어야 합니다.")
    cfg.setdefault("base_url", "https://api.kiwoom.com")
    cfg.setdefault("poll_seconds", 300)
    cfg.setdefault("market_type", "000")
    cfg.setdefault("amount_quantity_type", "1")
    cfg.setdefault("trade_type", "0")
    cfg.setdefault("minute_interval", "1")
    cfg.setdefault("adjusted_price_type", "1")
    cfg.setdefault("include_base_date", True)
    return cfg


class KiwoomClient:
    def __init__(self, appkey: str, secretkey: str, base_url: str) -> None:
        self.appkey = appkey
        self.secretkey = secretkey
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.token: str | None = None
        self.token_type = "Bearer"

    def authenticate(self, force_refresh: bool = False) -> None:
        if not force_refresh and TOKEN_CACHE_PATH.exists():
            try:
                with TOKEN_CACHE_PATH.open("r", encoding="utf-8") as f:
                    cached = json.load(f)
                expires_at = datetime.fromisoformat(cached.get("expires_at", ""))
                if (
                    cached.get("appkey") == self.appkey
                    and cached.get("token")
                    and expires_at > kst_now() + timedelta(minutes=5)
                ):
                    self.token = cached["token"]
                    self.token_type = cached.get("token_type", "Bearer").capitalize()
                    logging.info("로컬 캐시의 유효한 키움 API 토큰 사용")
                    return
            except (ValueError, OSError, TypeError):
                pass

        response = self.session.post(
            f"{self.base_url}/oauth2/token",
            json={
                "grant_type": "client_credentials",
                "appkey": self.appkey,
                "secretkey": self.secretkey,
            },
            headers={"Content-Type": "application/json;charset=UTF-8"},
            timeout=20,
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"토큰 응답을 JSON으로 읽지 못했습니다: HTTP {response.status_code}") from exc
        if not response.ok or not payload.get("token"):
            message = payload.get("return_msg") or payload.get("message") or payload
            raise RuntimeError(f"키움 API 인증 실패(HTTP {response.status_code}): {message}")

        self.token = payload["token"]
        self.token_type = payload.get("token_type", "Bearer").capitalize()
        expires_raw = str(payload.get("expires_dt", ""))
        try:
            expires_at = datetime.strptime(expires_raw, "%Y%m%d%H%M%S").replace(tzinfo=KST)
        except ValueError:
            # Conservative fallback; the returned expires_dt is preferred.
            expires_at = kst_now() + timedelta(hours=20)
        with TOKEN_CACHE_PATH.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "appkey": self.appkey,
                    "token": self.token,
                    "token_type": self.token_type,
                    "expires_at": expires_at.isoformat(),
                },
                f,
                ensure_ascii=False,
            )
        logging.info("키움 REST API 인증 완료")

    def post(
        self,
        api_id: str,
        path: str,
        body: dict[str, Any],
        cont_yn: str = "N",
        next_key: str = "",
    ) -> tuple[dict[str, Any], str, str]:
        if not self.token:
            self.authenticate()
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"{self.token_type} {self.token}",
            "api-id": api_id,
            "cont-yn": cont_yn,
            "next-key": next_key,
        }
        response = self.session.post(
            f"{self.base_url}{path}", headers=headers, json=body, timeout=30
        )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"{api_id} 응답을 JSON으로 읽지 못했습니다: HTTP {response.status_code}") from exc

        if response.status_code == 401:
            self.authenticate(force_refresh=True)
            headers["authorization"] = f"{self.token_type} {self.token}"
            response = self.session.post(
                f"{self.base_url}{path}", headers=headers, json=body, timeout=30
            )
            payload = response.json()

        if not response.ok:
            raise RuntimeError(f"{api_id} HTTP 오류 {response.status_code}: {payload}")
        return_code = payload.get("return_code")
        if return_code not in (None, 0, "0"):
            raise RuntimeError(f"{api_id} 조회 실패 ({return_code}): {payload.get('return_msg', payload)}")
        return payload, response.headers.get("cont-yn", "N"), response.headers.get("next-key", "")

    def all_pages(
        self, api_id: str, path: str, body: dict[str, Any], list_key: str, max_pages: int = 10
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        cont_yn, next_key = "N", ""
        for page_no in range(max_pages):
            payload, next_cont, next_value = self.post(
                api_id, path, body, cont_yn=cont_yn, next_key=next_key
            )
            page = payload.get(list_key, [])
            if isinstance(page, list):
                rows.extend(x for x in page if isinstance(x, dict))
            if str(next_cont).upper() != "Y" or not next_value:
                break
            cont_yn, next_key = "Y", next_value
            time.sleep(0.15)
        return rows


def to_num(value: Any, absolute: bool = False) -> int | None:
    if value is None:
        return None
    s = str(value).strip().replace(",", "")
    if not s or s in {"--", "-", "N/A"}:
        return None
    try:
        n = int(float(s))
        return abs(n) if absolute else n
    except ValueError:
        return None


def write_snapshot_csv(path: Path, rows: list[dict[str, Any]], key_columns: list[str] | None = None) -> int:
    if not rows:
        return 0
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    new_df = pd.DataFrame(rows)
    if key_columns and path.exists():
        try:
            old_df = pd.read_csv(path, dtype=str, encoding="utf-8-sig")
        except (pd.errors.EmptyDataError, UnicodeDecodeError):
            old_df = pd.DataFrame()
        df = pd.concat([old_df, new_df], ignore_index=True, sort=False)
        if all(k in df.columns for k in key_columns):
            df = df.drop_duplicates(subset=key_columns, keep="last")
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return len(new_df)

    # Append flow snapshots: repeated rows are intentional because collected_at_kst records
    # when each intraday investor-chart snapshot was observed.
    file_exists = path.exists() and path.stat().st_size > 0
    new_df.to_csv(
        path,
        index=False,
        mode="a" if file_exists else "w",
        header=not file_exists,
        encoding="utf-8" if file_exists else "utf-8-sig",
    )
    return len(new_df)


def collect_price(client: KiwoomClient, symbol: str, name: str, now: datetime, cfg: dict[str, Any]) -> int:
    body: dict[str, Any] = {
        "stk_cd": symbol,
        "tic_scope": str(cfg.get("minute_interval", "1")),
        "upd_stkpc_tp": str(cfg.get("adjusted_price_type", "1")),
    }
    if cfg.get("include_base_date", True):
        body["base_dt"] = now.strftime("%Y%m%d")
    rows = client.all_pages("ka10080", CHART_PATH, body, "stk_min_pole_chart_qry")
    # Keep only bars belonging to the current collection date in the current day's CSV.
    expected_date = now.strftime("%Y%m%d")
    rows = [r for r in rows if str(r.get("cntr_tm", "")).startswith(expected_date)]
    if not rows:
        logging.warning("%s(%s): 오늘 날짜의 분봉 데이터가 비어 있습니다. 장중/휴장일/종목코드를 확인하세요.", name, symbol)
        return 0

    observed_at = now.isoformat(timespec="seconds")
    output: list[dict[str, Any]] = []
    for item in rows:
        row = dict(item)
        row["collected_at_kst"] = observed_at
        row["symbol"] = symbol
        row["name"] = name
        # Kiwoom price fields may carry a leading +/- sign to indicate price direction.
        # Preserve the original fields and add absolute-price numeric fields for easier analysis.
        row["close_num"] = to_num(item.get("cur_prc"), absolute=True)
        row["open_num"] = to_num(item.get("open_pric"), absolute=True)
        row["high_num"] = to_num(item.get("high_pric"), absolute=True)
        row["low_num"] = to_num(item.get("low_pric"), absolute=True)
        row["volume_num"] = to_num(item.get("trde_qty"), absolute=True)
        row["acc_volume_num"] = to_num(item.get("acc_trde_qty"), absolute=True)
        output.append(row)

    path = DATA_DIR / f"prices_{now:%Y%m%d}.csv"
    count = write_snapshot_csv(path, output, key_columns=["symbol", "cntr_tm"])
    logging.info("%s(%s): 분봉 %d행 수신", name, symbol, count)
    return count


def collect_flow(client: KiwoomClient, symbol: str, name: str, now: datetime, cfg: dict[str, Any]) -> int:
    body = {
        "mrkt_tp": str(cfg.get("market_type", "000")),
        "amt_qty_tp": str(cfg.get("amount_quantity_type", "1")),
        "trde_tp": str(cfg.get("trade_type", "0")),
        "stk_cd": symbol,
    }
    rows = client.all_pages("ka10064", CHART_PATH, body, "opmr_invsr_trde_chart")
    if not rows:
        logging.warning("%s(%s): 장중 투자자별 매매 차트가 비어 있습니다.", name, symbol)
        return 0

    observed_at = now.isoformat(timespec="seconds")
    output: list[dict[str, Any]] = []
    for item in rows:
        row = dict(item)
        row["collected_at_kst"] = observed_at
        row["symbol"] = symbol
        row["name"] = name
        row["foreign_flow_num"] = to_num(item.get("frgnr_invsr"))
        row["institution_flow_num"] = to_num(item.get("orgn"))
        output.append(row)

    path = DATA_DIR / f"flows_{now:%Y%m%d}.csv"
    count = write_snapshot_csv(path, output)
    logging.info("%s(%s): 장중 수급 차트 %d행 스냅샷 기록", name, symbol, count)
    return count


def in_regular_session(now: datetime) -> bool:
    return now.weekday() < 5 and (now.hour, now.minute) >= (9, 0) and (now.hour, now.minute) <= (15, 35)


def collect_once(client: KiwoomClient, watchlist: list[dict[str, str]], cfg: dict[str, Any], force: bool = False) -> None:
    now = kst_now()
    if not force and not in_regular_session(now):
        logging.info("정규장 수집 시간이 아닙니다. KST 09:00~15:35, 평일에 대기합니다.")
        return
    logging.info("수집 주기 시작: %s", now.isoformat(timespec="seconds"))
    for item in watchlist:
        symbol = str(item["code"]).strip().zfill(6)
        name = str(item.get("name", symbol))
        try:
            collect_price(client, symbol, name, now, cfg)
        except Exception as exc:
            logging.exception("%s(%s) 가격 수집 오류: %s", name, symbol, exc)
        time.sleep(0.2)
        try:
            collect_flow(client, symbol, name, now, cfg)
        except Exception as exc:
            logging.exception("%s(%s) 수급 수집 오류: %s", name, symbol, exc)
        time.sleep(0.2)


def main() -> None:
    parser = argparse.ArgumentParser(description="키움 REST API 장중 수급 및 1분봉 수집기(조회 전용)")
    parser.add_argument("--once", action="store_true", help="장외시간에도 한 번 조회하여 연결 점검")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load_config()
    client = KiwoomClient(cfg["appkey"], cfg["secretkey"], cfg["base_url"])
    client.authenticate()
    if args.once:
        collect_once(client, cfg["watchlist"], cfg, force=True)
        return

    poll_seconds = max(60, int(cfg["poll_seconds"]))
    logging.info("수집 시작: %d초 간격, 종목 %d개", poll_seconds, len(cfg["watchlist"]))
    while True:
        now = kst_now()
        if in_regular_session(now):
            collect_once(client, cfg["watchlist"], cfg)
            time.sleep(poll_seconds)
        else:
            logging.info("정규장 밖입니다. 60초 후 확인합니다.")
            time.sleep(60)


if __name__ == "__main__":
    main()
