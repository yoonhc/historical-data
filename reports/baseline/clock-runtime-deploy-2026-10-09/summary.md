# T04·T11 운영 적용 기록

2026-10-09 **19:43 KST** 관측 기준. 고정 수집 배포본의 운영 반영과 초기 기동·저장·
닫힌 시각 근거 등록을 확인했다. 첫 1시간 child 경계와 새 근거의 예약 백업·복원은 아직 미완료다.

## 승인 범위와 배포본

- 수집 배포본: `clock-evidence-policy-20261009-65296619a374c325`.
- 원래 전환 도구: `collector-transition-20261009-1c7c4120369fd893`.
- 전체 계획 ID: `22706e571f3a46a8745803b18bca2efacc3318c3e05b2309df6f3c7c37c621e6`.
- [사용자 승인 기록](approval.json), [입력 해시](install-inputs.json),
  [기존 정확한 변경·원복 범위](../clock-runtime-transition-2026-10-09/deployment-review.md).

같은 boot ID와 원래 파일·ledger·Chrony invocation을 확인한 뒤 적용했다.
새 root 소유 `/opt` 배포본과 관리자 도구를 추가했고 이전 배포본은 보존했다.
설치된 Python·의존성·비 editable 모듈 37개의 해시는 기존 검증본과 일치했다.
새 경로의 설치 재현과 실제 운영 설치는 구분한다.
[설치 재현](installation-preparation.json), [최초 실제 적용 결과](initial-result.json).

## 최초 실행과 보류

최초 적용은 19:21:06에 시작했다. 기준 측정이 600초를 넘어 기존 수집을 유지하면서 기다렸다.
실제 준비가 회복된 뒤 19:23:49경 두 collector에 정상 종료를 요청했다.
종료 직전 depth 7,448건, aggTrade 3,646건의 수신·저장 건수가 각각 같았고,
두 collection은 `interrupted`, `complete=false`로 종료 보고를 저장했다.
systemd의 종료 코드 130을 정상 완료 collection으로 해석하지 않는다.
[종료 collection 기록](collection-state-during-hold.json).

세 파일 staging과 daemon-reload 이후, 19:23:55에 `Effective strict start checks differ`로 commit이 거부됐다.
세 파일은 승인된 after 해시였지만 ledger는 schema 2 pending으로 남아 업무 시작이 차단됐다.
기존 boot·closed·초기 보정 증거는 이 pending 기록 안에 보존됐다.
Chrony PID 1245·invocation·설정·RTC와 자동 정리 mask는 유지됐다.
[실제 중간 상태](operating-observation-20261009T102904Z.json).

### 발견 원인과 최소 재개 보완

실제 `systemctl show`는 두 `ExecStartPre`를 같은 키의 **두 줄**로 출력한다.
원래 전환 도구의 `dict(line.split(...))`가 첫 줄을 덮어써 시작 검사 하나를 잃었다.
실제 유닛에는 승인된 두 검사가 있었으며, 이 보류는 설정 불일치가 아닌 조회 파서 문제였다.
기존 격리 activation 시험은 두 명령을 한 문자열에 모은 대역을 사용해 이 출력 형태를 검증하지 못했다.

[재개 코드](continue_runtime.py)는 고정 도구의 `LiveHost.unit` 조회만 대체한다.
반복된 `ExecStartPre`/`ExecStart` 값을 모두 보존하고, 다른 scalar 키의 중복은 거부한다.
고정 도구의 plan/해시·실효 명령·보호 옵션·release·권한·provider·ledger·commit 검사는 그대로 호출한다.
고정 소스나 계획의 해시를 덮어쓰지 않는다. 재개 코드 자체는 **추가 보완**이며 원래 검증본과 동일하다고 하지 않는다.

재개 코드 SHA256: `fb7d502cc4516b916a366ce05c28e7a0014fbc195fbcb4837acb9d5261c82d23`.
root 사본과 범위 기록을 먼저 보존하고, 실제 commit receipt에도 해당 경로·해시·원래 도구 해시를 연결한다.
누락·추가·다른 명령 거부, 실제 두 줄 출력, 기존 한 줄 출력, apply/rollback의 동일 검사,
pending 상태에서 같은 계획으로 commit하는 9건과 기존 전환 58건이 통과했다.
[67건 결과](reader-recovery-validation.json), [회귀 검사](test_systemctl_reader.py).

