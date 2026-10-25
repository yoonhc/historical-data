# T18 출력 공유 수정본 — 최종 배포·재개 검토안

> **후속 상태 10/22 18:45 KST:** 아래 고정 operation은 사용자 승인을 받았다. 적용 전 대조에서 10/22 정기 사본이 추가돼 목록이 8→9개로 바뀌었으므로 승인 조건대로 설치 전에 보류했다. 이 문서의 승인 전 설명은 당시 검토 기록이며, 고정 계획·도구는 수정하지 않았다. [현재 승인·보류 근거와 다음 경계](deployment-status.md).

**2026-10-22 관리자 사전 점검과 전환 후보 검증 완료. 운영 설치·설정 변경·timer 재개는 아직 하지 않았다.** 기존 긴급 공간 확보·현재 수집/pack 복구 완료와 과거 수집 공백 후속은 별개로 유지한다. 같은 관리자 시험을 다시 실행할 필요는 없다.

## 고정 대상

| 항목 | 식별자 |
|---|---|
| Operation | `89a04417859c59ce5941e7aed36f25eb00db766962977878732cbd23e575901d` |
| 소스 실행본 | `restic-output-20261022-585c3e3686704e5c` |
| 적용 도구 | `t18-output-operation-20261022-7bb526de6c7fbaef` |
| `handoff.py` SHA-256 | `d9cc290b7cefc52c7e35d5d9930da7dcc2829bc383691d4025729f97ec4032e9` |
| Operation JSON 바이트 SHA-256 | `50aab5c8d8ded456fb6dffb32aedef755739f09377258d3f96d628dfca65a733` |
| 현재 T05 scope | `311ab16f019b4473185c487b219430c1fab4e90c4257f813bacfa696b1ba29a5` |
| 변경 후 예상 T05 scope | `2875edd74ccef07cd4a1f09917d147ab0a73cde0a6921a5f3f7cd9930344ef83` |

[정확한 계획·전체 before/after·외부 환경 해시](operation-plan.json), [CLI](handoff.py), [전환 상태 처리](operation.py), [실제 호스트 연결](native_host.py), [요청 근거 대조](verify_requests.py)를 고정했다. Operation ID는 ID 필드를 제외한 canonical JSON의 SHA-256이며 JSON 파일 자체의 바이트 해시와 다르다.

업무 변경은 이전 설치 archiver에 적용한 두 파일뿐이다. `archive_backups.py`는 성공 dump/기대 해시 확인 뒤 helper를 호출하며, `archive_output.py`는 같은 성공 본문을 공유하고 파일·디렉터리 동기화 및 `..` 거부를 처리한다. [수정 범위·23/25개 기존 검사](../t18-output-durability-2026-10-22/summary.md)를 재사용한다. 조회 빈도·신규 저장·전체 읽기 검사·batch 3개·T05 완료 bundle 경로는 유지한다.

새 `/opt/marketdata/releases/restic-output-20261022-585c3e3686704e5c`에는 **137개 파일, 1,214,942바이트**의 소스만 설치한다. Python·의존성은 기존 고정 `/opt/marketdata/releases/scheduled-clock-policy-20261016-686bb70c9ee4ef11/preserve/.venv/bin/python`을 그대로 사용하고 해시를 검사한다. 245MB 가상환경 복제나 이동은 하지 않는다. **새 실행본은 이 기존 Python 환경에 의존하므로, 이후 구 실행본 정리에서 이 환경을 제거하면 안 된다.**

## 필요한 연결 변경과 별도 승인 범위

유닛의 실행 경로가 bootstrap guard에 등록돼 있어, archiver 경로 교체만으로 끝나지 않는다. 다음 **구성 6개**와 기존 boot ledger의 연결 전환이 필요하다.

