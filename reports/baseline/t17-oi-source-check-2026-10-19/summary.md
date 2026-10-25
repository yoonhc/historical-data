# T17 · 공개 CHECKSUM 확인 — 수정 원본 대기

**2026-10-19 21:13:52 KST, 해당 날짜의 공개 CHECKSUM을 한정 조회했다.** HTTP 200으로 받은 97바이트는 10/16 두 실패 시도에서 보존한 CHECKSUM과 정확히 같고, 가리키는 ZIP SHA-256도 같다. **이번 응답에서는 수정된 ZIP 해시가 확인되지 않았다.** 합의한 분기에 따라 ZIP을 다시 다운로드하거나 운영 재처리를 시작하지 않았다.

T17은 **원인 조사·공개 CHECKSUM 대조 완료, 수정 원본 확인과 자료 복구 대기**다. 조회 성공을 자료 완료로 세지 않는다. 기존 `needs_review`·두 실패·격리 issue·대기 refresh를 유지했으며, 운영 코드·정책·원장·자료·서비스에 쓰기 작업을 하지 않았다.

## 실제 응답

| 항목 | 확인값 |
|---|---|
| 대상 | UTC 2026-10-15 BTCUSDT 선물 OI archive 5m |
| 공개 URL | [BTCUSDT-metrics-2026-10-15.zip.CHECKSUM](https://data.binance.vision/data/futures/um/daily/metrics/BTCUSDT/BTCUSDT-metrics-2026-10-15.zip.CHECKSUM) |
| 응답 | HTTP 200 / 97바이트 / 파일명 일치 |
| CHECKSUM 본문 SHA-256 | `e83db5cde9085b39e8545c7c620f4de5044fae82648ad61967e6113c828524e5` — 기존과 동일 |
| 본문이 가리키는 ZIP SHA-256 | `5f1c31bdf645489221d120079529bd311168258ef524a669e88908db22139d90` — 기존과 동일 |
| 응답 Date | `Mon, 19 Oct 2026 12:13:52 GMT` |
| Last-Modified | `Fri, 16 Oct 2026 07:13:04 GMT` |
| ETag | `"3368d84741d9653686c694cc3e9e55c3"` |
| 요청/캐시 정보 | `Cache-Control: no-cache` / `X-Cache: Miss from cloudfront` |
| ZIP 다운로드·운영 재시도 | 모두 0회 |

[응답 본문](attempt-fbaad6f714fe45d3854bd2d3f3342ac1/response.body)과 [시각·전체 헤더·해시·요청 도구 식별](attempt-fbaad6f714fe45d3854bd2d3f3342ac1/result.json)을 보존했다. 시각은 호스트 UTC와 서버 Date 기록이며 T05의 Chrony 시각 구간 증명으로 사용하지 않는다.

첫 샌드박스 시도는 DNS 해석 실패로 HTTP 응답을 얻지 못했고, [원래 실패 기록](attempt-ddf8f48467c449a18152f19195bfc9fa/result.json)을 그대로 남겼다. 네트워크 접근 허용 후 같은 고정 도구로 한 번 조회해 위 응답을 받았다. 애플리케이션의 자동 재시도나 daily attempt 추가가 아니다. 도구는 고정 URL의 GET 한 건, 20초 timeout·4,096바이트 상한·redirect/자동 재시도 없음으로 제한했다.

이 결과는 **이번 CHECKSUM 응답에 대한 관측**이다. ZIP 본문은 새로 읽지 않았으므로 원격 ZIP의 현재 바이트까지 독립 검증한 것으로 확대하지 않는다. 향후 수정 가능성이나 모든 캐시 지점의 상태를 판정하지도 않는다.

## 기존 상태 보존

읽기 전용 SQLite transaction으로 [대상 사후 대조](operating-target-after.json)를 남겼다. [앞선 조사](../t17-oi-archive-investigation-2026-10-19/summary.md)에서 보존한 다음 기록과 같다.

- acquisition `c24159c0b9f2893037ac67c41e52d5bbdfd0a9ed2c3f62ecdae43d6c7b32b6e9`: `needs_review`, validation 실패 2회.
- 원래 attempt `0af32466cb6e44efbee0fe5ba85c5282`·`39f32b6665984af28a7e3b84e73b13da`: 내용 동일, 추가 attempt 없음.
- issue 26: `quarantined_zero_oi`, `blocking=1`, `resolved=0`, 내용 동일.
- 해당 task의 review 기록 추가 없음. 10/16·17·18·19 refresh 4개는 `planned` 유지.

연결별 DB 변경 수는 0이다. 정기 업무가 계속 갱신하는 전체 DB 불변을 주장하지 않는다. T05 timer·반복 권한, T16 실행본·시각 정책, Chrony·수집기 설정을 제어하거나 변경하지 않았다. 이번 결과를 T05의 첫 자연 예약 관찰 완료로 세지 않는다.

## 다음 처리 조건

이번에 확인된 것은 같은 해시이므로, 동일 원문을 재처리하거나 0값을 통과시키도록 품질 조건을 바꿀 근거가 생기지 않았다. **T17은 수정 원본 확인 대기로 남긴다.** 재확인만을 위한 새 자동 polling·예약은 만들지 않았다.

다음 원본 점검에서 CHECKSUM 변경이나 수정 원본의 근거가 확인되면, 해당 ZIP만 별도 경로에 받아 날짜·288개 격자·중복·0값·이전 원문과의 차이를 검사한다. 해시 변경만으로 품질 통과로 인정하지 않는다. 통과한 자료가 있을 때 기존 review 절차의 한 acquisition 복구/재처리 범위를 고정하고, 운영 적용 전에 별도 확인한다. 원문·두 실패·issue 이력은 유지하며 이후 refresh와 최종 proof가 확인돼야 자료 복구 완료를 판단한다.

사용한 [조회 도구](check_checksum.py)와 모든 근거의 해시는 [artifact 목록](artifacts.json)에 기록했다. 앞선 원인 조사 보고서와 봉인된 사본은 수정하지 않았다.
