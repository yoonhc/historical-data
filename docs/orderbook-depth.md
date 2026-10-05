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
뒤처진 경우에도 소켓을 유지해 미복원 증분 원문·표준 journal을 계속 저장한다. 새 기준
스냅샷을 재시도하며 `lagged_snapshots`와 `unreconstructed_messages`를 기록한다.
이 상태는 유효한 호가 복원으로 취급하지 않는다. 새 수집에 기록된
`lagged_snapshot_policy=retain_unsynchronized_updates_v1`일 때만 재생기가 해당 실패 epoch의
후속 증분을 미복원 상태로 읽는다. 이전 수집의 오류·엄격한 재생 규칙은 유지한다.

`lagged_retry_policy=same_socket_backoff_v1`인 새 수집은 실패를 감지한 뒤 최초 **1초**,
연속 실패 시 **2·4·8·10초**, 이후 최대 10초 간격으로 같은 소켓의 anchor를 재시도한다.
시간 계산에는 단조 시계를 사용하며 대기 중에도 증분 수신·저장을 계속한다. 새 프레임이
도착하지 않는 동안에도 수신 timeout을 거쳐 재시도 시각을 확인한다. 실제 요청은 flush·REST
처리 등으로 늦어질 수 있으므로 1초 안에 복구된다는 보장은 아니다.

추가 재시도는 **한 수집 세션 안의 최근 60초에 최대 8회**다. 정상 동기화나 새 소켓에서 연속
실패 횟수는 초기화하지만 이 요청 예산은 유지한다. 요청 상한에 도달하면 다음 허용 시각까지
원문을 계속 보존한다. 일반 anchor·정기 검증·범위 소진 요청은 이 추가 재시도 계수와 별개이며,
기존 Fetcher의 요청 예산과 REST 429/418 cooldown도 그대로 적용한다.

재시도 직전에 미확정 증분을 저장하고, 그 저장 이후에도 세션 종료·계획 회전 시각을 다시
확인한다. 이미 종료 시각이 됐으면 새 retry epoch나 요청을 시작하지 않는다. 이미 시작한
네트워크 요청의 완료 시각까지 제한하는 기능은 아니다. 오류·중단 시 기존 flush와 실패 기록을
유지하며, 성공해도 이전 실패 epoch·미복원 계수·`partial` 판정을 지우지 않는다.

보고서의 `lagged_retry_delays_seconds`, `lagged_retry_budget`, `lagged_retry_requests`에 정책과
시도 횟수를 남긴다. 실패 epoch의 `lagged_retry`에는 선택한 대기·예산 대기·벽시계 기준 예정
시각·실제 요청 시각·단조 시계로 측정한 대기를 기록한다. 새 epoch의 `retry_of_epoch`로 이전
실패를 연결한다. 이 필드들은 요청 일정의 증거이고 호가 재생 의미를 바꾸지 않는다. 해당 필드가
없는 과거 자료도 기존 journal과 정책으로 재생하며, 과거 10초 대기 기록을 고쳐 쓰지 않는다.

`history_policy=bounded_same_socket_v1`인 새 수집은 같은 소켓에서 이미 받은 최근 증분을
보존한다. 한도는 이벤트 128개, 가격·수량 쌍 50,000개, 정규화한 이벤트의 직렬화 크기 8 MiB다.
어느 한도든 넘으면 오래된 이벤트부터 버린다. 이 바이트 수는 Python 프로세스 전체의
메모리 사용량이 아니며, 원문·표준 파일의 영구 보존 정책과도 별개다.

범위 소진 후 같은 소켓의 새 anchor가 뒤처져 있으면 이 버퍼로 엄격한 ID 연결을 시도한다.
소진을 유발한 이벤트와 이전 지연 epoch에서 미복원 상태로 저장한 이벤트도 사용할 수 있다.
실패한 후보는 버리고 원래 anchor의 동기화 절차를 유지한다. 성공해도 이전 epoch의 오류,
미복원 건수와 `partial` 판정을 소급 변경하지 않으며, 과거 이벤트를 새 epoch의 수신·적용
건수에 중복 합산하지 않는다. 기준 스냅샷 정렬에 쓴 수량은 별도 `anchor_history`에 남긴다.

새 연결·계획 회전·단절 후 재연결에서는 이 버퍼를 비운다. 서로 다른 소켓의 이벤트를
이어 붙이지 않으며, 컬렉션 단위 재생도 기록된 epoch 전환 사유에 따라 동일하게 처리한다.

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