| 경로 | 변경 |
|---|---|
| `/etc/systemd/system/marketdata-restic-archive.service` | Python·옵션을 유지하고 `policy/run_job.py` 인자 경로만 새 소스로 교체 |
| `/etc/marketdata/scheduled-system/topology.json` | 위 archiver ExecStart만 교체 |
| `/etc/marketdata/clock-bootstrap.json` | 위 두 파일의 guard 해시만 교체 |
| `/etc/marketdata/scheduled-clock-policy/clock-binding.json` | bootstrap 파일 해시 연결 |
| `/etc/marketdata/checkpoint-clock-retirement/clock-binding.json` | 같은 bootstrap 파일 해시 연결 |
| `/etc/marketdata/checkpoint-clock-retirement/recurring.json` | 정책·삭제 대상 범위는 그대로, 새 binding에 따른 정확한 scope와 이번 명시적 승인 연결 |

다섯 정적 파일의 after SHA-256은 계획에 고정했다. recurring은 먼저 `enabled=false`, 두 승인 필드 null인 정확한 제안으로 닫고, 실제 UID 1000의 **전체 `propose`가 검토한 값과 일치한 뒤** 활성화한다. 새 authority의 수락 시각은 최초 한 번만 기록한다. 활성 grant 바이트는 고정 제안 + 그 시각 + 고정 operation authority 경로에서 유일하게 결정되며 재실행 때 새로 승인하지 않는다. 기존 활성 grant를 다른 scope로 자동 승계하는 승인은 아직 없다.

Boot ledger는 기존 boot ID·`closed`·보정 횟수·기존 관측·이력을 보존한다. 전환 동안 schema 2 pending으로 기존 guard를 닫고, 검증 후 profile 식별자와 구성 이력만 연결한다. 원복에서도 과거 ledger 파일 전체를 덮어쓰지 않고 원복 이력을 추가한다. T05 baseline·완료 상한·생성 순서·등록 객체는 변경 대상이 아니다. 정상적인 신규 자료·근거 추가를 기존 근거 변경으로 취급하지 않는다.

공유 binding은 8개 업무의 요청 정체성에 포함되므로, **8개 업무와 T05의 총 9개 timer**를 전환 중 runtime stop 대상으로 제한한다. Archiver는 이미 정지 상태이므로 새 정지는 나머지 7개 업무와 T05의 예약 보류다. 유닛 파일·enable 링크·mask·예약 주기는 바꾸지 않고 이전에 active였던 다른 timer만 복원한다. [실제 9개 timer의 중지 전파 의존관계 없음](timer-dependencies.txt)을 확인했으며 적용 직전 다시 검사한다. 이 일시 보류와 T05의 파생 scope 승인은 최종 운영 승인 범위에 **명시적으로 포함돼야 한다.**

## 실제로 끝난 검증

| 검증 | 근거·한계 |
|---|---|
| 실제 관리자 시험 | **10/22 08:13:15–18 KST 통과.** [원본](system-preflight-result-c044d2ddabfe4623.json), [시험 도구 고정 해시](preflight-seal.json) |
| UID 1000·보호 환경 | 기존 Python·새 소스 import·읽기 전용 실제 Chrony 조회 확인. 현재/예정 설정은 전용 namespace에서 대조했고 호스트 구성은 유지 |
| 실제 timer 출처 | 임시 timer 실행은 scheduled=true, 별도 수동 시험 invocation은 false. 실제 PID·invocation·start job 근거 연결 |
| 출력/receipt | 실제 hardlink/replace·fsync, 두 invocation의 동일 inode 공유, 이전 receipt와 출력의 원래 바이트 보존·구 계약 재검증 |
| 전체 T05 제안 | 현재 `311ab16f…`와 예정 `2875edd7…` 전체 객체가 각각 예상과 일치. 예정 grant는 시험 namespace에서 비활성 |
| 관리자 시험 정리 | 운영 전후 파일·ledger·보호 프로세스 동일, 임시 유닛 3개 not-found, 시험 경로 부재, cleanup 오류 없음 |
| 로컬 사전 검사 | [10개](preflight-local-tests.xml): overlay 범위·보호 옵션·manager 출력·변경 범위·소스·용량 기준 |
| 전환·중단·원복 | [42개](transition-tests.xml): 실제 임시 파일과 경계 예외 주입. 부분 구성, 승인 1회 확정, scope 불일치, 시작 발행 모호성, 새 기록 보존, 구성 전 원복, 원복 중 재중단·재실행 |
| 호스트 연결 검사 | [14개](native-adapter-tests.xml): 실제 파일 flock·경합 후 해제, activating/auto-restart/대기 job, timer PID 속성 부재, 미완료 요청 보존, 제어 대상·의존관계·공간 거부 |
| 현재 요청 대조 | [08:27:57 KST 읽기 전용 결과](request-review.json): 실제 설치 계약으로 8개 요청의 저장 근거·업무별 종결 확인. OI partial·품질 경고 및 daily의 `data_complete=false`를 정상 자료 완료로 바꾸지 않음 |

