# T18 승인 후 적용 전 대조 — 사본 목록 변경으로 보류

2026-10-22 18:45 KST. 사용자가 operation `89a04417859c59ce5941e7aed36f25eb00db766962977878732cbd23e575901d`의 배포·연결 전환·timer 재개를 명시적으로 승인했다. 승인에는 **현재 목록·입력이 달라지면 보류**한다는 조건이 있다. 현재 대조에서 이 조건에 해당해 **설치 전에 보류**했다. 운영 apply를 호출하거나 고정 계획을 수정하지 않았다.

## 보류 근거

- 계획의 로컬 checkpoint manifest 목록은 8개, 18:33 현재는 9개다. 기존 경로 누락은 없고 정상적인 10/22 정기 사본 하나가 추가됐다. 기존 baseline·완료 상한·생성 순서의 변조로 판단한 것이 아니다.
- 추가 경로: `/mnt/backup/marketdata-historical/automatic/2026-10-22/9591d25d5a7a4d18af7769da3c3380ed/backup.json`.
- 새 backup ID: `b3f6573541bbba432fb2218678350596001aa5da73594d82b040c9af0b5aa298`. Manifest의 완료 시각은 **10/22 14:59:40 KST**, 크기는 **15,272,331바이트**, SHA-256은 `94075b7ef8349bc5e69b9a3bffa9f5a26a72afe9032c20e6e07bef84ac74d1bc`다.
- 기존 10/20·10/21 두 사본과 새 10/22 사본 모두 현재 archive state에 저장 snapshot이 없다. 미보관은 **3개**이며, 이 조회는 로컬 기록 대조다. 새 restic 조회·전체 읽기 검사를 실행하지 않았다.
- 18:45 가용량 **15,534,739,456바이트(약 15.53GB)**로 승인 계획의 요구량 **12,142,692,006바이트**는 통과한다. 추가 manifest만 포함한 검토용 계산은 첫 body **329,821,000바이트**, 최대 임시 stdout **16,983,273바이트**, 요구량 **12,157,964,337바이트**다. 약 3.38GB 여유가 있다. **이 계산은 새 승인이나 기존 계획 변경이 아니다.**

원본: [18:33 구성·공간·목록](preapply-files-183312.json), [18:45 추가 사본·미보관·용량 차이](preapply-hold-20261022.json).

## 유지 상태와 검사의 한계

Operation JSON 바이트 SHA-256 `50aab5c8d8ded456fb6dffb32aedef755739f09377258d3f96d628dfca65a733`과 handoff SHA-256 `d9cc290b7cefc52c7e35d5d9930da7dcc2829bc383691d4025729f97ec4032e9`은 승인값과 같다. 현재 boot·5개 정적 구성 before·recurring 원본·boot ledger·계획의 보호 파일/링크는 유지됐다. 실제 호스트의 구성 6개와 ledger는 root:root/0644다. [호스트 권한](preapply-native-metadata.txt).

[실제 manager 조회](preapply-manager-1832.txt)에서 archiver service는 유휴·job 없음, timer는 inactive/enabled였다. 나머지 8개 timer는 active, 중지 전파 의존관계는 비어 있었다. Chrony·두 collector supervisor의 PID/invocation은 고정값과 같았다. OI service의 `activating/auto-restart`는 유휴로 취급하지 않았다. [설치 계약의 요청 대조](preapply-request-proofs-1832.json)는 8개 요청의 저장 근거와 종결을 확인했으며, OI partial·품질 경고·daily 미완료 자료를 자료 성공으로 바꾸지 않았다. 이 기록은 순간 관찰이며 적용 직전 잠금 아래 검사를 대신하지 않는다.

새 `/opt` 실행본·공유 body 디렉터리는 아직 없었다. timer 보류/복원, service 시작/정지, configuration·grant·ledger 변경, root authority/적용 receipt 생성은 하지 않았다. 최종 경로 UID 1000 import·CLI, 전환 잠금, 설치 후 전체 propose는 이 보류 지점 이후의 미실시 항목이다. 도구 세션의 native sudo 인증도 없지만 **이번 보류 원인은 인증이 아니라 명시된 사본 목록 불일치**다. 현재 계획의 터미널 apply 실행을 요청하지 않았다.

## 다음 경계

기존 고정 operation과 승인 기록은 보존한다. 다음 적용 검토는 새 10/22 사본을 포함한 정확한 목록·공간 기준으로 operation을 다시 고정해야 한다. 코드·정책·scope·검증 빈도·batch 한도를 바꾸거나 전체 관리자 시험을 반복할 근거는 없다. 새 operation을 기존 승인으로 자동 실행하지 않는다.

설치·재개, 미보관 편입, 같은 snapshot 집합의 자연 no-op 효과는 **모두 미완료**다. 기존 두 사본의 관찰 요구는 유지하고 새 사본을 별도로 연결한다. 기존 공간 확보·현재 수집/pack 복구 완료와 과거 공백·장기 보관/조회 개선 후속은 그대로다. [TODO](../../../TODO.md).