위 history 정책이 있는 수집에서는 뒤처진 검증 스냅샷도 최근 버퍼로 현재 ID까지 맞춰 본다.
양쪽 상위 N호가가 충분하고 가격·수량이 정확히 일치할 때만 기존 갱신 정책에 따라 채택한다.
연결 누락·버퍼 한도·불완전한 상위 호가 등으로 대조할 수 없으면 채택하지 않는다. 같은 ID의
값 불일치는 기존과 같이 동기화 실패다. 시도별 버퍼 크기·적용한 sequence·결과를
`checks[].history_alignment`에 기록한다. 정렬은 실제 수신 순서의 과거 이벤트만 사용한다.

긴 수집에서는 가격이 초기 범위를 벗어날 수 있다. 새 수집의 `refresh_from_checks=true` 정책은
대조가 성공한 스냅샷을 같은 업데이트 ID에서 새 기준으로 이어받는다. 연결과 `pu` 연속성을
유지하면서 알려진 가격 범위를 갱신한다. 남은 호가가 적어지면 정기 검증 시각 전에도 요청하며,
`coverage_refresh.policy=bounded_headroom_v1`인 수집은 다음 기준을 사용한다.

- 어느 한쪽의 알려진 호가 개수가 기준 snapshot 개수의 75% 미만이거나, 최우선 가격부터
  알려진 경계까지의 가격 여유가 snapshot의 가격 폭의 25% 미만이면 조기 검증을 시도한다.
  호가 개수 기준은 정수로 내림한다. 가격 폭이 0인 snapshot에는 가격 비율 조건을 적용하지 않는다.
- 직전 snapshot 처리 완료 후 최소 1초를 기다리며, 조기 요청은 한 수집의 최근 60초에 최대
  12회다. 같은 소켓의 epoch 변경과 새 연결에서도 이 예산을 유지한다. 예산 대기 때문에
  수신 루프를 재우지 않으며, 기존 정기 검증은 계속 가능하다. 프로세스가 바뀌면 예산은 새로 시작한다.
- 정렬 중인 REST 후보가 있으면 중복 검증을 요청하지 않는다. 요청 전 flush 이후에도
  수집 종료·연결 회전 시각과 요청 가능 여부를 다시 확인한다.

