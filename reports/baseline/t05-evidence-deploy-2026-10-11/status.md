# T05 근거 생성 기능 운영 전환 상태

**10/18 22:59:39 KST 시스템 반복 자동화 활성화 완료·23:01:39 사후 대조 통과:** 승인 operation `2c03a81f…`와 후보 `e57a7995…`의 최종 설치 1,798개·도구 7개·after 8개·UID 1000 실행/guard·전체 proposal·scope `311ab16f…`·활성 grant `3b436c4f…`를 대조했다. timer만 한 번 시작했으며 **다음 실제 예약은 2026-10-19 15:40 KST**다. 사용자 두 mask·manual 비활성·기존 근거 24개/보호 입력 50개·Chrony/두 supervisor를 유지했다. 설치·반복 승인·활성화는 완료이고, **첫 timer 평가와 첫 자연스러운 자동 삭제/보존은 미실시·관찰 대기**다. 완료된 명령을 반복하지 않는다. [최신 실제 receipt·사후 감사·남은 관찰](../t05-recurring-deploy-2026-10-18/status.md). 아래는 각 시점의 과거 기록이다.

**10/18 반복 자동화 승인 수령·22:56 KST 적용 전 확인:** 최종 operation `2c03a81f…`의 설치·반복 권한·시스템 timer 활성화를 사용자가 명시적으로 승인했다. 고정 before/소스·보호 입력·기존 기록·유휴 대조는 통과했다. `sudo -n`은 인증 부재로 실행기 시작 전에 종료됐고, 후속 대조에서 새 설치/authority 부재와 네 mask·비활성 grant·보호 프로세스 보존을 확인했다. **새 승인 대기가 아닌 관리자 터미널 실행 대기**다. 활성화 receipt와 실제 다음 예약은 아직 없으며, 설치/활성화·첫 timer 평가·첫 자연스러운 자동 삭제를 구분한다. [최신 승인·명령·근거·남은 범위](../t05-recurring-deploy-2026-10-18/status.md). 아래 기록은 각 시점의 준비·실행 결과다.

**10/18 최종 반복 전환 도구 검증 완료:** operation `2c03a81f…`, 도구
`t05-recurring-operation-20261018-447cddda543bc6ed`의 설치·활성화·중지·부분 원복을 준비했다.
새 경계 73개와 별도 경로 1,798개 복사/import/CLI가 통과했고, 22:38 KST 현재 호스트의
before·보호 입력·기존 근거·네 mask·프로세스를 읽기 대조했다. **운영 설치·반복 승인 활성화는 하지 않았다.**
이전 관리자 시험과 수동 삭제를 반복하지 않았으며, 정확한 operation의 별도 승인과
실제 최종 scope 확인·timer 활성화·첫 예약/자동 삭제 관찰이 남았다.
[최종 도구·근거](../t05-recurring-deploy-2026-10-18/summary.md),
[승인·중지·원복 범위](../t05-recurring-deploy-2026-10-18/operation-review.md).

**10/18 반복 자동화 준비:** 새 후보 `t05-recurring-retirement-20261018-e57a7995f03fb301`의
잠금 보류 경계 보완·61개 격리/도구 검증을 완료했다. 첫 root 시험은 일회성 임시 유닛 회수 뒤
수동 시작을 시도한 도구 오류로 실패했으며 운영 전후 동일·시험 정리를 확인했다.
시험 도구만 보완해 **14:34 관리자 재점검을 통과했다.** 실제 timer/수동 출처·보호 환경·비활성 승인
보류와 시험 정리를 확인했다. 당시 남았던 최종 도구 검증은 위 기록으로 완료했고, 운영 반복 승인은 남아 있으며,
운영 실행본·recurring 비활성·네 mask를 유지한다.
[후보와 남은 범위](../t05-recurring-candidate-2026-10-18/summary.md).

**첫 수동 결과 — 10/17 20:32 정리 완료·독립 대조 통과.**
재개 operation `821367d2…`, run `f57f334b…`가 원래 plan `31766af4…`의 10개를 33분 51초에 정리했다.
실제 원본/staging 경로 소멸, 보호 3개·제외 3개 manifest, 원래 plan/inventory·과거 실패·기존 근거 보존을 확인했다.
여유 공간은 관측상 약 28.20GB 증가했다. manual 원본 비활성 복원, recurring 비활성·네 mask와
보호 입력·Chrony/두 supervisor 신원도 유지했다. [완료와 남은 범위](../t05-mount-recovery-2026-10-17/completion.md).
**첫 수동 적용은 완료이며 반복 자동화·T05 전체는 미완료**다. 아래는 각 시점의 이전 진행 기록이다.

