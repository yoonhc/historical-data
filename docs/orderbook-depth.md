# BTCUSDT 선물 orderbook depth 수집·재생

BTCUSDT USD-M 선물의 공개 100ms 증분 스트림과 REST 최대 1,000호가 스냅샷을 수집한다.
수집한 원문과 표준 Parquet으로 호가장을 재생하며, Freqtrade에 의존하지 않는다.
수동 명령은 최대 1시간 표본을 수집한다. [상시 서비스](stream-services.md)는 이를 반복 실행한다.

```bash
# 3분 수집. 연결을 두 번 교체하고 20초마다 독립 REST 스냅샷으로 상위 20호가를 대조한다.
.venv/bin/marketdata depth-stream --seconds 180 --rotate-seconds 60 \
  --verify-seconds 20 --report reports/depth-stream.json

# 마지막 수집의 원문 기반 표준 파일을 재생하고 수집 당시 결과와 비교한다.
.venv/bin/marketdata depth-replay --report reports/depth-replay.json

# 특정 수집을 고정해 재생한다.
.venv/bin/marketdata depth-replay --collection-id COLLECTION_ID

.venv/bin/marketdata depth-status
```

`--seconds`는 1~3,600초다. 기본값은 180초, 연결 교체 3,600초, REST 대조 30초,
대조 호가 수 20개다. 호가 수는 `--top`으로 양쪽 각각 1~1,000개까지 지정한다.
기존 일별·OI 타이머 및 aggTrade와는 별도 잠금을 사용한다. 기본 여유 공간 10 GiB 미만이면
멈추며 `--min-free-gib`로 기준을 조절한다. 원본을 자동 삭제하지 않는다.

## 동기화와 공백