이 상한은 **추가 조기 검증**에만 적용한다. 정기 검증·새 연결의 anchor·품질 저하 후 재동기화
anchor와 별개이며 IP 전체의 통합 상한이 아니다. 기존 Fetcher의 예산 및 저장소별 REST
429/418 cooldown은 유지한다. 최대 1,000호가 조회의 요청 가중치는 20이다.
[공식 REST 요청 가중치](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data#order-book)

양쪽 상위 N호가가 남아 있어야 기존과 동일하게 ID·가격·수량 대조 후 snapshot을 채택한다.
급격한 변화로 그 전에 상위 N호가가 부족해지면 `incomplete_top`과 부족 상태를 기록하고,
같은 소켓의 `same_socket_after_incomplete_top` epoch에서 새 anchor를 받는다. 이전 epoch의
ID 동기화 여부와 `top_complete=false`를 그대로 보존하고 `partial`로 남긴다. 부족한 호가를
완전한 것으로 취급하거나 검증을 건너뛰어 갱신하지 않는다. 이 재동기화와 범위 소진은 기존
10회 종료 한도를 공유한다. 해당 전환은 같은 소켓의 제한된 최근 버퍼만 이어받는다.

`coverage_check_requests`, `periodic_check_requests`는 시도 계수이고
`recent_coverage_checks`에는 최근 32개 시도의 사유·시각·호가 및 가격 여유를 기록한다.
실제 HTTP 요청·수신 시각은 원문 수집 이력으로 확인한다. `coverage_top_shortfalls`는 상위
호가 부족 횟수다. `refreshed`와 `refreshes`는 실제 대조에 성공해 갱신한 결과다.
빠른 변화·응답 지연·추가 요청 예산 소진에 따른 실패를 모두 방지한다고 보장하지 않는다.

`refresh_decisions.policy=coverage_decisions_v1`인 수집은 조회 필요가 생겼지만 요청할 수 없는
사유를 직접 기록한다. **요청 예산·최소 간격·호가 여유 기준은 위 정책 그대로**이며 긴급용
예산을 예약하는 기능은 운영에 포함하지 않는다.

| 보류 사유 | 의미 |
|---|---|
| `minimum_interval` | 직전 snapshot 처리 완료 후 1초가 지나지 않음 |
| `extra_budget` | 최근 60초의 추가 조기 요청 12회를 사용함 |
| `pending_verification` | 이전 REST 후보의 동일 ID 대조를 기다리는 중 |
| `not_synchronized` | 현재 호가장의 ID 동기화가 성립하지 않음 |
| `deadline` | 수집 종료 또는 계획된 연결 교체 시각에 도달함 |

조회 필요가 없는 평가는 `evaluations`에만 포함한다. `deferred_evaluations`와
`primary_reason_counts`는 보류 평가를 한 번씩 세며, 조건이 겹치면 `blocker_counts`에는
모든 사유를 센다. flush 전후 재평가도 최종 판단 한 번으로 집계한다. 이 수치는 HTTP 요청
수나 서로 다른 장애 횟수가 아니다. 지연 anchor 재시도 중 미재구성 증분을 보존하는 경로는
별도 재시도 정책을 사용하므로 모든 수신 이벤트가 이 평가를 거치는 것도 아니다.

보고서에는 사유별 최초·마지막 표본과 최근 보류 8개를 유지한다. 실제 REST 요청의
`refresh_context`에는 요청 순번·사유·당시 여유·예산 전후 상태를 성공 또는 `SourceError`
원문 수집 이력에 보존한다. 범위 소진·top 부족 시 종료 epoch의 `prior_refresh_decisions`에
당시 집계의 독립 사본을 남긴다. 기록 크기는 표본 수로 제한하며 모든 보류 평가를 개별 파일로
저장하지 않는다. 갑작스러운 프로세스 종료 시 마지막 보고서 저장 이후의 메모리 집계는
남지 않을 수 있다. 이 기록은 원문·표준 데이터나 과거 재생 판정을 변경하지 않는다.

`coverage_refresh` 필드가 없는 종전 조기 갱신은 최소 5초 간격과 각 방향의
`min(max(500, top × 10), 스냅샷 호가 수 / 2)` 미만 기준이었다. 과거 수집 기록을
새 일정으로 바꾸거나 존재하지 않았던 REST 응답을 재생에 추가하지 않는다.

이 정책 필드가 없는 기존 수집은 당시의 고정 범위 규칙으로 재생한다. 과거 자료의 해석을
자동으로 새 규칙으로 바꾸지 않는다. 재생과 원문 검사는 파일 단위로 읽어 전체 메시지를
메모리에 쌓지 않는다. [동시 수집 안정성 검증](stream-soak.md)

`history_policy`가 없는 자료에는 최근 이벤트 정렬을 적용하지 않는다. 새 정책은
`history_limits`와 함께 버전을 기록하며 알 수 없는 정책·다른 한도는 재생 시 거부한다.
이전 자료에 대한 별도 지연 스냅샷 사후 분석은 계속 실시간 기록과 구분한다. 새 정책의
원문 검사에서는 생산 재생기가 이미 시도한 한도를 넘어 추가 정렬한 결과로 판정을 바꾸지 않는다.

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
한 시간 세션 한도 안에서 미복원 증분 보존과 위 간격·요청 상한에 따른 재시도를 계속한다.

CLI 종료 코드는 정상 수집·재생 0, 불완전한 수집·재생 또는 실행 중이 아닌 상태 2,
실행 오류 1, Ctrl-C 130이다. `depth-status`는 **마지막 저장 보고서 기준으로** 실행 중이고
최근 수신·동기화가 정상이며 누적 동기화 손실이 없을 때 `healthy=true`다. 실패를 감지한
뒤 범위 소진·top 부족의 실패 상태를 DB에 먼저 커밋하고, 그 뒤 대량 이벤트를 저장한다.
선행 공개는 마지막 상세 보고서의 저장 범위를 유지하며 실패 입력은 진단 정보로 구분한다.
일반 flush의 종료 재시도까지 실패한 경우도 마지막 DB 보고서를 유지한다. DB 기록 자체의
대기·실패까지 즉시 공개를 보장하지는 않으므로 현재 시점의 top 호가 유효성을 즉시
보증하는 신호로 사용하지 않는다.
과거 자료 검증은 `depth-replay`로 확인하며, `valid`와 REST 대조의 `verification_complete`는
별도 판정이다.

실제 표본의 결과와 저장량은 [검증 기록](../reports/baseline/depth-2026-09-22/summary.md)에 남긴다.
초기 동기화 개선과 연결 교체 시험은
[2026-09-25 기록](../reports/baseline/depth-bootstrap-2026-09-25/summary.md)을 참고한다.
최근 이벤트 정렬과 같은 소켓 복구의 검증은
[2026-09-26 기록](../reports/baseline/depth-history-2026-09-26/summary.md)에 남긴다.
초기 스냅샷 지연 2건과 급격한 범위 소진 2건의 실제 원문 분석은
[2026-09-29 후속 기록](../reports/baseline/depth-followup-2026-09-29/summary.md)을 참고한다.
해당 분석에서는 기존 버퍼 복구의 동작을 확인하고, 최초 10초 재시도 대기와 선제 갱신의
5초 제한을 후속 개선 대상으로 정리했다. 분석 당시 운영 정책이나 과거 품질 판정은 변경하지 않았다.
이후 초기 재시도를 보강한 구현·시험·반영 상태는
[재시도 보강 기록](../reports/baseline/depth-retry-2026-09-29/summary.md)에 별도로 남긴다.
이후 선제 갱신의 5초 제한·상위 호가 부족 처리를 보강한 코드와 3분 실시간 검증,
운영 반영 및 아직 남은 24시간 관찰은
[2026-10-01 착수 기록](../reports/baseline/depth-refresh-2026-10-01/summary.md)에 구분한다.
조기 갱신의 첫 24시간 운영 결과와 남은 범위 소진·미재구성은
[2026-10-03 운영 점검](../reports/baseline/depth-observation-2026-10-03/summary.md)에 남겼다.
조회 예산·간격을 유지하면서 보류 사유를 직접 기록한 구현과 실제 적용 상태는
[보류 기록 적용 결과](../reports/baseline/depth-decisions-2026-10-03/summary.md)에서 확인한다.
그 적용 뒤 첫 24시간과 10/5 아침까지 45개 종료 세션을 확인한
[2026-10-05 보류·실패 관계 점검](../reports/baseline/depth-decisions-observation-2026-10-05/summary.md)에서는
보류 평가 105회 중 보존된 문맥 67개를 검산했다. 두 범위 손실은 추가 예산 소진이 없었고,
하나는 사전 보류 없이, 다른 하나는 pending 해소·새 범위 갱신 뒤의 다음 증분에서 발생했다.
원래 partial 2개·검증 대기 1개와 연결 공백은 유지한다. 이 관찰만으로 다른 요청 정책의 예방
효과를 확정하지 않으며, 운영 요청 예산·간격은 변경하지 않았다.

이후 두 실패 연결만 독립 계산한
[범위 채택과 상태 전달 검증](../reports/baseline/depth-independent-2026-10-05/summary.md)에서는
81회 채택의 공통 범위 가격·수량이 모두 일치했다. 마지막 범위 교체를 생략해도 첫 사건의
매수는 0개, 두 번째 사건의 매도도 0개가 된다. 두 번째 사건의 실제 새 범위에서는 매도
8개가 남았다. 별도 격리 시험은 실패 보고서 저장 전 `healthy` 공개 지연을 재현했으며,
실패 상태의 선행 공개를 후속 후보로 남겼다. 운영 코드·요청 정책은 유지했다.

그 후 [실패 상태 선행 공개 후보](../reports/baseline/depth-status-candidate-2026-10-05/summary.md)를
운영 소스와 분리해 구현했다. 대량 이벤트 저장을 멈춘 동안에도 새 조회 연결·CLI에서
실패가 보이고, 미저장 이벤트의 건수·ID는 저장 완료로 표시하지 않는 것을 확인했다.
DB·JSON·원문 저장 오류와 강제 종료·재시도를 포함해 호가 관련 163건이 통과했다.
이 검증 당시에는 후보 패치만 보관했고 운영에는 적용하지 않았다.
선행 DB 기록 자체가 실패하는 경우까지 즉시 실패 공개를 보장하는 변경은 아니다.

이어 [일반 저장 오류 검증과 배포본 고정](../reports/baseline/depth-status-release-2026-10-05/summary.md)에서
호가 부족 없는 flush 지속 실패의 메모리 ID·epoch 혼재를 재현하고 후보에서 보완했다.
마지막 DB 보고서를 유지하며 미저장 수신 관측은 별도 진단 필드에 남긴다. 관련 167건을
고정한 소스·입력의 깨끗한 경로에서도 재현했고 역패치도 확인했다. 당시에는 운영 미적용 상태였다.

운영 상태 조회는 `depth-status`/DB를 우선 기준으로 한다. `depth-latest.json`은 DB 커밋
뒤에 기록하는 보조 사본이라 쓰기 실패 시 오래된 값일 수 있다. 적용한 버전은 JSON
오류 한 번도 해당 수집의 최종 `failed`로 남기며, 종료 재시도 성공으로 이를 정상화하지 않는다.
설치된 서비스의 기존 15초 재기동 정책은 유지한다. 실제 적용과 새 프로세스·첫 정기 종료
확인 항목은 [배포 절차](../reports/baseline/depth-status-release-2026-10-05/deployment-plan.md)에 구분했다.

사용자 승인 후 10/5 14:01에
[운영 반영](../reports/baseline/depth-status-deploy-2026-10-05/summary.md)을 완료했다.
호가 서비스만 SIGINT 종료·새 기동했고 적용을 위한 `interrupted` 경계는 보존했다.
초기 수신·저장 1,350개와 REST 대조 13회 일치, CLI 정상 및 PID 유지를 확인했다.
첫 정기 종료와 다음 수집 기동은 15:05 이후의 후속 확인으로 남긴다.