**10/17 18:47 mount 구성 적용 완료·정리 Busy 실패:** 승인 operation `eced87a4…`의 네 파일은 정확한 after다.
이어진 run `2d9f7b8b…`는 reload 뒤 5초 timer 요청으로 시작한 restic과 잠금이 경합해 삭제 0개로 종료됐다.
18:52 원래 journal 해시·10개 prepared·사본 16개·inventory 10개·기존 근거·보호 입력/프로세스·네 mask 보존과
manual/recurring 비활성을 확인했다. 설치와 재개를 분리한 새 operation `821367d2…`는 11개 격리 검사·19:07 실제
호스트 읽기 전용 대조를 통과했다. **한 번 재개의 명시적 승인을 받았고, 직전 대조도 통과했으나 sudo 인증 부족으로 관리자 터미널 실행 대기**다. 프로그램·서비스는 아직 시작하지 않았다. 새 도구는 reload 없이 유휴/잠금과 다음 예약 시각을 확인한다.
[최신 결과·정확한 ID·재개안](../t05-mount-recovery-2026-10-17/status.md).
기존 설치 명령을 반복하지 않는다. 첫 수동 정리·반복 자동화·T05 전체는 미완료다.

**10/17 17:15 실제 삭제 실행의 최종 결과:** 명시적 재개 run `5d7deaf5…`는 유지 사본 검사·후보 inventory 생성 뒤 첫 rename의 `EXDEV`로 실패했다. 삭제 0개·원래 사본 16개/manifest 보존·journal 전부 prepared·staging 비어 있음이다. manual/recurring 비활성·네 mask·기존 근거·프로세스 유지. 후속 실제 system 시험과 복구안 준비는 위 기록을 따른다. [실패 원본과 근거](../t05-first-manual-exdev-2026-10-17/status.md)를 보존한다. 아래는 실패 전 시점의 기록이며 이전 Busy 재개 명령을 반복하지 않는다.

**10/17 16:37 최신 결과:** 수동 유닛 한 개의 일반 파일 설치 보완과 실제 guard는 통과했다.
이어진 run `d1c40fd5…`는 같은 시각 실행 중인 restic 업무와 repository lock이 경합해 보류·삭제 0개로 끝났다.
manual은 원래 비활성으로 복원됐고, journal/staging/적용 inventory는 없으며 사본 16개·기존 근거·네 mask가 유지됐다.
기존 첫 수동 승인만으로는 설치된 unfinished-run guard를 통과하지 않으므로,
[같은 계획의 정확한 잠금 보류 재개안](../t05-first-manual-resume-2026-10-17/status.md)을 준비·검증했다.
명시적인 재개 승인 대기이며 첫 수동 삭제·반복 자동화·T05 전체는 미완료다. 아래는 이전 단계 기록이다.

**10/17 16:15:25 첫 수동 적용은 삭제 전 보류:** 실제 유닛 링크와 일반 파일을 요구하는 설치 guard의 불일치로
exit 75·삭제 0개, 시각 조회/사본 검사/삭제 journal 생성 전 종료됐다. manual 비활성 복원·기존 근거·프로세스·네 mask를 유지했다.
동일 내용 유닛 한 개의 설치 형식을 보완하는 [준비·검증](../t05-first-manual-recovery-2026-10-17/status.md)을 진행했고 이후 결과는 위 최신 기록을 따르며,
아직 첫 실제 삭제나 반복 자동화가 완료된 것은 아니다.

**10/17 15:49:30 현재 구성의 정식 미리보기 완료:** 당일 backup `9b916470…`와 restic `0911b25d…` 저장/전체 검사 이후,
계획 `31766af4…`에서 후보 10개·보호 3개·등록 이전 제외 3개를 확인했다. 새 snapshot의 근거 객체 14개를 직접 읽어
완료 상한·선행 관계를 대조했고 기존 baseline·근거·scope·수집기/Chrony·네 mask는 유지했다.
유지 사본 전체 내용 검사는 적용 직전에 남아 있으며 삭제는 0개다. **정확한 계획의 수동 삭제와 반복 자동화는 별도 승인**이고 T05 전체는 미완료다.
[정식 결과·목록·원본·남은 검사](../t05-formal-preview-2026-10-17/status.md)를 따른다.