WebSocket 연결 후 실제 첫 증분을 먼저 받고 즉시 REST 스냅샷을 요청한다.
첫 메시지의 수신 대기는 최대 2초이며, 뒤따르는 메시지는 WebSocket의 제한된 큐에 남는다.
이는 연결만 열고 첫 증분이 도착하기 전에 스냅샷을 요청하던 초기 동기화 실패를 줄이기 위한
버퍼다. 거래소의 장기 스냅샷 지연까지 해결하는 것은 아니며 ID 연결 조건은 유지한다.
스냅샷보다 오래된 이벤트는
원본에 남긴 채 적용에서 제외한다. 처음 적용하는 이벤트는 `U <= lastUpdateId <= u`를
만족해야 하며, 이후에는 매 이벤트의 `pu`와 직전 `u`가 같아야 한다. 선물의 업데이트 ID가
매번 정수 1씩 증가한다고 가정하지 않는다. 수량은 증감량이 아닌 해당 가격의 최종 절대량이다.
수량 0은 삭제이며, 없던 호가의 삭제도 정상 처리한다.
[공식 로컬 호가장 규칙](https://developers.binance.com/en/docs/products/derivatives-trading-usds-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly)

초기 버퍼는 REST 요청 전에 `depth_bootstrap` 원문으로 보존한다. REST 오류나 Ctrl-C가
스냅샷 생성 전에 발생해도 이미 받은 원문은 남지만, 호가장이나 완전한 journal을 확보했다고
판정하지 않는다. 정상 진행 시 같은 메시지를 원래 수신 시각으로 기존 증분 journal에도 기록한다.
`bootstrap_policy=buffer_before_anchor_v1`, `bootstrap_timeout_seconds`, `bootstrap_messages`가
새 수집 보고서에 남는다. 이 필드가 없는 이전 journal의 해석·실패 판정은 바꾸지 않는다.
초기 스냅샷보다 앞선 버퍼 구간의 과거 호가장을 소급 복구한 것은 아니다.

연결 교체·단절·프로세스 재시작 때마다 새 REST 스냅샷과 새 `epoch`를 시작한다.
ID 연결 누락, 충돌하는 중복, 역순 업데이트, 교차 호가, 알려진 가격 범위 소진도 기존 epoch를
닫고 재동기화한다. 이전 epoch와 새 epoch 사이의 호가 변화는 복구된 것으로 표시하지 않는다.
REST는 현재 호가장만 제공하므로 aggTrade처럼 과거 누락 이벤트를 다시 조회하지 않는다.
재동기화가 성공해도 이전 누락 이력은 남으며 해당 수집은 `partial`이다.

가격 범위 소진은 소켓을 유지한 채 새 epoch에서 재시도한다. 초기 스냅샷의 ID가 첫 증분보다
뒤처진 경우에도 소켓을 유지해 미복원 증분 원문·표준 journal을 계속 저장한다. 10초 간격으로
새 기준 스냅샷을 요청하며 `lagged_snapshots`와 `unreconstructed_messages`를 기록한다.
이 상태는 유효한 호가 복원으로 취급하지 않는다. 새 수집에 기록된
`lagged_snapshot_policy=retain_unsynchronized_updates_v1`일 때만 재생기가 해당 실패 epoch의
후속 증분을 미복원 상태로 읽는다. 이전 수집의 오류·엄격한 재생 규칙은 유지한다.

`complete`는 정해진 표본 수집을 오류 없이 마쳤다는 의미다. 계획된 연결 교체로 여러 epoch가
있으면 재생 결과는 정상이어도 `continuous=false`다. 단일 epoch의 `continuous=true`도
관측된 이벤트 사이의 연결성만 뜻하며, 요청 시간의 양 끝까지 모두 확보했다는 보장은 아니다.

스냅샷은 양쪽 최대 1,000개 호가만 포함한다. 복원기는 현재 기준 스냅샷의 최저 매수 가격 이상과
최고 매도 가격 이하를 완전히 알려진 가격 범위로 관리한다. 바깥 가격의 증분은 원문·표준 파일에
저장하지만 완전한 호가장으로 노출하지 않는다. 상위 N호가가 이 범위 안에 충분히 있을 때만
`top_complete=true`다. RPI 주문은 이 REST·증분 원천에 포함되지 않는다.
[공식 REST 범위](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#order-book),
[공식 증분 경로·주기·RPI 제외](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/public#diff-book-depth-streams)

## 검증과 저장

수집 중 별도 REST 스냅샷을 주기적으로 가져온다. 이를 버퍼의 증분으로 진행시켜 현재 호가장과
같은 업데이트 ID에 도달했을 때 양쪽 상위 N호가의 가격·수량을 Decimal로 정확히 대조한다.
서로 다른 시각의 호가장을 그대로 비교하지 않는다. 스냅샷이 뒤처지거나 연결 이벤트가 없어
같은 ID로 맞출 수 없으면 `comparable=false`로 남긴다. 종료 시 미완료 대조도 별도로 기록한다.
실제 값이 다르면 동기화를 잃은 것으로 처리한다.

긴 수집에서는 가격이 초기 범위를 벗어날 수 있다. 새 수집의 `refresh_from_checks=true` 정책은
대조가 성공한 스냅샷을 같은 업데이트 ID에서 새 기준으로 이어받는다. 연결과 `pu` 연속성을
유지하면서 알려진 가격 범위를 갱신한다. 남은 호가가 적어지면 정기 검증 시각 전에도 요청하며,
추가 요청 간격은 최소 5초다. 기준은 각 방향에서 `min(max(500, top × 10), 스냅샷 호가 수 / 2)`
미만이다. 결과에 `refreshed`와 `refreshes`를 기록한다. 그 사이 범위를 소진하거나 ID 누락이
발생하면 여전히 새 epoch로 재동기화하고 공백을 남긴다.

이 정책 필드가 없는 기존 수집은 당시의 고정 범위 규칙으로 재생한다. 과거 자료의 해석을
자동으로 새 규칙으로 바꾸지 않는다. 재생과 원문 검사는 파일 단위로 읽어 전체 메시지를
메모리에 쌓지 않는다. [동시 수집 안정성 검증](stream-soak.md)

| 저장 항목 | 내용 |
|---|---|
| REST 원문 | 응답 JSON 바이트, URL·헤더·요청 시작·수신 시각 |
| WebSocket 원문 | 메시지 문자열과 수신 시각·epoch·로컬 sequence 배열의 gzip |
| 초기 수신 원문 | `depth_bootstrap`: 스냅샷 요청 전에 받은 메시지·수신 시각 배열의 gzip |
| `data/canonical/book_depth/` | 스냅샷과 증분을 담은 불변 Parquet 세그먼트 |
| `event_collections` | 수집 상태, 연결·재동기화 이력, epoch별 최종 호가와 검증 결과 |
| `event_segments` | 파일·원문 해시, 수집 ID, 로컬 sequence 범위와 행 수 |
| `kind` | `anchor` 초기 스냅샷 / `update` 증분 / `check` 독립 검증 스냅샷 |
| `sequence` | 수집 실행 안의 처리 순서; 거래소 업데이트 ID와 구분 |
| `epoch` | 하나의 초기 스냅샷에서 시작한 독립 동기화 구간 |
| `first_update_id` / `final_update_id` / `previous_update_id` | 원천의 U / u 또는 lastUpdateId / pu |
| `timestamp` / `event_time` / `received_at` | 거래소 T / E / 프로그램의 수신 함수 반환 후 시각 |
| `bids` / `asks` | 가격·수량의 Decimal 구조체 배열; 삭제 수량 0도 보존 |
| `source_id` / `source_row` | 원문 해시와 원문 안의 위치 |

로컬 `sequence`는 처리 순서다. WebSocket 큐가 REST 응답을 기다리는 동안 받은 이벤트는
거래소 시각이 스냅샷보다 빠를 수 있다. `received_at`도 실제 패킷 도착이나 최초 공개 시각이
아니며 큐 대기·처리 지연을 포함한다. 임의로 시각순 정렬해 재생하지 않는다.

기본 5초, 500개 메시지 또는 메시지 합계 4 MiB 중 먼저 도달하는 조건으로 파일을 확정한다.
REST 요청과 네트워크 수신 중에는
저장소 writer 잠금을 잡지 않는다. Ctrl-C는 받은 버퍼를 저장하고 중단 이력을 남긴다.
강제 종료로 미확정 버퍼가 사라지면 그 구간을 과거 REST로 복구할 수 없다. 재시작은 항상
새 스냅샷이며, 종료 보고서가 없던 이전 수집은 `interrupted`로 기록한다.

`depth-replay`는 파일 해시와 연속된 로컬 sequence를 확인하고, epoch별로 재생한 전체 알려진
호가의 해시·상위 호가·처리 건수·REST 대조 결과를 수집 당시 기록과 비교한다. 조회는 한 수집
실행 단위이며 입력 최대 100,000행으로 제한한다. 기존 `Store.read`나 고정 스냅샷 API,
Freqtrade 호가 어댑터에는 아직 통합하지 않았다. 기존 `validate`·백업·복원은 등록된 모든
depth 세그먼트를 포함한다.

원문과 표준 데이터의 대응은 다음 보조 검사로 확인한다. 가격·수량의 모든 쌍과 거래소 ID,
시각, 수신 시각, 원문 위치를 실제 저장 바이트와 대조한다.

```bash
.venv/bin/python scripts/verify_depth_capture.py --collection-id COLLECTION_ID \
  --report reports/depth-source-parity.json
```

실제 표본에서는 `received_at - timestamp`가 음수인 사례도 관측했다. 두 시계의 오차를
교정·검증한 것은 아니므로 이 차이를 통신 지연으로 사용하거나 시각 값을 임의 보정하지 않는다.

REST 오류 원문과 대기 종료 시각을 보존한다. 429는 최소 5분, 418은 최소 1시간 및 더 긴
Retry-After를 따르며 같은 저장소의 다음 depth 요청에도 적용한다. WebSocket 418/429도
동일한 최소 대기와 더 긴 Retry-After를 파일에 기록하고 종료한다. 다음 프로세스도 대기
중에는 접속하지 않는다. 연결 연속 실패 5회, 가격 범위 소진 10회 또는 일반 동기화 오류를
처리할 때 누적 동기화 실패가 10회 이상이면 종료한다. 초기 스냅샷 지연만 반복되는 경우는
한 시간 세션 한도 안에서 미복원 증분 보존과 10초 간격 재시도를 계속한다.

CLI 종료 코드는 정상 수집·재생 0, 불완전한 수집·재생 또는 실행 중이 아닌 상태 2,
실행 오류 1, Ctrl-C 130이다. `depth-status`는 현재 실행 중이고 최근 수신·동기화가 정상일 때만
`healthy=true`다. 과거 자료 검증은 `depth-replay`로 확인한다.

실제 표본의 결과와 저장량은 [검증 기록](../reports/baseline/depth-2026-09-22/summary.md)에 남긴다.
초기 동기화 개선과 연결 교체 시험은
[2026-09-25 기록](../reports/baseline/depth-bootstrap-2026-09-25/summary.md)을 참고한다.
