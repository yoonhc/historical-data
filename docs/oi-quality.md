# OI의 값·시각·조회 가능 여부 검증

출처 간 값 일치와 HTTP 응답 이력을 검증한다. 관측기와 보고서는 canonical 값을
게시·교체하거나 archive 라벨을 이동하지 않는다. 수량·명목가치 단위는 계속 미검증이다.

## 자동 실행

- `marketdata-oi-probe.timer`: **15초마다** 최근 한 시간의 BTCUSDT 5분 OI를 조회한다.
- `marketdata-oi-quality.timer`: **UTC 03:40 / 한국 시각 12:40**에 최근 완료된 7일의
  archive/REST 비교와 현재까지의 응답 관측 보고서를 생성한다.
- 기존 일별 수집과 5분 REST 수집이 canonical 데이터를 계속 관리한다.

Binance 문서의 제한은 이 통계 API에 최대 500행, IP당 5분 1,000회다. 관측기는 요청당
약 13개 라벨, 5분당 20회 수준이다. 동일 IP의 다른 프로그램 사용량까지 통제하는 것은 아니다.
[공식 OI Statistics 문서](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/market-data)

각 요청의 전송 전 UTC 시각, 수신 시각, monotonic 경과 시간, 요청 범위, 응답 헤더,
원본 SHA-256과 probe ID를 기록한다. 원본 내용은 중복 제거하지만 HTTP 수신 이력은
매번 남긴다. 근거는 `data/raw/`와 SQLite `fetches.metadata.oi_probe`에 있으며,
최신 요청 상태는 `state/oi-probe-latest.json`이다.

HTTP 요청 중에는 canonical writer를 잡지 않는다. 별도 관측기 잠금으로 중복 실행을
막고, 기록 저장 시에만 writer를 얻는다. 잠금 대기 시간은 HTTP 수신 시각에 포함하지 않는다.
저장 실패·중단으로 놓친 관측은 소급 생성하지 않는다.

## 시각 해석

시각 구간은 **우리 클라이언트가 받은 응답의 변화 구간**이다. 거래소 전체의 정확한 최초
공개 시각이나 모든 클라이언트에서 지속적으로 조회 가능했던 시간을 증명하지 않는다.

- 유효한 빈 응답은 해당 요청에서 라벨이 없었다는 근거다.
- HTTP/통신 오류, 중복·0값·형식 오류, 의심스러운 시계 기록은 부재 근거로 쓰지 않는다.
- 마지막 부재 응답의 **요청 시작 시각**부터 첫 존재 응답의 **수신 시각**까지 기록한다.
  부재 응답의 수신 시각을 시작 경계로 쓰면 네트워크 중간 시점을 과도하게 단정하게 된다.
- 처음 조회할 때 이미 있으면 시작 경계는 null이다. 과거 파일의 첫 다운로드 시각을
  당시 공개 시각으로 해석하지 않는다.
- 일반 수집기의 성공 응답도 존재 근거가 된다. 정밀한 요청 시각이 없는 기존 기록으로
  부재 경계를 만들지는 않는다. 이후 정상 수집에서 등장한 값을 계속 미관측으로 남기지도 않는다.
- 등장 후 다시 빠지는 응답은 별도로 표시한다. `subsequent_absence_detected=true`인
  라벨은 단순한 단조 공개 지연 모델로 해석해서는 안 된다.

`lower_delay_seconds`와 `upper_delay_seconds`는 위 관측 경계에서 원천 라벨을 뺀 값이다.
60초 기준은 검토용 운영 기준이며 거래소의 게시 지연 보장이 아니다. 첫 조회에 이미 있는
오래된 표본을 늦은 게시로 분류하지 않는다. 45초를 넘는 요청 사이 공백, 60초를 넘는
마지막 요청의 나이는 별도로 기록한다.

HTTP Date는 거친 시계 점검에만 사용한다. 벽시계와 monotonic 경과 시간이 2초 넘게
어긋나거나 HTTP Date가 요청 구간에서 5초 넘게 벗어나면 시각 근거에서 제외한다.
NTP 검증이나 캐시·서버 시각 문제를 완전히 판별하는 기능은 아니다.

