# BTCUSDT 선물 aggTrade 수집·검증

BTCUSDT USD-M 선물의 짧은 REST 과거 구간과 실시간 WebSocket 수집을 구현했다.
이번 단계는 소규모 수집·검증이며, 현물·일별/월별 aggTrades 아카이브·장기 백필과 호가
depth 수집은 포함하지 않는다. depth는 이후 별도로 추가한 [호가 수집기](orderbook-depth.md)를
사용한다. 기존 캔들·OI 수집과 Freqtrade 출력은 그대로 사용한다.

공식 REST는 최근 48시간, 시간 조회 한 번은 1시간 미만, 최대 1,000행이다. 이 구현은
여유를 두어 최근 47시간, 한 명령의 시간 범위를 최대 59분으로 제한한다. 첫 페이지는
`startTime/endTime`, 이후는 `fromId`만 사용한다. 같은 밀리초에 1,000건 이상 있어도
다음 ID부터 이어서 조회하며, 내부 종료 경계는 `[start, end)`를 유지한다.
[공식 REST 규칙](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#compressedaggregate-trades-list)

```bash
# 2026-09-22에 검증한 10분 구간. 이후 실행 시 최근 제공 범위로 날짜를 바꾼다.
.venv/bin/marketdata aggtrade-rest \
  --start 2026-09-21T23:00 --end 2026-09-21T23:10 \
  --report reports/aggtrade-rest.json

# 3분 표본; 60초마다 연결을 교체해 누락 감지와 REST 복구도 확인한다.
.venv/bin/marketdata aggtrade-stream --seconds 180 --rotate-seconds 60 \
  --report reports/aggtrade-stream.json

# 현재/마지막 WebSocket 수집 상태
.venv/bin/marketdata aggtrade-status
```

시간 범위를 지정하는 REST는 기본 100페이지까지 요청한다. `--max-pages`는 1~1,000이며,
예산이 끝나면 `partial`로 기록한다. 빈 응답·짧은 마지막 페이지는 원천 조회 종료 근거이지
시장에 있어야 할 모든 체결의 존재를 증명하지 않는다. 중간 ID 공백은 별도로 기록한다.
명시적인 ID 구간 복구는 `--first-id ID --last-id ID`를 사용하며 두 끝을 모두 포함한다.

수신 원문, HTTP 헤더·요청 시작·수신 시각과 수집 실행 ID를 보존한다. 잘못된 JSON·필드·
순서도 원본을 남기고 실패로 처리한다. REST 429는 최소 5분, 418은 최소 1시간, 기타 원천
오류는 최소 30초 대기하며 더 긴 Retry-After를 따른다. 대기 종료 시각을 로컬 state에 남겨
같은 저장소의 다음 명령에도 적용한다. 한 요청 안에서 숨겨진 재시도는 하지 않는다.

실시간 주소는 `wss://fstream.binance.com/market/ws/btcusdt@aggTrade`다. 5초 또는
5,000메시지마다 작은 불변 파일을 확정해 하루 전체 파일을 반복해서 쓰지 않는다. 기본 연결
교체 주기는 1시간이고 `--rotate-seconds`로 바꿀 수 있다. 네트워크 연결·REST 요청 중에는
기존 저장소의 writer 잠금을 잡지 않으며, 쓰기 작업에만 사용한다.
[공식 WebSocket 경로](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/Connect)

연결 교체·재접속·프로세스 재시작 후에는 마지막으로 저장한 WebSocket ID와 다음 ID 사이를
확인한다. 공백이 있으면 REST로 최대 20페이지까지 보충하고 성공 여부를 기록한다. 원래
WebSocket 파일의 공백은 남겨 두며 복구 자료는 REST 출처로 보존한다. 실패한 복구와 순서
역전은 정상 결과로 숨기지 않는다. 강제 종료로 마지막 미확정 버퍼가 사라진 경우에도 같은
방식으로 복구를 시도하지만, REST 제공 범위를 넘겼다면 복구를 보장하지 않는다.

`--seconds 0`은 수동 연속 실행 옵션이다. 이번 검증에서는 180초 표본을 종료했으며 상시
systemd 서비스를 추가·활성화하지 않았다. 기존 네 타이머와 별도로 실행된다. 연속 실행은
가용 공간이 기본 10 GiB 미만이면 중단하고, 연속 다섯 번 연결 실패 시에도 종료한다.
`--min-free-gib`로 공간 기준을 바꿀 수 있다. 서버의 WebSocket 418/429는 자동 재연결을
멈추므로 Retry-After를 확인한 뒤 재시작한다. Ctrl-C는 받은 버퍼를 저장하고 중단 이력을 남긴다.

저장 형식과 의미는 다음과 같다.

| 위치·필드 | 내용 |
|---|---|
| `data/raw/` | REST 응답 원문, WebSocket 원문 문자열과 수신 시각 배열의 gzip |
| `data/canonical/agg_trades/{rest,websocket}/` | 출처별 불변 Parquet 세그먼트 |
| `event_collections` | 실행 종류·상태·조회 범위·페이지 수·복구 결과 |
| `event_segments` | 파일 해시·원본 ID·수집 ID·체결 ID/시간 범위·행 수 |
| `agg_trade_id` | 집계 체결 식별자; 시각이 같아도 다른 ID는 별도 체결 |
| `timestamp` / `event_time` / `received_at` | 체결 시각 T / 거래소 이벤트 시각 E / 프로그램 recv 반환 후 시각 |
| `price`, `quantity`, `normal_quantity` | Decimal 값; `nq`가 없으면 null 유지 |
| `first_trade_id`, `last_trade_id`, `buyer_is_maker` | 원천 필드 f/l/m을 보존 |
| `source_id`, `source_row` | 원본 해시와 원본 안의 행/메시지 위치 |

`received_at`에는 클라이언트 내부 큐·처리 지연이 포함될 수 있으며 실제 네트워크 도착
시각이나 거래소의 최초 공개 시각을 증명하지 않는다. WebSocket gzip의 압축 전 바이트
수와 SHA-256, 연결 번호는 fetch metadata에 기록한다. 동일 메시지를 다시 받아도 원문은
보존하고, 조회할 때 ID 기준으로 중복을 제거한다. 같은 ID의 가격·수량·시각 등이 다르면
충돌로 보고하며 자동으로 한쪽을 정상값으로 채택하지 않는다.

선물 aggTrade는 같은 가격·주도 방향의 시장 체결을 집계하며 보험기금·ADL 체결은 제외한다.
`q`에는 RPI 체결이 포함되고, 제공되는 `nq`는 RPI가 제외된 수량이다. 집계 이벤트 개수나
첫/마지막 체결 ID의 차이를 캔들의 개별 체결 횟수와 같다고 가정하지 않는다.
[공식 aggTrade 필드·범위](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market#aggregate-trade-streams)

저장 자료를 검사하는 명령은 다음과 같다.

```bash
# 출처를 생략하면 REST 복구분과 WebSocket을 함께 검사한다.
.venv/bin/marketdata aggtrade-audit \
  --start 2026-09-21T23:00 --end 2026-09-21T23:10

# 기존 표준 선물 1분봉과 OHLC·거래량·거래대금·taker 매수량을 정확히 대조한다.
.venv/bin/marketdata aggtrade-compare-candles \
  --start 2026-09-21T23:00 --end 2026-09-21T23:10 \
  --report reports/aggtrade-candle-parity.json

# 양쪽을 수집한 동일 시간 구간의 ID·값을 대조한다.
.venv/bin/marketdata aggtrade-compare-sources \
  --start 2026-09-22T13:44:26.984 --end 2026-09-22T13:47:26.638 \
  --report reports/aggtrade-source-parity.json
```

파일은 읽기 전에 해시를 검사한다. 현재 조회는 최대 1시간, 입력 최대 1,000,000행으로
제한한다. `aggtrade-audit`의 `valid`는 관측된 첫 ID와 마지막 ID 사이의 연속성·충돌·시간
역전을 검사한 결과다. 요청 시간의 양 끝까지 모든 체결을 확보했다는 보장은 아니다.
각 보고서에 읽은 세그먼트 ID를 남긴다. 기존 `Store.read(Key(...))`와 고정 스냅샷 API에는
아직 이벤트 세그먼트를 통합하지 않았으며 Python에서는 `marketdata.aggtrades.read_trades`를
사용한다. Freqtrade용 체결/Orderflow 어댑터도 후속 범위다.

`compare-sources`는 공통 ID의 값 비교, 한쪽에만 있는 ID, 원래 WebSocket 공백과 합친
자료의 상태를 구분한다. 정상 복구분이 REST에만 있다면 전체 출처 집합은 다르므로
`equal=false`와 종료 코드 2를 반환할 수 있다. 값 불일치로 바꾸어 해석하지 않는다.
캔들 비교도 차이가 있으면 보고하고 종료 코드 2를 반환하며 원천을 수정하지 않는다.

정상 수집·비교는 종료 코드 0, 불완전한 수집·감사 또는 비교 차이는 2, 실행 오류는 1이다.
`aggtrade-status`는 실행 중이며 최근 30초 내 수신이 있고 미복구·순서 오류가 없을 때만
`healthy=true`다. 종료된 표본은 데이터가 정상이더라도 실행 중인 서비스로 표시하지 않는다.
과거 수집의 오류 기록은 별도로 표시한다. 기존 `marketdata status`는 캔들·OI 등의 운영
범위를 확인하므로 aggTrade 상태는 이 전용 명령으로 함께 확인한다.

이벤트 파일은 기존 `validate`와 백업·복원 명령에도 포함했다. 이전 버전의 백업 검증도
지원한다. [실데이터 검증 보고서](../reports/baseline/aggtrade-2026-09-22/summary.md)에
실제 구간·행 수·누락 복구·값 대조와 저장량을 기록한다. 짧은 표본의 일치를 모든 시장
상황이나 장기 무중단 수집의 검증으로 일반화하지 않는다.
