# T05 근거 생성 기능 운영 전환 상태

최신 후속: **10/12 첫 정기 사본의 완료/순서 근거·restic 보존·독립 복원은 확인 완료**했다.
[실제 근거](../t13-operating-completion-2026-10-12/t05-restored-evidence.json)를 따른다.
**10/13 00:32:58 T15에서 backup/restic의 manager·연결 설정을 시스템으로 이전**했다.
[이전 결과·기존 객체 9개/등록 이력 보존](../scheduled-system-deploy-2026-10-13/status.md)을 확인했다.
**10/13 첫 system 정기 backup의 완료/순서 연결도 확인 완료**했다. checkpoint `10dd2182…`의 완료 상한은
**14:26:29.724714 KST**, U 0.1382557355초이며 같은 baseline과 10/12 선행 사본을 유지했다.
restic `a2dbcdd4…` 저장·14:44 전체 검사와 지정 지점의 근거 객체 10개 조회/재구성을
[확인](../scheduled-system-operating-completion-2026-10-13/summary.md)했다. 그 시점의 신규 후행 사본은 **2개**다.
10/15 [후속 대조](../scheduled-clock-policy-2026-10-15/t05-observation/result.json)에서는 10/14까지 후행 사본 **3개**를 확인했다. 기간·당일 보존·새 삭제 계획의 통과와는 별개다.
10/17에는 T16 Decimal 수정본까지 [적용](../scheduled-clock-repair-deploy-2026-10-16/status.md)했다. T05의 정책·기존 baseline과 근거는 유지하며 새 wrapper의 정기 backup/restic 연결 확인은 남아 있다.
이 문서 아래는 10/11 적용 당시 기록이다. 당시 user drop-in 원복 절차를 현재 구성에 직접 적용하지 않는다.
자동 정리는 계속 masked이며 후행 사본·보호 기간·별도 삭제 승인 단계는 미완료다.

2026-10-11 **17:24 KST 확인**. **고정 실행본 설치·기존 7개 사본의 사후 근거 등록·backup/restic 두 ExecStart 전환 완료.**
새 정기 checkpoint의 완료 직후 근거 생성·restic 보존·복원은 아직 확인 전이며 **T05 전체는 미완료**다.

고정 후보는 **`t05-completion-evidence-20261011-c9d413cfe11ec0e7`**으로 유지했다.
[승인 범위](authorization.md), [설치 전 원래 계획](application.md), [실제 중단·재개와 현재 원복](recovery.md)을 함께 따른다.
Chrony·두 수집 supervisor·기존 시작 검사·예약·boot ledger는 유지했다. 운영 checkpoint 삭제와 추가 백업 요청은 0건이며,
사용자/시스템 자동 정리 service·timer 네 곳은 모두 `/dev/null` mask다. manual/recurring 승인도 비활성이다.

## 실제 완료 범위

| 단계 | 결과·근거 |
|---|---|
| 15:50 설치 전 시스템 미리보기 | 관리 대상 7개 보호·후보 0개. [원본 대조](../t05-completion-evidence-2026-10-11/system-preflight-review.json) |
| 16:20:55–16:21:02 관리자 설치 | `/opt` 소스 151개·비-editable 환경 1,646개를 검증본과 대조. root 소유·권한 확정 후 총 1,958개 파일/디렉터리/링크 확인. 일반 계정 import와 최종 registry 쓰기 확인. [root 결과 사본](root-receipts/result.json) |
| 기존 집합 등록 | 7개 ID/manifest의 baseline과 첫 유효 사후 상한 등록. available 7·missing 0, baseline 1개+checkpoint 근거 7개. [등록 결과](root-receipts/registration-result.json), [읽기 대조](root-receipts/registered-readback.json) |
| 16:22 첫 활성화 | 의존성 출력 순서를 변화로 오판해 중단. 정확한 두 새 drop-in은 제거됐지만 원복 확인도 같은 비교에서 실패. 원본 결과 보존 |
| 17:22:52 활성화 재개 | `After`/`Requires`만 구성원 기준으로 비교하는 보완으로 두 ExecStart 전환. 후보·설정·명령/시작 검사·보호 조건 유지. [재개 결과](activation-recovery-result.json) |
| 17:24 최종 조회 | 실제 두 명령·시작 검사·예약·147개 기준 경로 중 승인 대상 외 불변 항목·정리 mask·기존 근거 유지. Chrony/수집 PID와 invocation 동일. [대조 결과](postactivation-readback.json) |

