# T13 — 예약 daily의 미완료 날짜와 원격 제공 상태

2026-10-10 **18:40 KST 전후 읽기 전용 확인**이다.
기존 T13 대상 **10/8 UTC의 네 데이터셋은 오늘 예약 실행에서 실제 편입됐다**.
현재 남은 것은 **10/9 UTC의 같은 네 데이터셋**이며, 조회 시점에는 ZIP과 CHECKSUM 모두 HTTP 200으로 제공된다.
오늘 미완료 재시도가 끝난 뒤 추가 실행은 없었다. 이 점검에서 update·서비스 시작·reset-failed·설정 변경은 하지 않았다.

## 10/8 UTC 대상 — 편입·선택 파일 검증 완료

오늘 12:22 KST의 첫 예약 실행에서 아래 revision이 생성됐고 현재 active에 연결돼 있다.
카탈로그는 `mode=ro`로 열었으며 운영 Store 초기화·카탈로그 갱신은 하지 않았다.
각 원본 artifact와 표준 Parquet의 SHA-256, 행 수, 하루 전체 timestamp 격자, 미해결 blocking issue 부재를 확인했다.

| 대상 | 행 수 | UTC timestamp 범위 | 편입 시각 KST |
|---|---:|---|---|
| 선물 trade bars 1m | 1,440 | 10/8 00:00–23:59 | 12:22:04 |
| 선물 mark price bars 1m | 1,440 | 10/8 00:00–23:59 | 12:22:05 |
| 선물 index price bars 1m | 1,440 | 10/8 00:00–23:59 | 12:22:05 |
| OI archive 5m | 288 | 10/8 00:00–23:55 | 12:22:07 |

합계 **4,608행**이다. OI의 격자는 원천 timestamp label의 연속성 판정이며 별도의 사건 시각 의미를 인증하지 않는다.
OI revision에는 기존 수집이 남긴 비차단 `source_order_reversed` 경고(`adjacent_count=37`)가 있다.
현재 표준 자료는 정렬된 전체 288개 label을 충족하며, 원본과 경고를 그대로 보존했다. 품질 경고까지 없었다고 표현하지 않는다.

정확한 revision ID·원본 artifact·해시·검증 결과는 [카탈로그와 선택 파일 대조](catalog-evidence.json)에 있다.
전체 저장소를 다시 전수 검증한 결과는 아니다.

## 오늘 실행과 10/9 UTC 미완료

- 예약 timer 실제 trigger: **12:22:01 KST**.
- 실제 update 실행: **12:22:02, 12:23:08, 12:24:14, 12:25:20, 12:26:27**의 다섯 번.
- 마지막 종료: **12:26:33**, 코드 2. 각 실행은 8개 target 중 4개 complete·4개 partial이고 오류 배열은 모두 비어 있다.
- 네 partial은 10/9 UTC `[00:00, 다음 날 00:00)`만 부족하다. bars 각각 1,440개, OI 288개 label이다.
- **12:27:33** 다음 재시도에서 `Start request repeated too quickly`가 기록됐다.
- 18:39 조회 당시 service는 failed, timer는 active/waiting, 다음 실제 예약은 **10/11 12:24:03 KST**다.

수집 코드의 archive 경로는 HTTP 404를 `waiting_source`로 기록하고,
update CLI는 미완료 결과에 종료 코드 2를 사용한다. systemd의 `2/INVALIDARGUMENT` 이름만으로 CLI 인자 오류라고 해석하지 않는다.
현재 카탈로그에는 네 대상의 10/9 active revision이 없고 마지막 보고서도 같은 날짜의 미완료를 유지한다.

[고정한 마지막 daily 보고서](daily-latest.json), [실제 40개 target 실행과 현재 coverage](catalog-evidence.json),
[systemd 상태·journal 요약](host-and-journal.json), [원본 journal](daily-journal.jsonl)을 함께 보존했다.