새 도구 관련 로컬 검사는 **66개(10+42+14)**다. 기존 helper 23/25개와 합쳐 독립 사례 수로 부풀리지 않는다. 시스템 manager 제어 자체는 관리자 시험에서, 실제 운영 구성 변경 순서는 임시 파일과 manager 대역에서 확인했다. **운영 배포·새 구성 재부팅·정전 복구를 완료했다는 결과가 아니다.**

## 적용·보류·원복 순서

1. 정확한 operation 승인·boot·현재 구성/권한·보호 입력·기존 archiver 요청·유휴/잠금·현재 사본 목록·가용 공간을 확인한다. 다른 상태를 승인한 것으로 간주하지 않는다.
2. root 소유 도구/소스 사본을 고정한 뒤 최종 `/opt` 소스를 설치한다. 기존 Python 환경 해시와 UID 1000의 최종 import·읽기 전용 CLI를 확인한다. 실패하면 업무 timer를 보류하기 전에 멈춘다.
3. 이전 timer 상태를 저장하고 신규 예약만 보류한다. 본문·재시작 대기·발행 job의 정상 종료를 최대 600초 기다린다. **대기 동안 coordinator 잠금을 잡지 않는다.** OI의 `auto-restart`도 유휴로 인정하지 않는다. 600초 초과는 강제 종료가 아니라 timer가 보류된 상태의 종료 75다.
4. 기존 8개 request lock·T05 runner lock·coordinator lock을 비차단 방식으로 잡고 다시 유휴·종결 요청을 확인한다. 잠금 경합·미완료 요청이면 보류하며 요청·pins·예산을 삭제하거나 초기화하지 않는다. 저장 근거는 UID 1000의 설치 계약으로 다시 확인한다.
5. pending ledger → 비활성 recurring → 고정 구성 5개 교체 → daemon-reload/실효 명령 대조 → 실제 전체 propose 대조 → 정확한 활성 recurring → 완료 receipt → `closed` ledger 순서다. 불일치·중단 상태는 기록하고 승인한 before/after 밖의 파일을 덮어쓰지 않는다.
6. 잠금을 해제한 뒤 읽기 전용 진입 검사·공간을 다시 확인한다. 이전 active timer들을 복원하고, **archiver는 timer만 시작**한다. `OnActiveSec=5s`·`Persistent=true` 때문에 빠른 실행 요청이 생길 수 있다. 직접 service start는 없다. 다음 예약 값과 실제 시작 발행을 기록한다.
7. 시작 intent는 남았지만 발행 여부가 모호하면 자동 start를 반복하지 않는다. `check`의 operation state·실제 timer invocation과 journal을 대조해야 한다. 같은 apply는 관측 가능한 완료/중단 단계만 이어가며 stop/rollback 이후 재활성화에는 새 검토가 필요하다.

`stop`은 archiver timer 신규 요청을 막고 해당 업무의 정상 종료를 기다리는 조치다. **구성 원복은 아니다.** 부분 전환 중 stop했다면 pending ledger/다른 timer 보류는 `check`로 확인하고 rollback 또는 별도 복구 검토가 필요하다. 실행 중 업무를 죽이거나 reset-failed하지 않는다.

