# 검증 범위와 실패 이력

## 전환 도구

최종 `collector-transition-20261009-1c7c4120369fd893`의 58건과 tar를 새 경로에 추출한 같은 58건이 통과했다.
[최종 XML](transition-final.xml), [clean 재현 XML](transition-final-clean.xml).
실제 파일·fsync·flock·SIGKILL을 사용하고 서비스/provider 응답은 명시적 시험 대역을 사용했다.

- prepared/pending/세 파일 변경/files_staged/receipt/committed의 실제 SIGKILL 후 재개 또는 원복.
- 원복 중 다시 종료돼도 적용 이력 보존. 승인 ID·정책 범위·도구·설정·boot 변화·알 수 없는 파일·손상 증거 거부.
- 기존 gate가 중간 schema 2 상태를 거부하며 보정 권한이 열리지 않음.
- 실효 유닛의 수명·종료·명령·쓰기 경로·보호 설정 불일치, 미완성 설치 manifest 거부.
- 설정 승인/활성화와 현재 시각 준비 결과를 분리.

초기 43건 이후 설치 manifest의 누락 및 실효 유닛 대조를 보충했다. 실제 read-only 계획 생성에서
`@.service` 경로가 초기 허용 문자 검사에 빠진 문제를 발견해 수정하고 1건을 추가했다.
초기 `5e638d…`, `a1d044…` helper와 원래 수집 후보를 연결한 `operating-plan.json`은 미적용 이력이다.
최종은 `release-final/`, `transition-source-final.tar.gz`, `operating-plan-final.json`이다.

## 수집 후보 종료 순서 수정

root 시험의 마지막 기록에서 정상 provider 관측이 `provider_observation_expired`로 거부됨을 확인했다.
Python 인자 평가 순서상 `observe(stamp(), checker(profile))`의 표본이 뒤에 조회한 관측보다 먼저였기 때문이다.
조회가 10ms 걸리는 회귀 입력으로 원래 후보의 실패와 보완 후보의 통과를 확인했다.
[수정 전](shutdown-order-before.xml), [수정 후](shutdown-order-after.xml).

새 후보 `65296619a374c325`의 관련 **305건, skip 0건**이 통과했다.
[XML](collector-correction/regression.xml), [최소 차이](collector-correction/correction.patch).
실제 restic 통합·원문/호가 회귀·부팅 gate·시각 로그·강제 종료 검사는 포함하되,
이 숫자로 실제 운영 NTP 품질·최종 설치 경로·예약 백업 완료를 주장하지 않는다.

## 실제 버전 조합과 새 자료 이후 원복

[최종 실행 결과](mixed-versions-result.json)의 새 시험 저장소에서 수행했다.

1. 새 non-editable 후보의 **실제 depth/aggTrade capture**에 합성 거래소 입력을 공급한다.
   새 collection 연결과 clock JSONL artifact를 만든다.
2. 현재 업무가 사용하는 **workspace `.venv`의 기존 pack·daily_backup·archive_backups**로 처리한다.
   실제 restic 0.19.1 저장·전체 데이터 검사·명시적 ID 추출·프로그램 복원·독립 재백업을 실행한다.
3. 새 자료가 남아 있는 같은 저장소에 실제 `/opt`의 **이전 고정 설치본**으로 depth/aggTrade capture를 추가한다.
   운영 저장소를 이전 사본으로 바꾸거나 새 catalog/index/artifact를 삭제하지 않는다.
4. 기존 pack·daily_backup·restic으로 두 번째 사본을 만들고, **그 backup ID에 대응하는 snapshot ID**를 골라
   다시 복원·재백업한다. 두 단계 모두 재실행은 처리 0건, 중복 snapshot 생성이 없다.
5. 새 CLI로 source/checkpoint/추출본/복원본/독립 재백업의 두 collection 조회를 대조한다.
   총 20개 비교가 같고 원래 artifact 7개가 바이트 그대로 남았다. `fetches_url` 인덱스도 유지됐다.

최종 시험의 두 backup ID와 snapshot ID:

| 단계 | backup ID | snapshot ID |
|---|---|---|
| 새 수집 자료 | `0315623ec54fad10c0fed9d8f5c71abd43efff77821f19785cf5e3d9bac0dc4b` | `1e926d4d5d8b48d974900525b5fac989b40be1366ff90963cb57c8aea048edf0` |
| 이전 수집으로 원복한 뒤 추가 자료 | `047cb2867ed61b3f962edbfc900b87899b7d2565de879f21cdc0c5911e1004c7` | `f642dff5443582175b8c3372cb47cd2e98da3450435d8d1ce7700295f82d2837` |

**시험의 대역:** 거래소 transport/수신 시각, `/tmp` 시험 폴더를 별도 백업 마운트로 간주하는 UUID 판정.
실제 운영 mount/UUID·백업 profile·암호는 사용/변경하지 않았다. restic 바이너리·repository ID 검사·
manifest/source_root·파일/논리 검사·잠금·프로그램 복원은 실제 기존 코드다.
시장 event 파일은 pack에 편입되며 clock artifact는 loose raw로 남아도 정상 백업된다.
시각 근거 자체를 event pack에 넣었다고 주장하지 않는다.