## 원격 현재 상태 — 네 종류 모두 제공 응답

**18:39:58 KST**, 마지막 보고서에 남은 정확한 Binance URL 네 개와 대응 CHECKSUM에 HEAD를 요청했다.
여덟 요청 모두 HTTP 200, `X-Cache: Miss from cloudfront`였다. HEAD 제공 확인이며 원격 ZIP 본문을
새로 다운로드·파싱·편입한 결과는 아니다.

| 10/9 UTC 파일 | ZIP 크기 | ZIP Last-Modified KST | ZIP / CHECKSUM |
|---|---:|---|---|
| 선물 trade bars | 60,398 bytes | 10/10 17:05:55 | 200 / 200 |
| 선물 mark price bars | 33,531 bytes | 10/10 17:27:34 | 200 / 200 |
| 선물 index price bars | 34,335 bytes | 10/10 16:53:12 | 200 / 200 |
| OI metrics | 11,408 bytes | 10/10 15:41:42 | 200 / 200 |

Last-Modified는 현재 객체의 수정 시각이며 최초 공개 시각을 입증하지 않는다.
현재 객체의 시각이 오전 재시도보다 늦고, 실제 조회는 성공했으므로 지금도 원격 미제공이라고 판단할 수 없다.
Binance는 일별 자료를 다음 날 제공한다고 안내하지만 이 호스트의 12:20 KST 예약에 맞춘 공개 보장은 적혀 있지 않다.
[공식 공개 데이터 안내](https://github.com/binance/binance-public-data#binance-public-data).

성공 응답의 URL·헤더·시각은 [호스트 원격 조회](remote-20261010T093958.json)에 있다.
앞선 [샌드박스 조회](remote-20261010T093927.json)는 DNS 접근 실패이며, 이를 원격 미제공의 증거로 사용하지 않았다.

## 다음 처리와 변경하지 않은 범위

10/8의 기존 미완료는 해소됐지만 daily 전체가 최신 대상까지 complete인 상태는 아니다.
T13 활성 항목을 **10/9 공개 자료의 기존 경로 재처리**로 갱신했다.
다음 처리는 기존 daily 서비스의 시각 준비 검사·잠금을 유지한 재실행과 실제 네 날짜 파티션의 편입 확인이다.
18:40 조회에서는 공통 `marketdata-clock-ready.service`가 activating/start였으므로, 시작 요청 자체를
업무 착수나 완료로 세지 않는다. 다음 예약 시각도 완료 보장으로 취급하지 않는다.

`RestartSec=60s`, 10분 내 시작 5회 정책은 몇 시간 뒤의 공개를 계속 재확인하는 정책이 아니다.
systemd도 시작 제한에 도달한 서비스는 자동 재시작이 중단되며, 이후 timer·수동 시작 등으로 다시 시작할 수 있다고 설명한다.
[systemd v255 시작 제한](https://raw.githubusercontent.com/systemd/systemd/v255/man/systemd.unit.xml).
10/9·10/10의 유사한 양상을 근거로 공개 지연과 일반 오류의 재시도 요구를 별도 조건부 검토에 남긴다.
이번에 timer·retry·시각 gate를 바꾸거나 시작 제한을 해제하지 않았다.

이 결과는 공개 아카이브 갱신 상태다. T04의 checkpoint/restic/복원 성공과 실시간 WS 수집 품질을 대신 판정하지 않는다.
자동 checkpoint 정리 service/timer는 조회 시점에도 masked·비활성이었다.

재현 스크립트는 [observe_daily.py](observe_daily.py)다. 첫 보조 스크립트의 파일명을 `inspect.py`로 두어
Python 표준 모듈을 가리는 import 오류가 있었고, 이름을 바꾼 뒤 격리 옵션 `-I`로 검증했다.
이 오류는 파일·DB 검증 시작 전 발생했으며 운영 자료를 바꾸지 않았다.