## 재개와 실제 운영 결과

사용자가 보완 범위가 명시된 관리자 재개 명령을 실행했다. 19:33:46에 같은 계획의 commit이 완료됐고,
현재 strict 시각 준비도 통과했다. step 권한은 다시 열리지 않았다.
[재개 root 결과](recovery-result.json), [재개 코드 범위](recovery-scope.json),
[원래 승인·보완 해시를 연결한 적용 receipt](apply-receipt.json).

| 실제 확인 | 결과 |
|---|---|
| 새 supervisor | depth PID `224785`, aggTrade PID `224784`; 각 1개 |
| 수집 child | depth PID `224866`, aggTrade PID `224855`; 각 1개, 모두 UID 1000 |
| 실제 시작 | 두 collection 모두 19:33:48 KST. `python -I`, 새 `/opt` 배포본으로 실행 |
| depth collection | `d2d18424592f4f549f5bda828e270759` |
| aggTrade collection | `c5b36646466344d5b1672275ffb2faf8` |
| 19:43 저장 범위 | depth event 5,709행, aggTrade WS 8,637행; 실행 중 시점의 값 |
| 실제 파일 | 양 스트림 원문·Parquet 파일 존재와 카탈로그 SHA256 일치 |
| 시각 근거 | 총 6개 raw artifact 등록(시작 근거 4개 + 첫 5분 회전 2개), collection/run/boot/child 연결 확인 |
| 시각 상태 | 두 스트림 `raw_running=true`, `current_estimate_usable=true`, 위반 0, stale 이력 없음(관측 범위 한정) |
| 보존 조건 | 원래 boot·closed·보정 증거, Chrony PID/invocation·설정·RTC, 그 외 guard 파일과 정리 mask 유지 |

[실제 프로세스·저장·상태 관측](operating-observation-20261009T104329Z.json),
[초기 완료 판정과 미완료 항목](initial-operating-verification.json).
설치 commit만 성공한 19:33:46 결과와 실제 수신·저장 확인은 분리했다.

각 collection에서 첫 5분의 **297개 관측 구간**을 실제 새 CLI로 조회했다.
[depth 조회](clock-query-d2d18424592f4f549f5bda828e270759.json),
[aggTrade 조회](clock-query-c5b36646466344d5b1672275ffb2faf8.json).
실행 중인 collection이므로 `collection_end_unknown`, `evidence_state=unknown`, CLI 종료 코드 2다.
조회 실패나 근거 부재로 해석하지 않고, 닫힌 범위만 근거가 있으며 열린 끝은 미확인으로 남긴다.
`available` 역시 정상 품질 인증이 아니라 근거 존재를 뜻한다.

전환 오류를 포함해 약 **10분**의 수집 공백이 있었다. 호가 원문의 해당 공백은 복구됐다고 하지 않는다.
aggTrade는 기존 자동 REST 경로가 ID `3480321934`~`3480325964` **4,031개를 5페이지로 보강**했다.
직전 WS ID `3480321933`, 재개 WS 첫 ID `3480325965`와 연결되고, 보강 Parquet 전체 ID를 읽어
연속·중복 없음과 파일 해시를 확인했다. 원래 WS 수신 시각을 REST 자료에 만들지 않았다.
[독립 저장 범위 대조](cutover-gap-verification.json). 요청 예산·코드를 바꾸거나 이번 점검에서 추가 REST 요청을 하지 않았다.

## 남은 실제 관찰과 다음 점검

### 20:17 KST 중간 확인

첫 두 collection은 아직 `running`이다. 같은 supervisor/run과 첫 child ID가 유지되며,
20:16 조회 시 depth 25,190건·aggTrade 29,877건의 수신/저장 건수가 각각 같았다.
depth는 sync loss 0, aggTrade는 기존 1개 공백 복구·미복구 0이었다.
[첫 경계 전 상태](pre-boundary-20261009T2016.json).

