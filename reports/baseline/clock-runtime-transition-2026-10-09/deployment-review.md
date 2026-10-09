# T04·T11 운영 전환 범위

2026-10-09. 이 문서는 **별도 운영 승인용**이며 아직 운영에 적용하지 않았다.
현재 부팅·Chrony·수집·8개 업무와 자동 정리 mask를 유지한다.

## 고정한 배포본

| 대상 | 최종 식별자 |
|---|---|
| 수집 후보 | `clock-evidence-policy-20261009-65296619a374c325` |
| 수집 소스 tar SHA256 | `0c1ae174206c984dc73ac8d2572d2cf4bad9283c72132e8d0000145c1ba40841` |
| 전환 도구 | `collector-transition-20261009-1c7c4120369fd893` |
| 전환 도구 tar SHA256 | `4898fc07a2458880e59c1bbb1b1040e73623c32b5951360e9e5564761f60dec4` |
| 전체 계획 ID | `22706e571f3a46a8745803b18bca2efacc3318c3e05b2309df6f3c7c37c621e6` |

수집 소스는 [collector-correction/source.tar.gz](collector-correction/source.tar.gz),
전환 도구는 [transition-source-final.tar.gz](transition-source-final.tar.gz)다.
[실제 before/after·기존 ledger를 담은 계획](operating-plan-final.json),
[파일 차이](configuration-final.diff), [현재 false인 승인 양식](approval.template.json)을 함께 보관한다.

원래 `26e7fd4bbd0a68a5` 후보를 수정해 덮어쓰지 않았다. 실제 root 시험의 종료 기록에서
`observe(stamp(), checker(...))`가 조회 이전 표본으로 새 관측을 평가해 `provider_observation_expired`를
남기는 문제를 발견했다. 조회를 끝낸 뒤 표본을 얻는 순서로 바꿨다.
[두 파일의 차이](collector-correction/correction.patch)에는 runtime의 이 순서 변경, 회귀 검사 1건과
기존 테스트 딕셔너리의 서식 정리만 있다. 600초·100ms·500ms·초기 준비·부팅 보정 규칙은 그대로다.

## 정확한 변경 대상

| 경로 | 승인 시 수행할 변경 |
|---|---|
| `/opt/marketdata/releases/clock-evidence-policy-20261009-65296619a374c325` | 새 root 소유 배포본·비 editable 가상환경 설치/봉인 |
| `/opt/marketdata/tools/collector-transition-20261009-1c7c4120369fd893` | 검증한 관리자 도구 설치. 실행기는 `work/collector_transition.py` |
| `/etc/marketdata/runtime.json` | `release_root` 값만 새 수집 후보로 변경 |
| `/etc/systemd/system/marketdata-collector@.service` | 새 배포본·`capture_runtime.py`; 종료 제한 150→330초 |
| `/etc/marketdata/clock-bootstrap.json` | runtime 파일 해시와 위 유닛의 guard 해시만 갱신 |
| `/var/lib/marketdata-clock/boot-state.json` | 원래 closed 증거를 보존한 전환 pending/완료 기록과 profile binding |
| `/var/lib/marketdata-clock/runtime-transitions/<전체 계획 ID>/` | 원래 ledger·계획·승인·진행 단계·적용/원복 receipt |
| 별도 root 승인 자료 폴더 | 정확한 계획과 사용자가 승인한 receipt의 root 소유 사본 |

계획 생성 당시 boot ID는 `eb163bf2-6642-4336-b4d6-6cd58771b13d`다. 기존 ledger의 canonical SHA256은
`666e49943506429f386417d2e81ba8a2a07b2891c985bd8846098e6f5be249f0`이다.
부팅·설정·ledger가 달라지면 이 계획을 그대로 실행하지 않고 실제 상태로 새 검토를 한다.

데이터 root·UID·마운트 UUID·Chrony 설정·시각 정책·원문/manifest/source_root·비밀값·기존 백업·정리 grant는
변경하지 않는다. 기존 bootstrap/Chrony drop-in/8개 예약 업무는 **이전 고정 배포본의 같은 엄격한 gate**를
계속 사용하므로 이전 release를 유지한다. workspace의 pack/backup/restic 실행 코드도 바꾸지 않는다.

## 승인 후 적용 순서

1. 운영 쓰기 전에 현재 boot/프로필/ledger/guard 해시와 미완료 전환 부재를 재확인한다.
   새 배포본과 관리자 도구를 각 전용 경로에 설치하고 SHA256·175개 소스·실제 설치 모듈·Python 버전·
   비 editable import·소유권/권한을 대조한다. 승인 자료는 root 소유 0644, 상위 폴더는 일반 계정이
   쓸 수 없는 경로에 둔다. 기존 배포본을 덮어쓰지 않는다.
2. 승인한 수집 공백 창에서 **두 system collector만 정상 종료**한다.
   진행 중인 backup/pack/restic/기타 업무가 있으면 완료를 기다린다. 전환기는 활성 업무가 있으면 거부한다.
   예약 타이머는 guarded service만 시작할 수 있으며 coordinator exclusive lock과 pending ledger가
   전환 중 새 업무 시작을 막는다. 현재 작업을 무조건 강제 종료하지 않는다.