## 초기 요청 경계의 보정

초기 v1은 `endTime`을 최신 라벨의 정각에 맞췄다. 실제 대조에서 일반 수집 응답에는
있는 라벨이 경계에 맞춘 조회에서는 빠지는 사례가 있었다. 경계 처리, 서버별 상태,
캐시 중 어느 원인인지는 확정하지 않았다. 당시 CloudFront 헤더도 Miss였으므로 캐시라고 단정하지 않는다.

**v2는 실제 요청 시각을 종료 범위로 사용해 대상 라벨이 쿼리 내부에 있도록 한다.**
경계 직후 2ms 이내에는 요청하지 않는다. v1 원본은 보존하되 부재/지연 경계 계산에서
제외하고 `boundary_policy_excluded`에 수량을 표시한다. 반환된 정상 값은 존재·수치
비교 근거로 유지한다. 버전이 다른 조회 조건을 구분해 재검산할 수 있다.

## 값 비교와 수정 검출

archive 라벨 기준 -300·0·+300초의 REST 수량·명목가치를 Decimal로 정확히 비교한다.
날짜 경계를 넘는 대응 행도 읽으며, 비교 가능한 수와 정확 일치 수를 따로 기록한다.
누락과 격리는 값 불일치와 분리한다. 격리 전 active 파일이 남아 있어도 정상 비교에 쓰지 않는다.

`--reference-shift 300`은 **예외 목록을 만들 비교 가설**이다. 저장소에 적용하거나 모든
과거 기간에 성립하는 규칙으로 확정하지 않는다. 같은 라벨의 수량·가치가 응답 사이에서
바뀌면 이전·다음 값과 fetch ID·원본 ID를 남긴다. canonical을 자동 교체하지 않는다.

## 사용과 결과

프로젝트 루트에서 실행한다.

```bash
.venv/bin/marketdata oi-probe
.venv/bin/marketdata oi-quality --days 7
.venv/bin/marketdata oi-quality --start 2026-09-15 --end 2026-09-22

systemctl --user list-timers 'marketdata-oi-*'
journalctl --user -u marketdata-oi-probe.service -n 10 --no-pager
```

한 번에 최대 31일을 비교한다. 보고서 생성은 네트워크 요청과 canonical 갱신 없이 실행한다.
`reports/oi-quality/latest.json`은 마지막으로 완성된 보고서를 가리킨다. 각 실행의 보고서는
별도 디렉터리에 보존하고 다른 내용으로 덮어쓰지 않는다.

| 파일 | 내용 |
|---|---|
| `report.json` | 보고서 ID, 기준 시각, 입력 revision·fetch ID, 통계와 검토 여부 |
| `parity.csv` | 날짜·시각 이동별 일치·불일치·누락·격리 수 |
| `availability.csv` | 라벨별 관측 경계, 원본 ID, 후속 부재 여부 |
| `anomalies.jsonl` | 기준 가설의 예외, 요청 오류, 값 변경, 빠짐/재등장, 지연 후보 |

관측 공백은 `report.json`의 `observations.probe_gaps_over_45_seconds`에 있다.
`observation_status`는 v2 관측이 없는 `not_started`, 활성 `active`, 오래된 `stale`을 구분한다.
값 비교 기간과 실제 v2 관측 시작·종료를 별도로 확인한다. 과거의 정밀 관측은 복구할 수 없다.

`oi-quality` 종료 코드 0은 현재 검토 항목이 없음을, 2는 보고서를 생성했지만 검토 항목
또는 관측 부족이 있음을, 1은 생성 오류를 뜻한다. systemd는 2를 정상적인 보고서 생성으로
취급하므로 서비스 Result와 함께 `needs_review`, 관측 범위·수·오류를 확인한다.
외부 메시지 알림은 전송하지 않는다.

한 요청 내부에서 재시도하지 않는다. 429는 최소 5분, 418은 최소 1시간, 기타 오류는
최소 30초 쉬고, 더 긴 Retry-After가 있으면 따른다. cooldown 중에는 요청도 부재 관측도
생성하지 않는다. 중단·재개는 [systemd 안내](../integrations/systemd/README.md)를 따른다.