초기 실행에서는 시험 도우미의 Store context manager 오용, 시각 구간 길이 0, 이전 배포본의 pytest 부재,
미래 checkpoint 날짜, 동일 합성 입력의 중복, snapshot 목록의 첫 항목 재선택을 발견했다.
각 시도는 `mixed-versions-attempt-*.json`과 `mixed-versions-parent-checkpoints-result.json`에 남았다.
최종 시험은 구분된 두 실제 사본 ID를 명시적으로 선택했으며 이 이전 시도를 최종 성공 근거에 합산하지 않는다.

동일 입력 중복은 도우미 오류만으로 단정하지 않았다. 같은 거래소 ID/내용/**로컬 수신 시각까지 동일한**
합성 입력을 별도 collection에 반복하면 event segment의 content ID가 같아 두 번째 연결이 누락되고
replay가 보고서와 달라진다. 이전 코드→이전 코드에서도 재현됨을 확인했다.
[독립 대조](duplicate-fixture-control.json). 이 극단 입력은 새 clock 정책의 회귀가 아니며 실제 운영 발생 여부는
확인하지 않았다. 후속 T12에 남기고 코드·기존 보고서를 고쳐 맞추지 않았다.
최종 버전 조합 시험은 서로 다른 update ID로 두 수집 구간을 구성했다.

## 실제 systemd 접점

시험용 root 소유 `/opt/md-clock-probe-<nonce>`의 비 editable 복사 환경과
`/var/lib/md-clock-probe-<nonce>` 저장소를 사용한다. root가 관리하는 transient service가 일반 계정으로 실행된다.
보호 옵션은 그대로이며 **입력 clock/bootstrap·수집 payload와 경로가 시험용**이다.
원래 수집 CLI의 WS/저장 접점은 위 mixed 시험이, supervisor의 systemd 수명/종료/worker 접점은 이 시험이 맡는다.
실제 운영 bootstrap을 통과해 운영 자료를 수집한 시험과 구분한다.

- 첫 실행 `9cfb…`: 빈 fixture에 필수 `data/` 디렉터리가 빠짐. root 기록에는 timeout만 남았으며,
  별도 동일 시작 검사에서 `Data directory inaccessible: data`를 재현했다. 디렉터리와 실패 진단 보존을 보완했다.
- 두 번째 `34a1…`: child 시작·종료는 확인했으나 빈 CapabilityBoundingSet을 생략한 `systemctl show` 출력 때문에
  시험 판정이 실패했다. `--all`로 빈 값까지 읽도록 수정했다. 보호 옵션을 바꾸지 않았다.
  종료 기록에서 위 후보의 표본 순서 문제를 발견했다.
- 세 번째 `2e3a…`: 수정 후보의 **정상 종료 시나리오 통과**. 같은 supervisor에서 다음 child를 시작하고,
  child 최종 저장 뒤 publication을 장벽에서 멈춘 동안 서비스가 deactivating 상태로 기다리는 것을 확인했다.
  장벽 해제 후 worker/queue/spool을 정리하고 종료했다. 최종 시각 판정에 가짜 hard fault가 없었다.
  강제 종료 명령은 main/child에 시그널을 보낸 뒤 보조 프로세스 처리 오류를 반환해 전체 run은 false다.
  그 결과를 재시작 성공으로 세지 않는다.

- 네 번째 `08b7…`: **강제 종료 시나리오만 실행해 통과**했다.
  [실제 결과](system-preflight-result-08b7a916b7fc4a46.json).
  kill 명령의 같은 오류/종료 코드 1을 보존하면서도 이전 main/child PID가 모두 사라졌는지 별도로 확인했다.
  invocation은 `233925c7d3044369b21e12df5dbf266f`에서 `a61809b2fa524e15bfc5738a5d71c25c`로 바뀌었고,
  새 child가 저장한 뒤 정상 종료했다. pending 0, 열린 spool 0, worker 종료를 확인했다.
  강제 종료한 이전 run의 spool이 artifact 조회 구간으로 복구됐다. 이전 collection의 종료 시각은 없으므로
  `collection_end_unknown`을 유지했고, 다음 collection은 근거 available이었다.
  관측하지 않은 종료 시각을 만들거나 이전 collection을 정상 완료로 고치지 않았다.

**전체 run은 첫 세 번 false, 마지막 kill-only run true다.** 정상 종료의 성공 근거는 세 번째 run의
graceful 결과이고, 강제 종료·재시작의 성공 근거는 네 번째 run이다. 이 둘을 통과한 시나리오로 집계하되
이전 실패 전체를 성공으로 바꾸지 않는다. kill 명령 자체의 성공을 주장하지 않으며, 보조 프로세스 오류의
systemd 내부 원인을 이번 검증에서 확정하지 않았다.

각 root 시험은 해당 nonce의 유닛·파일만 정리하고 운영 설정/ledger/Chrony invocation·mask 유지를 대조한다.
cleanup errors 0, 시험 경로 부재, 유닛 not-found를 결과에 별도로 남긴다.

18:49 KST의 [최종 읽기 전용 대조](final-audit.json)에서도 38개 운영 파일/링크의 해시·권한·소유권,
기존 boot/ledger, Chrony PID/invocation, 이전 collector 실행 설정, 시험 경로·유닛 부재를 확인했다.
원래 175개 후보 소스와 새 175개 소스·tar, 최종 helper 9개 파일·tar, 실제 설치 runtime 37개 파일의
해시를 다시 대조했다. 원래 후보와 T10 도구는 보존했다. 실제 운영 적용·root 전환 쓰기는 수행하지 않았다.
