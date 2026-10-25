# T19 제한된 과거 출력 공간 회수 완료

**2026-10-25 20:13:03 KST, 승인한 1,022개 교체가 한 번의 apply로 완료됐다.** 두 원본과 전체 1,024개 경로·내용·권한·receipt는 보존했다. 20:13–20:15 사후 대조도 통과했다. 이 묶음의 공간 회수는 완료이며 같은 apply/resume 또는 전체 검증을 반복할 필요가 없다. 전체 과거 출력 정리와 장기 관리까지 완료한 것은 아니다.

## 승인과 실행 연결

| 항목 | 값 |
|---|---|
| 승인 operation | `7ce8007352fb195b713f2efc067cf5712c453a826dc6d8d96657f3f49c30eb65` |
| Operation JSON SHA-256 | `98f9cb59adc39581bfa3d3d6c32379e686b4580b270574a21c4c56c1d141b3e4` |
| apply attempt | `f78d8cd6d57e4f60b8f4f679b6eec5b5` |
| 사용자 승인 | 최종 검토안의 정확한 operation·동일 범위 재개 질문에 “네” |
| authority 수락 시각 | **20:06:37.419242 KST**, UID 1000, 한 번 기록 |
| authority SHA-256 | `809c4625734859c0d0ebb8bf3daedd657b046f50fd8024b183a81d5dc38670e9` |
| 실행 | 20:06:37.419095 → **20:13:03.122124 KST**, 약 6분 26초 |
| 결과 | `passed=true`, `state=complete`, 종료 코드 0, 교체 **1,022개** |

[고정 검토안](review.md)·[operation](operation.json)·[승인 연결](approval-observation.json)·[authority](audit/authority.json)·[실제 attempt](audit/f78d8cd6d57e4f60b8f4f679b6eec5b5.json)·[완료 receipt](audit/complete.json)·[교체 전후 JSONL](audit/f78d8cd6d57e4f60b8f4f679b6eec5b5.jsonl).

20:05의 자연 archiver 실행이 진행 중이어서 먼저 기다렸다. 서비스가 PID/job 없는 유휴로 돌아온 뒤 고정 도구를 실행했고, timer는 한 번도 정지·재시작하지 않았다. 실행 중 새 예약과 경합하지 않았으므로 **이번 운영 적용이 실제 lock_busy 재개까지 발생시킨 것은 아니다.** 해당 경계는 앞서 고정한 격리 시험 근거를 유지한다.

## 정확한 처리와 공간 결과

| 확인 | 실제 결과 |
|---|---|
| 승인 대상 | 서로 다른 독립 출력 **1,022개** 교체, 두 유지 원본의 inode 유지 |
| 전체 경로 | **1,024개** 유지, 두 inode의 link 수 각 **512** |
| 내용/권한 | 적용 전 선택 바이트 및 교체 직전 바이트 대조, 최종 공유 본문 해시와 경로/inode 대조 통과. UID/GID 1000·0600·xattr 유지 |
| command/run receipt | 적용 전 **1,568개**, 적용 후 **1,568개** 해시 일치 |
| 교체 감사 | 정확한 대상별 before/after **1,022쌍**. 순서 일치·추가 대상 없음 |
| 중간 상태 | 임시 링크 **0개**, 재개할 미완료 교체 없음, archive 잠금 해제 확인 |
| 선택 집합의 중복 없는 할당 | **8,629,833,728 B → 16,859,136 B** |
| 선택 집합의 할당 감소 | **8,612,974,592 B**, 약 **8.61GB / 8.02GiB** |
| `/home` 실제 가용 변화 | **15,947,558,912 B → 24,554,389,504 B**, 약 **15.95GB → 24.55GB (22.87GiB)** |
| 가용량 증가 관측 | **8,606,830,592 B**. 감사 파일·동시 업무 등을 포함하므로 위 선택 집합 회수량과 구분 |

정확한 두 snapshot/backup ID·기대 manifest SHA·원본 inode·512개 연결과 작은 사후 대조는 [post-application-audit.json](post-application-audit.json)에 있다. 전체 경로와 교체 전 metadata는 변경하지 않은 [원래 선택 목록](../archive-capacity-planning-2026-10-25/verified-selection.json)을 따른다.

원래 출력 이름과 내용은 그대로다. inode·link 수·ctime이 바뀌고 mtime은 유지 원본과 공유되며, 명령 실행 시각은 원래 receipt로 판단한다. 이 작업은 stdout **할당 공유**다. 시장 자료·checkpoint·restic snapshot/pack을 삭제한 작업이 아니다.

적용 과정의 선택 출력 논리 읽기는 25,866,463,060 B였다. 사후 [observer](observe_result.py)는 **본문을 다시 읽지 않고**, 완료 receipt·정확한 경로 metadata·JSONL·작은 상태만 사용했다. 전체 시장 자료나 저장소 검사, 복원, 확인용 backup을 추가하지 않았다.

## 운영 보존

[전후 host 대조](post-host-comparison.json)와 [실행 전](before-host-units.txt)·[실행 후](after-host-units.txt) 상태를 보존했다.

- Chrony PID 1245, depth supervisor PID 3530137, aggTrade supervisor PID 3529954의 **PID·invocation·active 상태가 동일**하다.
- Archiver timer는 같은 invocation의 active/enabled, 다음 자연 예약 **10/25 20:35 KST**다. 서비스는 작업 전·후 유휴다.
- T05 timer는 같은 invocation의 active/enabled, 다음 예약 **10/26 15:40 KST**다. recurring 활성·manual 비활성과 사용자 자동화 두 mask를 유지했다. 보호 입력·정책·구성 파일의 해시/소유권/mode/링크 대조도 통과했다.
- archive state는 잠금 아래 동일했다. request `91357e783198488d866efdbb7433ed95`의 complete/0·identity·pins·파일 해시는 전후 같았다. 요청/원장/예산을 수정하거나 초기화하지 않았다.
- service 제어·timer 재시작·clock 제어·구성 변경은 **0건**이다. authority를 새로 만들거나 실행을 반복하지 않았다.

**수집기의 다음 child는 아직 대기 중이다.** 20:13 표본은 같은 supervisor 아래 `between_sessions`, child 없음, `clock_usable=false`·`prior_hard_fault_blocks_stale_exception`이다. 현재 추정의 유효성과 raw 시작 허용을 구분하는 기존 정책/이력 상태다. 이를 공간 회수 실패로 단정하거나 “현재 수집도 새로 재개했다”고 기록하지 않는다. 시각 정책·재시작을 이번 승인으로 변경하지 않았다.

## 완료 밖의 후속

[TODO](../../../TODO.md)에서 이번 정확한 묶음의 적용 대기는 닫고 다음 범위를 유지한다.

- **T19:** 남은 과거 출력의 추가 정리는 별도 정확한 목록·승인 단위. 다음 주력은 일반 기록의 기간+총량 예산, 필수/미완료 참조 보호, 파일시스템별 용량 경고와 이후 조회 빈도 분리다. 현재 약24.55GB를 무제한 여유로 해석하지 않는다.
- **T18 후속:** 과거 수집 공백과 interrupted collection, 보조 기록 독립 복구는 기존 범위대로 남는다.
- **운영 관찰:** T05의 10/26 15:40 자연 예약, 수집기의 다음 자연 child 경계. 이를 위해 수동 시작·정책 완화·새 backup을 만들지 않는다.

이전 sandbox 소유권 표시에 따른 실패 check와 미승인 계획도 보존한다. 이번 성공으로 과거 실패를 성공으로 덮어쓰지 않는다.
