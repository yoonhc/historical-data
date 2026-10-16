# T16 관리자 시험과 이후 인계의 경계

이번 [고정 입력](tool-seal.json)은 **시험 환경과 격리 구성 전환**을 위한 것이다. [비활성 승인](approval.template.json)은 운영 승인으로 사용할 수 없다. **10/16 21:32 KST 관리자 시험은 [완료](system-preflight-result-48167a9e9eb24fdd.json)**했고 아래 명령을 반복할 필요는 없다. 이후 실제 system→system 인계 실행기/외부 계획은 [최종 운영 승인안](../scheduled-clock-deploy-2026-10-16/deployment-review.md)에 별도로 고정했다. 운영 적용은 아직 미승인·미실행이다.

## 관리자 시험 명령

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/scheduled-clock-handoff-2026-10-16/system_preflight.py --run
```

시험 전 고정 입력, 같은 boot, 현재 구성/ledger, 네 정리 mask를 다시 확인한다. 다르면 시험 생성 전에 중지한다. 검사를 제거하거나 후보 입력을 자동 재생성해 통과시키지 않는다. `--run`이 없으면 입력 검사만 수행한다.

실제 업무의 원래 데이터 인수를 그대로 실행하지 않는다. 임시 저장소/외부 대역을 쓰면서 설치 모듈·wrapper·기존 CLI의 저장/결과 계약을 시험한다. 별도의 새 waiter만 현재 Chrony를 읽기 전용으로 조회한다. 45초는 이 전용 waiter 시험의 상한이며 운영 waiter 무기한 보류 정책을 바꾸는 값이 아니다. 업무 시험의 Restart는 no로 제한해 실패 후 반복 시험이 생기지 않게 한다. 업무별 timeout/kill/protection은 최종 값 그대로다.

결과 JSON에서 `passed`, 각 업무/JUnit, 두 invocation의 동일 request 복구, system timer/수동 attribution, cleanup, `operating_unchanged`를 함께 확인한다. 일부 단계 실패를 전체 성공으로 바꾸지 않는다. 필요하면 실패 원인을 수정한 별도 도구 해시로 남은 범위만 다시 검증한다.

## 운영 인계의 요구 조건과 구현 연결

아래 조건은 새 실행기에 연결했다. 실제 명령·해시·원복 경계는 위 최종 승인안이 기준이다. service 파일도 guard에 포함되므로 **본문 종료 대기 동안에는 timer만 보류하고 service 파일/closed ledger를 유지**한다. pending을 먼저 확정한 뒤 임시 service mask를 설치한다.

1. 현재 구성·boot·입력 해시와 소유권, 최종 `/opt` 설치/import, 사용자 mask·시스템 timer 링크, 준비와 T05 실행/잠금을 확인한다. 완료된 기존 기준 목록·시각 상한·순서 근거는 다시 등록하지 않는다.
2. 별도 handoff lock으로 중복 실행을 막고, 기존 timer의 새 시작을 보류한다. 8개 업무의 실제 PID/control PID/job/재시작 상태를 분류한다. 실행 중 본문은 정상 종료를 기다리고, 시작 대기는 원래 요청을 기록한 뒤 승인된 범위에서만 취소한다. 시간 초과는 강제 종료가 아니다.
3. **coordinator 배타 잠금 없이 drain**한다. body가 끝나기 전에 기존 Requires waiter를 중지하지 않는다. collector도 쓰는 기존 strict waiter는 이번 업무 이전만으로 제거하거나 새 정책으로 바꾸지 않는다.
4. drain 후 같은 request.lock 아래 미완료 요청을 읽는다. 초기 T16 적용이라도 state가 없다고 가정하지 않는다. 다른 identity, 원래 고정 날짜/범위, 저장 결과와 재개 필요를 확인한다. 불명확하면 보류하며 request 삭제/자동 변환은 하지 않는다.
5. 짧은 coordinator 배타 구간에 pending → 13개 정확한 구성 변경을 수행한다. 실제 reload와 명령/보호 옵션을 대조한 뒤 commit한다. 준비 여부와 구성 적용 여부는 별도로 기록한다. 반쪽 구성에서는 새 시작을 허용하지 않는다.
6. 기존 timer 예약·링크는 유지하면서 재개하고, 취소한 요청의 발행/대기/처리 여부를 대조한다. `complete` 인계 receipt는 구성 적용/요청 인계를 뜻하며 모든 데이터 완료로 해석하지 않는다.

## 원복과 재적용

원복 대상은 실행 경로/유닛/guard/binding/현재 ledger의 구성 연결이다. 데이터·카탈로그·T13 원장·T05 baseline/완료 상한/생성 순서·request/attempt/JSONL은 보존한다. 원복하면 이전 strict/reference-age 대기도 돌아온다.

미완료 request가 있으면 원래 작업이 이미 저장됐는지와 이전 runtime에서 무엇을 실행할지 명시적으로 대조해야 한다. old runtime이 새 request를 이해한다고 가정하지 않는다. 같은 후보로 다시 적용할 때는 새 계획으로 구성/ledger를 연결하고 원래 request identity/저장 근거로 복구한다. 새 release/config가 다르면 자동 승계하지 않는다.

latest가 후속 관측으로 바뀌어도 원래 OI fetch/JSONL 구간이 보존돼 있으면 원래 요청을 종결할 수 있다. 원래 기록 누락/변조·모호한 receipt는 계속 거부한다. 파일 원복만으로 미전달 요청까지 처리됐다고 기록하지 않는다.

## 적용 이후 별도 확인

운영 승인을 받은 후에만 8개 업무의 실제 처리/시각 보류·재개, 새 system timer restic 출처, 다음 정기 backup/T05 연결, 같은 collector supervisor의 자연스러운 다음 child를 확인한다. 미발생 장애를 만들거나 과거 전체 복원 시험을 반복하지 않는다. 현 단계는 실제 운영 적용·새 구성 재부팅 시험이 아니다.