**10/17 14:12:23 비활성 scope 갱신 완료:** 승인 계획 `82d0f54a…`의 `recurring.json` 한 파일을 적용했다.
14:12:44 독립 대조에서 after `14523bb8…`·root:root/0644·`enabled=false`/승인 필드 null,
보호 입력 91개·기존 객체 13개와 등록 receipt·네 mask·Chrony/두 supervisor 신원을 확인했다.
기존 근거·보호 기간을 초기화하지 않았다. 정식 미리보기·삭제·T05 전체 완료는 아니다.
같은 시각 당일 backup은 `validate` 진행 중이며 같은 backup ID의 restic 저장/전체 검사 완료가 남아 있다.
[적용 결과·감사 기록·다음 단계](../t05-disabled-scope-2026-10-17/status.md)를 따른다.

**10/17 09:23–09:28 준비 당시:** 최종 T16 구성과 대조한 비활성 `recurring.json` 한 파일 갱신 계획 `82d0f54a…`를 고정했다.
격리 17개·실제 설치본의 임시 승인 입력 대조·읽기 전용 사전 점검을 통과했으며 당시에는 미설치였다.
[준비 결과와 설치·원복 절차](../t05-disabled-scope-2026-10-17/summary.md)를 보존한다.

**10/17 01:19 대조 당시:** 10/16까지 신규 후행 사본 **5개**, 최신 restic의 근거 객체 13개와 당시 U 약 0.140초를 확인했다.
시간·후행 관계만 보면 9개가 검토 대상이나, 당시 당일 backup/restic 완료와 비활성 scope 갱신이 남아 정식 미리보기·삭제를 보류했다.
[당시 결과·두 보류 사유](../t05-readiness-2026-10-17/summary.md)를 보존한다. 네 mask와 기존 상한·baseline은 유지했다.

최신 후속: **10/12 첫 정기 사본의 완료/순서 근거·restic 보존·독립 복원은 확인 완료**했다.
[실제 근거](../t13-operating-completion-2026-10-12/t05-restored-evidence.json)를 따른다.
**10/13 00:32:58 T15에서 backup/restic의 manager·연결 설정을 시스템으로 이전**했다.
[이전 결과·기존 객체 9개/등록 이력 보존](../scheduled-system-deploy-2026-10-13/status.md)을 확인했다.
**10/13 첫 system 정기 backup의 완료/순서 연결도 확인 완료**했다. checkpoint `10dd2182…`의 완료 상한은
**14:26:29.724714 KST**, U 0.1382557355초이며 같은 baseline과 10/12 선행 사본을 유지했다.
restic `a2dbcdd4…` 저장·14:44 전체 검사와 지정 지점의 근거 객체 10개 조회/재구성을
[확인](../scheduled-system-operating-completion-2026-10-13/summary.md)했다. 그 시점의 신규 후행 사본은 **2개**다.
10/15 [후속 대조](../scheduled-clock-policy-2026-10-15/t05-observation/result.json)에서는 10/14까지 후행 사본 **3개**를 확인했다. 기간·당일 보존·새 삭제 계획의 통과와는 별개다.
10/17에는 T16 Decimal 수정본까지 [적용](../scheduled-clock-repair-deploy-2026-10-16/status.md)했다. T05의 정책·기존 baseline과 근거를 유지한 새 wrapper의 정기 backup/restic 연결은 [당일 결과](../t05-disabled-scope-2026-10-17/backup-archive-status-20261017.md)와 위 정식 미리보기에서 확인했다.
이 문서 아래는 10/11 적용 당시 기록이다. 당시 user drop-in 원복 절차를 현재 구성에 직접 적용하지 않는다.
자동 정리는 계속 masked다. 후행 사본·기간·비활성 scope·당일 보존·정식 미리보기를 확인했으며, 수동 적용 전 검사와 별도 삭제/반복 승인은 남아 있다.

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
