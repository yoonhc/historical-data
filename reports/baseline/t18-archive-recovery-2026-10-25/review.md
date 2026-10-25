# T18 빈 job 출력 처리·기존 operation 재개 검토안

**현재 상태 갱신:** 이 경로는 [별도 승인](approval-record.json) 뒤 15:43 실제 실행됐으나 `KeyError: 'ExecStartPre'`로 실패했다. 파일 5개 after·비활성 after grant·pending ledger·9개 timer 보류이며 완료된 전환이 아니다. 이 명령을 반복하지 않는다. [원본 실패를 보존한 후속 대조와 두 번째 보완 검토](../t18-archive-manager-recovery-2026-10-25/review.md)로 연결한다.

아래는 첫 복구 승인 당시의 고정 검토 내용이다. 원래 operation `27dd5101bfa0b7005ce0a931b075bc9cccad5a5331e9350a30c4765cbf1ee225`의 소스 설치/최종 UID 1000 import는 완료했지만, 9개 timer 보류 뒤 job 목록 해석에서 실패했다. 당시 구성 5개·recurring(합계 6개)와 boot ledger는 before였으며 transaction·apply receipt는 없었다. 다른 8개 timer와 archiver timer가 모두 inactive였고 두 수집기·Chrony는 유지됐다.

## 원인과 최소 변경

이 호스트의 성공한 `systemctl list-jobs --output=json --no-pager`는 job 0개에서 빈 stdout을 반환한다. 기존 adapter가 `json.loads("")`로 실패했다. [실제 원문·구성 대조](original-failure-diagnosis.json). 원래 apply와 rollback 양쪽이 같은 함수에 연결되므로 동일 명령을 반복하지 않았다.

보완한 `native_host.py`는 **명령 성공 뒤 빈 출력만 빈 job 목록으로 처리**한다. 비어 있지 않은 잘못된 JSON/행 구조·명령 실패·실제 T05 job·실행 중 PID는 계속 거부한다. source 실행본·업무 정책·원래 operation engine은 변경하지 않는다.

원래 작업공간과 root의 tool seal, operation JSON, authority, 실패 attempt를 수정하지 않는다. 별도 `repair_resume.py`를 root에 봉인해 **동일 operation의 기존 state와 authority를 읽고**, 보완 adapter로 기존 engine을 실행한다. 최초 진입에서 원래 `timers_held` state·authority·설치 import·실패 attempt 해시와 구성 전환 미시작을 검사한다. 같은 handoff lock을 사용하며, 기존 request/coordinator 잠금·정상 종료·용량·propose·모호한 start 거부도 유지한다.

## 정확한 보완 식별자

| 항목 | 값 |
|---|---|
| Recovery ID | `a277ae71a27b2ed095b299b1244b16df7fa7394f57898abcf2bd2b5c490aa59e` |
| Recovery JSON 바이트 SHA-256 | `87e2f7c4261729e75c46723ec31425c019341695b7b4eb0d681bdbee9282c539` |
| 보완 도구 ID | `t18-job-query-recovery-20261025-06fcf27fedd346f7` |
| 보완 entrypoint SHA-256 | `079c91d89d8dfb5e39952fc47b9abcae3dbec0aafec87bd28562f65d961ba4cf` |
| 보완 native adapter SHA-256 | `080d7782bf3e71aee84526cd8ee803563f0e6ff6564cd50a0183cb84504b08f5` |
| 보존할 원래 authority SHA-256 | `5e501e421bb7ad14b43abc5329c123130c72a1f571c0a343a98ac4643caadae4` |

[전체 보완 계획](recovery-plan.json) · [정확한 root 경로](artifact-paths.json). 원래 source `restic-output-20261022-585c3e3686704e5c`, 전체 구성 before/after, manifest 12개/미보관 6개, after scope `2875edd74ccef07cd4a1f09917d147ab0a73cde0a6921a5f3f7cd9930344ef83`는 그대로다. 원래 T05 authority의 승인 시각·바이트를 다시 만들지 않는다. 별도 복구 승인 수락 기록은 새 recovery 디렉터리에만 남긴다.

## 확인한 범위

[관련 18개 검사](recovery-tests.xml): 기존 빈 출력 오류 재현, 빈/정상 응답, 오류·실제 대기 job·활성 oneshot 거부, 정확한 복구 진입 경계, timers_held에서 동일 authority로 재개·8개 timer만 원복·기록 보존·중복 시작 거부. 기존 66개와 관리자 사전 시험은 변경하지 않았고 전체 재실행하지 않았다.

[15:37 실제 호스트 읽기 전용 대조](native-job-probe.json): 보완 idle 경로가 실제 성공/빈 출력에서 통과했다. 9개 timer 보류와 서비스 유휴, 소스/기존 Python·보호 입력·8개 request 종결·구성/ledger/recurring before·authority를 확인했다. 이것은 root 적용 시험이나 실제 전환 성공이 아니다.

## 승인 후 선택할 실행

원래 목표를 이어가는 재개:

```sh
sudo /usr/bin/python3 -B -I \
  /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t18-archive-recovery-2026-10-25/repair_resume.py \
  --action resume \
  --approve-recovery a277ae71a27b2ed095b299b1244b16df7fa7394f57898abcf2bd2b5c490aa59e
```

이번 작업창을 접고 이전 예약을 복원하는 경우에는 위 명령의 `--action resume`을 **`--action rollback`**으로 바꾼다. 이 경우 **이전 active 8개 timer와 기존 T05 승인/구성을 복원하고 archiver는 정지**한다. 원래 operation은 rollback으로 닫혀 같은 승인으로 다시 활성화하지 않는다. 새 실행본·보고서·원장·자료는 삭제하지 않는다.

재개를 선택하면 기존 계약대로 비활성 recurring → 구성/ledger 전환 → 전체 propose 일치 → 기존 authority로 승인 연결 → 잠금 해제 → 준비/공간 재검사 → 이전 active 8개 timer 복원 → archiver timer 시작을 수행한다. 실제 service를 직접 시작하지 않는다. 실패/보류라면 마지막 state와 timer 상태를 보고하며 자동 반복하지 않는다.

원래 고정 승인에는 이 보완된 **도구 바이트**가 포함되지 않으므로, 새 경로의 운영 실행은 별도로 확인받는다. 새 시각/보존 정책이나 신규 백업 요구는 없다. 설치/재개 완료, 미보관 6개 편입, 자연 no-op의 출력 증가 억제는 계속 각각 확인한다.