`rollback`은 먼저 예약을 보류하고 정상 종료·종결 요청을 확인한 뒤 원래 5개 파일·원래 recurring을 복원하고 ledger 원복 이력을 추가한다. 구성 변경 전 중단이라면 기존 ledger 이력은 그대로 두고 이전 timer 상태만 복원한다. **Archiver timer는 구버전 출력 누적을 재개하지 않도록 정지 상태로 끝낸다.** 다른 이전 active timer와 T05 자동화는 복원하며, 새 소스·body·명령 receipt·request·자료·원장·T05 근거는 남긴다. 새 scope의 미완료 request가 있으면 지워서 원복하지 않고 해당 요청의 명시적 해결이 먼저 필요하다.

## 공간과 재개 후 완료 기준

08:27:57 KST `/home` 가용량은 **15,814,721,536바이트(약 15.81GB)**다. 24개 기존 snapshot의 마지막 실제 manifest 출력과 미보관 두 사본을 기준으로 첫 공유 body는 **314,548,669바이트**, 가장 큰 명령 임시 stdout은 **16,983,273바이트**다. 본문만의 합이며 새 run/JSONL·캐시·실패 출력·동시 수집 비용 전체를 0으로 보지 않는다.

기존 수집 보호 여유 + 위 두 항목 + 이번 재개 여유 **1GiB**를 합한 **12,142,692,006바이트**를 설치 전/재개 직전 요구한다. 1GiB는 이번 작업창의 추가 여유이며 수집기의 기존 보호 기준이나 영구 로그 정책 변경이 아니다. 현재 8개 로컬 manifest 경로 목록이 바뀌면 최신 크기로 계획을 재검토한다. 전체 136GB 분류나 같은 디스크의 대형 조사 사본은 만들지 않았다.

재개 후에는 다음을 각각 확인한다.

- **설치·연결·timer 재개:** 최종 소스·실효 유닛·scope·ledger·보호 입력·timer 시작/다음 예약과 root receipt.
- **미보관 두 사본의 편입:** 10/20 backup ID `a284b57feecbb18aa4f3e344d572f886b6877d7032de8cee82fcc7b845c38387`, 10/21 `ee64631d41bbad436fc9a24fe589d1ee161f9a143328a5863186254f9cc5c0ed` 각각의 정확한 snapshot·기존 전체 검사·T05 bundle·request 종결. 이후 신규 사본과 구분하고 batch 한도를 유지한다.
- **자연 no-op의 증가량:** 같은 snapshot 집합의 다음 자연 평가에서 새 dump/검증 사실은 receipt로 확인하고 큰 본문 추가 할당이 반복되지 않는지 측정한다. 최초 body 생성 비용·작은 기록 증가·공유 stdout mtime을 구분한다.
- **운영 유지:** 실제 수집 저장·시각 근거와 가용량을 작은 기록으로 확인한다. T05는 기존 조건으로 다음 예약에서 평가하며, 확인용 backup·service 수동 시작·삭제·전체 복원을 추가하지 않는다.

과거 출력 전체 정리·일반 로그 기간/총량·조회 주기 조정·보조 기록 독립 복구·과거 수집 공백은 [TODO](../../../TODO.md)의 후속이다. 이번 배포의 새 선행 조건으로 합치지 않는다.

## 별도 승인 후 사용할 명령

아래는 실행 방법이며 **아직 운영 승인을 받거나 실행하지 않았다.**

```sh
sudo /usr/bin/python3 -B -I \
  /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t18-archive-deploy-2026-10-22/handoff.py \
  --action apply \
  --approve-operation 89a04417859c59ce5941e7aed36f25eb00db766962977878732cbd23e575901d
```

읽기 전용 점검은 같은 도구의 `--action check`다. 승인된 작업창에서 중지/원복은 동일 operation을 지정하고 `--action stop` 또는 `--action rollback`을 사용한다. root 감사 기록은 `/var/lib/marketdata-clock/t18-output-handoffs/<operation>/`에 남는다. `check`나 종료 75를 보관/복구 완료로 표시하지 않는다.
