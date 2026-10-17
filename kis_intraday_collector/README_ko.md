# 한국투자증권 장중 수급·1분봉 수집기

이 프로그램은 한국투자증권 Open API의 **조회 API만** 호출합니다. 주문·매매 API는 없습니다.

## 중요 제한

- `주식일별분봉조회`는 종목별 1분봉을 조회합니다. 공식 예제 설명상 1회 최대 120건, 서버 보관 범위 내 과거 분봉 조회가 가능하며 보관 기간은 최대 약 1년입니다.
- `종목별 외인기관 추정가집계`는 장중 수급 추정치입니다. 외국인·기관 수치는 거래소의 확정된 개별 체결 주체 기록이 아니며, 하루에 몇 차례 갱신되는 추정 자료입니다. 프로그램은 매 조회 시각을 함께 저장합니다.
- 이미 지난 날짜의 1분봉은 조회할 수 있어도, 그 날짜의 과거 장중 수급 추정치 스냅샷을 이 API로 되살릴 수 있다고 보장할 수 없습니다. 순서 검증을 하려면 수급 스냅샷을 지금부터 저장해야 합니다.
- 첫 단계는 설정 파일의 세 종목을 기록합니다. 전체 시장 스캔은 별도 단계로 구현하세요. 처음부터 모든 종목을 고정 주기로 호출하지 마세요.

## 1. 준비

1. Python 3.10 이상을 설치합니다. Windows에서는 설치 중 `Add Python to PATH`를 선택하는 것이 편합니다.
2. 한국투자증권에서 Open API 서비스를 신청하고 App Key / App Secret을 발급받습니다: https://apiportal.koreainvestment.com/
3. 이 폴더에서 `config.example.json`을 복사해 이름을 `config.json`으로 바꿉니다.
4. `config.json` 안의 `appkey`, `appsecret`에 본인 키를 입력합니다. 이 파일이나 키 값을 공개 저장소에 올리거나 다른 사람에게 보내지 마세요.

## 2. Windows PowerShell에서 설치

폴더를 압축 해제한 뒤 PowerShell에서 해당 폴더로 이동하고 아래를 실행합니다.

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

PowerShell이 스크립트 실행을 막으면, 가상환경 활성화를 생략하고 아래처럼 직접 실행해도 됩니다.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe collector.py --once
```

## 3. API 연결 점검

```powershell
python collector.py --once
```

`--once`는 장외시간에도 한 번 조회합니다. 주말/휴일에는 분봉이 비거나 오래된 데이터가 나올 수 있으므로, 최종 수집 검증은 거래일 장중에 해주세요.

## 4. 장중 자동 수집 시작

```powershell
python collector.py
```

기본 설정은 300초(5분)마다 각 감시 종목의 1분봉과 수급 추정치를 조회합니다. 정규장 외 시간에는 호출하지 않고 대기합니다. 종료는 `Ctrl+C`입니다.

원하는 종목은 `config.json`의 `watchlist`에 추가합니다. 종목코드는 6자리 문자열로 입력하세요.

## 5. 생성 파일

`data` 폴더가 자동 생성됩니다.

- `prices_YYYYMMDD.csv`: 분봉 OHLC, 체결량 등 API 반환 필드 + `symbol`, `name`, `collected_at_kst`
- `flows_YYYYMMDD.csv`: 외국인/기관 추정 수급 API 반환 필드 + `symbol`, `name`, `collected_at_kst`

`prices` 파일은 종목·거래일·분봉 시각 기준으로 중복을 제거합니다. `flows` 파일은 값이 반복되는 경우에도 수집 시각을 보존합니다. 따라서 수급 값이 언제 처음 바뀌어 관측됐는지 대략 파악할 수 있지만, 실제 매매가 발생한 정확한 시각은 아닙니다.

## 6. 데이터 분석 전 주의

- 신호는 `collected_at_kst`가 아니라 API 자료의 집계 구분/갱신 상태까지 확인하고 정의하세요.
- 수급 데이터가 10:00에 처음 관측되었더라도 실제로 10:00에 매수가 시작되었다는 뜻은 아닙니다.
- 1분봉의 API 체결 시각(`stck_cntg_hour`)과 수급의 관측 시각을 분리해 보존하세요.
- 가격 데이터만으로는 과거 장중 외국인·기관 수급 순서를 복원할 수 없습니다.
- API 호출 제한과 이용약관을 지키고, 처음엔 소수 종목으로 확인하세요.