3. 전환기 `stage`를 실행한다. 초기 상태·승인·provider를 보존하고 기존 gate가 거부하는 schema 2 pending을
   **파일 변경보다 먼저 fsync**한다. 그다음 정확한 세 파일을 순차 적용한다.
4. system manager의 `daemon-reload`를 수행한다. 전환기에는 서비스 제어 명령이 없으므로 이 작업과
   수집 중지/기동은 승인된 운영자가 별도로 수행한다.
5. 전환기 `commit`이 실효 유닛, 정확한 ExecStart/두 ExecStartPre, UID·보호 설정·무한 supervisor 수명,
   330초 종료 제한·쓰기 경로·새 배포본·실제 일반 계정 import/UUID/DNS 접근을 검증한다.
   Chrony invocation은 준비 당시와 같아야 한다. 예기치 않은 Chrony 재시작을 자동 채택하지 않는다.
6. 원래 boot ID·closed·step 시도/결과·초기 관측은 그대로 두고 receipt/history와 새 binding을 확정한다.
   **설정 적용 성공은 현재 시각 준비 성공이 아니다.** commit 시 관측을 따로 기록하고 기존 strict gate를
   다시 확인한다. 준비가 불충분하면 새 수집은 기존 재시도 절차로 대기한다.
7. 두 collector를 시작해 중복 실행 없음·새 import/버전·실제 WS 원문/표준 저장·collection 연결·닫힌
   시각 artifact를 확인한다. 첫 정상 종료/다음 child와 새 예약 checkpoint/restic 편입은 실제 발생 후 기록한다.
   1시간 child와 15초 세션 간격은 유지되며, 모든 호가 공백을 없앤다고 주장하지 않는다.

실행 형태는 아래와 같다. 이는 **승인 이후 root 소유 경로에서 사용할 명령**이며 현재 실행하지 않았다.
변수 `TOOL`은 위 설치 경로의 `work/collector_transition.py`, `PLAN`/`APPROVAL`은 검토한 root 사본이다.

```sh
sudo /usr/bin/python3 -B -I "$TOOL" stage --plan "$PLAN" --approval "$APPROVAL"
sudo /usr/bin/systemctl daemon-reload
sudo /usr/bin/python3 -B -I "$TOOL" commit --plan "$PLAN" --approval "$APPROVAL"
```

## 중단 후 재개와 원복

- 같은 boot에서 알려진 before/after 파일 조합이면 진행 기록과 실제 파일을 대조해 `stage`를 재실행한다.
  receipt 생성 후 ledger 커밋 전에 죽은 경우에는 첫 receipt를 보존한다.
- 미승인 파일, 변경된 provider, 손상된 receipt/progress, 다른 boot에서는 자동 진행하지 않는다.
  ledger 삭제·재등록·해시 임의 변경으로 우회하지 않는다. 다른 boot에서 전환이 남았다면 보류한 채
  별도 복구 계획을 검토한다. 현재 도구의 자동 복구 범위는 **같은 boot**다.
- 원복할 때 후보 collector를 정상 종료하고 업무가 조용한지 확인한 뒤 같은 도구의 `rollback`을 실행한다.
  rollback은 원복 의도를 먼저 기록하고 원래 세 파일을 staging한다. 이후 daemon-reload와 `commit`으로
  이전 실행본의 실제 활성 설정을 검증한다. 적용 receipt/history를 지우지 않고 원복 receipt를 추가한다.

```sh
sudo /usr/bin/python3 -B -I "$TOOL" rollback --plan "$PLAN" --approval "$APPROVAL"
sudo /usr/bin/systemctl daemon-reload
sudo /usr/bin/python3 -B -I "$TOOL" commit --plan "$PLAN" --approval "$APPROVAL"
```

현재 strict 준비를 확인한 뒤 이전 collector를 재개한다. 원복하면 **이전의 엄격한 세션 시작 정책도 돌아온다**.
새 시각 artifact·collection·인덱스·spool·기존 데이터를 삭제하거나 catalog를 과거로 되돌리지 않는다.
이전 CLI에는 `clock-evidence`가 없으므로 보존한 새 후보의 CLI로 조회한다.

## 적용 이후에도 별도로 남는 범위

- 실제 운영 collection의 시각 근거가 새 예약 checkpoint/restic에 포함되고 복원 조회되는지 확인.
- 기존 v1 시각 로그의 보존·백업 편입, 다른 8개 업무의 실행 정책 분리.
- T05 시각 안전 검사와 별도 승인 전까지 checkpoint 자동 정리는 계속 masked.
- T10 시간원 구성, Chrony/RTC/Windows 변경·step·재부팅은 이번 전환에 포함하지 않는다.
- 실제 운영 경로의 최종 root 쓰기·collector 기동은 승인된 적용 단계에서 확인한다.
  격리 transient unit 시험을 운영 배포 완료로 세지 않는다.
