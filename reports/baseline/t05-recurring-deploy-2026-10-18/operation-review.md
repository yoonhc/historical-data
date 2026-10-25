# 승인 검토안 — T05 시스템 반복 자동화

**이 문서는 실행 승인 자체가 아니다. 아직 운영 설치·활성화하지 않았다.**

operation `2c03a81f309d0eca6f03e24a5d84fb838d75598fd1d38dbc6dd2a4204a57ae6e`에 한정한다.
고정 후보 `t05-recurring-retirement-20261018-e57a7995f03fb301`,
도구 `t05-recurring-operation-20261018-447cddda543bc6ed`다.
[계획 원본](operation-plan.json)에 실행 도구 6개·설치 객체 1,798개·모든 변경 바이트와 SHA-256이 있다.
[파일 목록](file-changes.json)은 그 계획에서 추출한 조회용 목록이다.

## 승인 대상

시스템 정리 service/timer에 한해, 고정 코드·경로·정책·scope 안에서 매 예약 **새 계획을 만들고**
현재의 시각·당일 보존·무결성·잠금·승인 조건을 모두 만족한 대상만 정리하는 반복 권한이다.
특정 10개를 다시 삭제하는 승인이 아니다. 기존 사용자 서비스/타이머는 계속 masked다.

설치 경로:

- `/opt/marketdata/releases/t05-recurring-retirement-20261018-e57a7995f03fb301`
- `/opt/marketdata/tools/t05-recurring-operation-20261018-447cddda543bc6ed`
- 감사 경로 `/var/lib/marketdata-clock/t05-recurring-operations/2c03a81f309d0eca6f03e24a5d84fb838d75598fd1d38dbc6dd2a4204a57ae6e/`

| 운영 변경 경로 | before → after |
|---|---|
| `/etc/marketdata/checkpoint-clock-retirement/units/marketdata-checkpoint-retirement.service` | 현 수정 유닛 → 새 후보 경로, 공통 부모 mount 그대로 |
| 같은 디렉터리 `marketdata-checkpoint-retirement-preview.service` | 기존 경로 → 새 후보 경로 |
| 같은 디렉터리 `marketdata-checkpoint-retirement-apply@.service` | 기존 경로 → 새 후보 경로 |
| `/etc/systemd/system/marketdata-checkpoint-retirement-apply@.service` | 기존 root 일반 파일 → 새 canonical과 같은 root 일반 파일 |
| `/etc/systemd/system/marketdata-checkpoint-retirement.service` | `/dev/null` mask → 새 canonical과 같은 root 일반 파일 |
| `/etc/systemd/system/marketdata-checkpoint-retirement.timer` | `/dev/null` mask → 검증된 calendar timer 일반 파일 |
| `/etc/marketdata/checkpoint-clock-retirement/recurring.json` | 기존 비활성 → 새 scope 비활성 → 승인 시 활성 |
| `/etc/systemd/system/timers.target.wants/marketdata-checkpoint-retirement.timer` | 부재 → 위 시스템 timer를 가리키는 링크 |

일반 설정/유닛은 root:root·0644다. 유닛은 옛 renderer로 생성하지 않는다.
`ReadWritePaths=/mnt/backup/marketdata-historical` 공통 부모 경계를 그대로 사용한다.
canonical timer와 실제 preview 링크, settings/policy/binding/manual은 보호 입력으로 유지한다.

기존 비활성 recurring SHA `9d9c544fe9fc7c4b3ec498fddf6feef7fb419b09b1f760a2bbe3c009f8422a5e`,
새 비활성 SHA `6d731e2e9e43aba3f249c5349531b1155948e4ecdeb4546ae0c4ef7463d62c66`다.
예상 scope는 `311ab16f019b4473185c487b219430c1fab4e90c4257f813bacfa696b1ba29a5`다.

활성 문서의 정확한 바이트는 **승인 명령 수락 뒤** 확정한다. 새 비활성 제안의 내용에서
`enabled=true`, 최초 승인 명령 시각 `approved_at`, operation의 `authority.json` 경로
`approval_reference`만 설정한다. 준비 시각을 승인 시각으로 쓰지 않는다.
영속 authority와 active-grant 해시는 재실행에도 같아야 한다. scope나 다른 값의 자동 변경은 허용하지 않는다.

## 적용 순서와 중단 처리

1. 사용자 명시적 승인 인자·계획/도구 해시를 대조한다. root 소유 고정 도구로 복사·재진입한다.
2. 별도 operation EX 아래 before/boot/ledger·50개 보호 입력·24개 기존 기록·사용자 mask·프로세스를 확인한다.
   정상적인 새 완료/순서 객체 추가는 허용하며 기존 객체를 덮어쓰지 않는다.
3. 해시가 맞는 소스/환경을 최종 `/opt`로 복사한다. 파일별 임시 쓰기→fsync→rename으로 중단을 처리한다.
   완료된 설치의 누락/변조는 자동 복구로 숨기지 않는다. UID 1000의 실제 Python·import·CLI를 확인한다.
4. 시스템 timer 유휴, 정리 service/preview/수동 인스턴스·대기 job 유휴를 확인한다.
   진행 중이면 최대 600초 **정상 종료만 대기**한다. 이 동안 coordinator/runner 잠금은 없다.
