# 키움증권 장중 수급·1분봉 자동 수집기

키움증권 **REST API 전용** 조회 프로그램입니다. 주문 API는 포함하지 않습니다.

## 수집 항목

- `ka10080` 주식분봉차트조회: 기본 1분봉 OHLCV
- `ka10064` 장중투자자별매매차트: 종목별 장중 외국인·기관계 등 투자자 매매 추이

공식 자료: https://openapi.kiwoom.com/guide/apiguide
공식 샘플 저장소: https://github.com/Kiwoom-Securities/Kiwoom-REST-API

## 매우 중요한 제한

- 이 폴더는 한국투자증권(KIS) 버전과 호환되지 않습니다. 키움증권 REST API용 App Key/Secret을 발급받아야 합니다.
- 키움증권 **REST API**를 사용합니다. 기존 Windows OCX 기반 `키움 OpenAPI+`와도 별개의 연결 방식입니다.
- 장중 투자자 매매 차트는 API가 제공하는 집계 데이터를 나타냅니다. 각 체결에 실제 외국인/기관 신원이 붙어 있는 원시 체결자료와 동일하지 않습니다.
- `collected_at_kst`는 프로그램이 자료를 조회한 시각입니다. 응답의 `tm`은 API가 반환한 차트 시각입니다. 둘을 혼동하지 마세요.
- 프로그램은 우선 설정 파일에 적힌 3개 종목을 추적합니다. 전 시장 전체 종목 스캔은 아직 포함하지 않았습니다.
- API 사용량/호출 제한은 키움증권 안내를 준수하세요. 처음에는 기본 300초 간격으로 소수 종목만 시험하세요.
- 자동 호출 및 CSV 구조는 정적으로 점검했지만, 사용자의 키가 없어 실제 계정 인증 및 실시간 API 응답까지 검증하지는 않았습니다.

## 1. API 신청

1. https://openapi.kiwoom.com/ 접속 후 로그인합니다.
2. REST API 사용 신청을 완료하고 App Key / Secret Key를 발급받습니다.
3. 운영용과 모의투자용 키는 다를 수 있으므로, 사용할 환경의 키를 사용합니다.
4. App Key/Secret은 채팅, 공개 저장소, 다른 사람에게 공유하지 마세요.

## 2. Windows 설치

Python 3.10 이상을 준비합니다. 이 폴더에서 PowerShell을 열고 실행합니다.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

PowerShell이 가상환경 활성화를 차단하는 경우 아래처럼 실행할 수 있습니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 3. 설정

1. `config.example.json`을 복사해 같은 폴더에 `config.json`으로 저장합니다.
2. `config.json`에서 `appkey`와 `secretkey`를 본인의 키로 바꿉니다.
3. `watchlist`에 추적할 종목코드와 이름을 설정합니다. 종목코드는 6자리 문자열이어야 합니다.

기본값은 5분마다 수집합니다. 처음에는 3개 종목만으로 인증과 응답을 확인하세요.

## 4. 한 번 테스트

```powershell
python collector.py --once
```

주말/휴장일에는 분봉 또는 장중 수급 데이터가 비어 있을 수 있습니다. 데이터가 비었다고 곧바로 인증 오류로 단정하지 말고, 거래일 장중에 재시험하세요.

## 5. 자동 수집 시작

```powershell
python collector.py
```

중단은 `Ctrl+C`입니다. 정규장 시간에는 설정한 간격으로 수집하며, 기본 간격은 300초입니다.

## 6. 출력 파일

`data` 폴더에 일자별 CSV가 생성됩니다.

- `prices_YYYYMMDD.csv`: `ka10080` 분봉 데이터와 `collected_at_kst`, 심볼, 종목명, 분석용 절대가격 열
- `flows_YYYYMMDD.csv`: `ka10064` 장중 투자자별 수급 차트. 반복 조회 결과를 수집 시각과 함께 보존합니다.

수급 스냅샷은 반복 저장되므로 `tm`뿐 아니라 `collected_at_kst`도 기준으로 비교해야 합니다. 같은 차트 시각의 과거 값이 뒤늦게 수정되는 경우를 살펴볼 때도 두 시각이 모두 필요합니다.

## 7. 가설 검증 시 권장 절차

1. 수급의 `tm`과 가격의 `cntr_tm`을 시간축에 맞춥니다.
2. 신호는 사전에 정한 외국인/기관 순매수 기준으로 정의합니다.
3. 신호 시각 이후 5분·15분·30분 수익률을 계산합니다.
4. 비슷한 직전 수익률·거래대금의 무신호 종목과 비교합니다.
5. 여러 거래일의 시계열을 모으고, 거래비용 및 시장지수 효과를 고려합니다.

주의: API 수급값의 단위/집계 방법을 키움 API 가이드의 해당 TR에서 먼저 확인하고, 단위를 변환하기 전에는 CSV 원본값을 보존하세요.