19:43 이후에는 자연스러운 reference 지연이 발생했다. 등록 완료된 시각 artifact의 조회 범위에서
약 **19:51:00~20:07:04 KST**를 걸치는 951개 표본 구간에 stale 표시가 있었다.
두 스트림에서 해당 구간의 현재 오차 판정·원문 시작 허용은 유지됐고, 표본의 최대 reference age는
약 1,563.406초였다. 951은 장애 건수가 아니라 인접 관측 구간 수다.
20:16 조회 시 현재 stale은 해소됐지만 `reference_stale_seen=true` 이력은 유지됐다.
[등록된 근거 대조·artifact ID·관측 경계](pre-boundary-clock-observation-20261009T2017.json).

이는 첫 child 실행 중의 실제 stale 관측이며, **stale 상태에서 다음 child가 시작됐다는 근거는 아직 아니다**.
collection 전체 조회의 미확인 사유는 여전히 `collection_end_unknown`이다.
예정된 첫 child 전환과 운영 백업·복원은 아래 일정의 실제 기록으로 따로 판정한다.

1. **오늘 10/9 20:40 KST 이후**: 첫 1시간 child의 실제 최종 보고, 같은 supervisor의 다음 child,
   15초 간격 중 감시와 근거 연결을 확인한다. 시간 경과만으로 완료 처리하지 않는다.
2. 다음 checkpoint 예약은 **10/10 13:10 KST**. restic 타이머는 매시 `:05`/`:35`다.
   이후 **15:00 KST 전후 점검**에서 새 근거가 포함된 정확한 backup ID·snapshot ID·전체 검사 완료를 대조한다.
   날짜가 됐다는 이유로 편입 완료를 가정하지 않는다. strict gate나 검사 시간이 길어지면 실제 상태로 남긴다.
3. 그 명시적 snapshot에서 복원한 collection의 시각 근거 조회를 대조한다. 오늘의 초기 raw 등록을 백업 완료로 세지 않는다.

## 현재 상태 조회와 원복

일반 사용자 상태 조회는 [observe_runtime.py](observe_runtime.py) 또는 새 runtime의 `--status`를 사용한다.
이 조회는 보고서만 남기며 서비스·시각·설정을 바꾸지 않는다.

```sh
/opt/marketdata/releases/clock-evidence-policy-20261009-65296619a374c325/.venv/bin/python -B -I /opt/marketdata/releases/clock-evidence-policy-20261009-65296619a374c325/integrations/runtime/capture_runtime.py --profile /etc/marketdata/runtime.json --bootstrap-profile /etc/marketdata/clock-bootstrap.json --stream depth --status
```

`--stream aggtrade`로 다른 스트림을 조회한다. runtime의 실행·시각 상태와 collection의 시장 데이터 품질은 별개다.

원복은 [rollback_runtime.py](rollback_runtime.py)에 준비했다. 같은 boot·계획·적용 receipt·현재 파일을 확인하고,
현재 시각 준비 및 업무 유휴 상태를 확인한 다음 **두 수집기만 정상 종료 → 원래 세 파일 staging →
daemon-reload → 기존 도구의 모든 commit 검사 → 이전 수집본 기동** 순서다.
여러 줄 조회 보완은 검증한 root 사본을 재사용해 같은 문제로 원복 commit이 막히지 않도록 한다.

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/clock-runtime-deploy-2026-10-09/rollback_runtime.py --run
```

현재 기준의 `--validate`는 통과했다. **운영 원복은 실행하지 않았다.** 실제 원복에서 오류가 나면 결과·pending을
보존하고 단계별로 점검하며 재실행을 강행하지 않는다. 데이터·catalog·새 artifact·index·spool을 삭제하거나
과거 백업으로 덮어쓰지 않는다. 이전 CLI에 없는 시각 근거 조회에는 보존한 새 CLI를 사용한다.
원복하면 이전의 엄격한 세션 시작 정책도 돌아온다. 다른 boot에서는 이 절차를 자동 재사용하지 않는다.

T03은 이미 완료했으며 다시 열지 않는다. 기존 v1 로그·다른 8개 업무의 strict gate·T05·T10·T12는 별도다.
Chrony/시간원·RTC·Windows·재부팅·자동 삭제 재개는 이 적용에 포함하지 않는다.