5. coordinator SH + 설정 디렉터리 EX + 기존 runner EX를 nonblocking으로 얻어 현재 상태와 미완료 기록을 재확인한다.
   비활성 새 grant → canonical/실제 유닛 순으로 교체하고 daemon-reload 한다.
6. 최종 경로 UID 1000의 실제 fragment guard·읽기 전용 Chrony·`propose`를 확인한다.
   실제 proposal 전체가 예상값과 다르면 **승인 활성화·timer 시작 전에 보류**한다.
7. 활성 grant와 enable 링크를 쓰고 재조회한다. 잠금 해제 후 **timer만 start**한다.
   service 직접 시작·추가 backup·checkpoint 삭제 명령은 도구에 없다.
8. 실제 다음 calendar 시각이 다음 24시간 내 06:40 UTC인지 기록한다.
   Persistent=false이며 OnActiveSec 등 monotonic 기동 조건은 없다. 활성화 receipt는 업무 정리 완료를 뜻하지 않는다.

중단 전 영속 intent와 현재 파일을 대조해 인지한 before/after만 재개한다.
timer 시작의 응답을 잃은 경우 현재 timer가 활성/정상인지 확인해 끝낸다.
활성 여부가 불명확하거나 inactive이면 자동 start를 반복하지 않고 점검을 요구한다.
필요하면 아래 원복으로 비활성 상태에 닫고 새 활성화 계획을 검토한다.
반복 apply가 이미 완료됐으면 검증만 하고 timer를 다시 시작하거나 grant를 다시 쓰지 않는다.

## 승인 후 사용할 명령

아래는 **승인 전에는 실행하지 않는다.** 도구의 `check`는 최초 설치 전 상태를 읽기 대조하는 용도다.

```sh
sudo /usr/bin/python3 -B -I \
  /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-recurring-deploy-2026-10-18/handoff.py \
  --action apply \
  --approve-operation 2c03a81f309d0eca6f03e24a5d84fb838d75598fd1d38dbc6dd2a4204a57ae6e
```

정확한 동일 apply 재개만 가능하다. 알 수 없는 구성이나 잠금 경합을 우회하지 않는다.
새 boot·입력 변경은 적용 승인의 재검토 대상이며, 원래 계획을 편집해 승인 ID를 유지하지 않는다.

## 중지·원복

위 고정 `handoff.py`에서 같은 operation ID와 `--action stop` 또는 `--action rollback`을 사용한다.
최초 실행 후에는 `/opt/marketdata/tools/t05-recurring-operation-20261018-447cddda543bc6ed/handoff.py`로도 실행할 수 있다.

- **stop:** timer를 먼저 멈춘다. service/PID/job이 끝나기 전 grant·유닛을 바꾸지 않는다.
  유휴와 잠금을 확보하면 반복 승인을 비활성화하고 시스템 두 mask와 enable 링크 부재로 닫는다.
  새 실행본/canonical은 유지한다. 설치 중간의 stop은 일부 canonical이 남을 수 있으므로 그 경우 rollback으로 정리한다.
- **rollback:** 같은 중지·정상 종료 대기 뒤 정확한 이전 8개 노드로 복원한다.
  이전 비활성 scope, 시스템 두 mask와 사용자 두 mask 상태가 종착점이다.
  새 `/opt`, root 감사 기록, 실제 run/plan/journal·baseline/완료 상한/순서는 삭제하지 않는다.
- 중지 시 600초가 지나도 실행 중이면 timer만 멈춘 상태로 보류한다. grant는 해당 실행 종료까지 유지한다.
  강제 종료나 timeout 변경 없이 정상 종료 후 같은 stop/rollback을 재실행한다.
- 미완료 journal은 **자동화 차단을 마무리한 뒤에도** 점검 대상으로 보고한다.
  이를 해결하거나 구버전에서 실행 가능하게 만들기 위해 기록을 고치지 않는다.
- stop/rollback을 선택한 operation은 terminal이다. 같은 과거 apply 승인으로 다시 켜지 않는다.
  모르는 파일/권한/기록 변경은 보류하며 새로운 정확한 검토가 필요하다.

중지·원복은 현재 UTC의 품질 회복을 기다리는 작업이 아니다. 활성화 때와 달리 다른 boot에서의 차단도 허용하되,
인식 가능한 변경 경로·사용자 mask·수동 비활성·기존 근거 보호와 유휴/잠금 조건을 유지한다.
도구는 Chrony·수집기·T16 유닛을 제어할 수 없다.

## 적용 후 구분할 결과

설치/권한/유닛/scope와 다음 예약 확인, 실제 timer 출처의 첫 새 plan·0건/보류/실패/완료,
자연 발생한 첫 자동 삭제와 보호/제외/기록 대조를 각각 확인한다.
서비스 success(75 포함)만으로 정리 완료로 판정하지 않는다.
백업·restic 검사나 삭제를 시험 목적으로 추가 실행하지 않는다.

실제 설치·manager 명령·최종 scope 대조는 이 계획의 **적용 시점에 남은 확인**이다.
이번 격리 73개와 이전 관리자 사전 점검을 실제 자동 삭제 성공으로 확대하지 않는다.