root 원본은 `/var/lib/marketdata-t05-evidence-deploy-20261011/`에 있다.
[root-receipts/](root-receipts/)의 설치·등록·명령 기록 사본은 17:24에 원본 bytes와 다시 대조했다.
기존 설치 결과의 `installed_pending_user_entrypoint_activation` 및 첫 활성화 실패 결과를 성공으로 고쳐 쓰지 않았다.
실제 최종 상태는 별도의 activation recovery receipt와 연결한다.

baseline ID: `5ec056db51157f58a4537cabf374eee65148dfd901007c1138c1cba481b6cf0b`.
등록 plan ID: `0404b2e43594055456c5e9aae6cd1219b2199e2eccb1e82f4f2d9e9536d91ee3`.
설치된 disabled 정리 scope: `529cb1c08746e19c5d1ca8841cb80d8b3439c1040ffcfe021412d3e17b42a53c`.
등록 승인에는 삭제 권한이 없다.

## 사후 상한의 의미와 다음 확인

7개 기존 사본의 첫 완료 상한은 **10/11 16:21:02.643250–16:21:02.730247 KST**다.
원래 완료 시각을 바꾼 것이 아니라, 당시 검증된 완성 사본의 존재를 확인한 뒤 얻은 보수적 상한이다.
이 근거는 재평가 때마다 뒤로 미루지 않고 보존한다.

기간 조건만 보더라도 **현재 관측 하한**이 **10/14 16:22:02.643250–16:22:02.730247 KST**에
도달해야 72시간+60초가 성립한다. 표시된 wall clock이 그 시각을 지났다는 것만으로 통과시키지 않는다.
그때도 확실히 후행인 검증 사본 3개·데이터와 근거의 restic 보존·전체 검사·당일 완료·잠금·새 승인이 모두 필요하다.
지금은 삭제 후보를 다시 만들거나 정리 예약을 활성화하지 않았다.

- **10/12 13:10 KST 이후 다음 정기 backup:** 실제 완료 확정 뒤 관측, 같은 backup ID·manifest·생성 선후관계·sidecar를 확인한다. 예정 시각은 성공 보장이 아니다.
- **그 사본의 restic 저장·전체 검사 완료 후:** baseline/7개 사후 근거와 신규 완료 근거가 지정 snapshot에 실제 포함됐는지 확인하고, 별도 경로에서 복원 조회한다. 현재는 registry에만 등록된 상태이며 기존 10/11 snapshot에 소급 포함됐다고 보지 않는다.
- **정기 백업으로 조건이 실제 쌓인 뒤:** 새 삭제 없는 미리보기 → 한 계획의 별도 수동 삭제 승인/결과 확인 → 별도 반복 승인/시스템 timer 활성화를 진행한다. 조건을 앞당기는 추가 백업은 요청하지 않았다.

17:24의 restic start job `375611`은 기존 `marketdata-clock-ready.service`를 기다리고 있었다.
새 archiver 본 실행 성공으로 세지 않으며, 기존 엄격한 시각 gate를 우회하지 않았다.
새 backup/restic은 계속 **사용자 manager**에서 실행한다. 이번 시스템 유닛 설치는 정리 실행 환경에 한정한다.

## 검증 수와 배포 도구 보완

원래 전환 도구 **11개** 검사는 [deployment-validation.json](deployment-validation.json)에 보존했다.
실제 출력 회귀 **5개 + 기존 11개 재검사 = 16개 통과**는 [recovery-validation.json](recovery-validation.json)이다.
중복을 합쳐 27개의 서로 다른 검사로 표현하지 않는다. 고정 후보의 기존 215개/재현 58개는 반복하지 않았다.
의존성 추가/삭제·시작 검사 순서 변경·알 수 없는 drop-in·종료 제한 변경은 여전히 거부한다.
[원복 읽기 검증](rollback-readiness.json)은 통과했으며 실제 원복을 다시 실행한 것은 아니다.

원래 입력 17개와 [설치 입력](install-inputs.json)은 유지했다.
설치 입력 SHA-256: `43a8c8380b82953f1ec6775cef9323036d02efe611dea9e49641ce97dd64d271`.
보완 실행기 SHA-256: `4019db79e0c6adc32ba533e839fc68b1d4e3bdeb3d432ac6456fc8feab63f1cf`.
의존성 판독기 SHA-256: `9c25096f07e55b30ecd430f4d433a841e5fd89c81dd5f9e0395826cc0e26f8cf`.
