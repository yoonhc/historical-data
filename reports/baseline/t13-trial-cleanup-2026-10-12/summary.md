# T13 시험 사본 정리 완료

**2026-10-12 18:45:23 KST**, 사용자가 요청한 T13 시험 사본 **5개 폴더를 부모 포함 모두 정리**했다.
파일 **104개**, 디렉터리 **158개**를 삭제했고, `/tmp`와 공용 복원 부모 `/mnt/backup/marketdata-restic-restore`는 유지했다.
실행 전후 관측 사용 가능 공간은 합계 **22,126,600,192바이트(22.13GB / 20.61GiB)** 증가했다.
동시 파일시스템 활동을 배제한 정확한 단독 회수량으로 해석하지 않는다.

[계획·목록·보존 대상](plan.json), [실제 결과](result.json), [삭제 기록 대조](post-verification.json), [실행 코드](cleanup.py).
계획 ID: `f13040e7befa5294ff19c25216587fbd378c487642bee961246a747583a3809d`.

## 정리한 정확한 범위

| 폴더 | 파일 | 부모 포함 디렉터리 | 목록의 할당 바이트 |
|---|---:|---:|---:|
| `/tmp/t13-daily-operating-preview-20261010` | 7 | 3 | 4,859,031,552 |
| `/tmp/t13-daily-operating-preview-20261011` | 5 | 4 | 4,859,834,368 |
| `/tmp/t13-daily-operating-preview-v3-20261011` | 5 | 3 | 4,860,448,768 |
| `/tmp/t13-daily-predeploy-preview-20261011` | 5 | 3 | 4,866,678,784 |
| `/mnt/backup/marketdata-restic-restore/t13-completion-20261012-637d2877b36f4c99` | 82 | 145 | 2,680,664,064 |

앞의 네 경로는 카탈로그 미리보기용으로 원문 전체나 운영 복구 지점이 아니다.
마지막 경로는 [T13 고정 운영 검증과 T05 근거 조회](../t13-operating-completion-2026-10-12/summary.md)를 끝낸 선택 복원본이다.
T05의 후행 사본 3개·72시간 조건을 기다리는 운영 checkpoint가 아니므로 해당 조건을 이 시험 폴더에 적용하지 않았다.

## 남긴 근거와 보존 확인

- 네 `preview.json`을 각각 `preserved-0-preview.json.gz`부터 `preserved-3-preview.json.gz`로 원문 보존했다.
  최종 복원본의 manifest와 시각 sidecar는 `preserved-4-backup.json.gz`·`preserved-4-checkpoint-evidence.json.gz`에 보존했다.
  압축 파일 해시는 계획에 있고, 압축 해제한 JSON도 확인했다.
- 기존 T13 후보·배포·운영 검증 보고서, 고정 대상 ID와 26개 시도/32개 작업/24,206행 대조 기록은 삭제 대상 밖에 있다.
  최종 보고서 manifest의 25개 파일 해시와 관련 후보/배포/고정 대상 근거가 유지됐다.
- 실제 checkpoint `fd4bf8417a5203f4dfbd467118740ecc2441d642096471737727fcbcdad00fdf`의 manifest·시각 sidecar 해시가 동일하다.
  restic snapshot `616ed4d6dbd3636b1970c5e623e3a770d42d31ae9ee3b9b26102e9675110a470`의 메타데이터와 저장소의 snapshot 목록도 전후 동일하다.
  이 지점을 포함한 기존 성공한 전체 읽기 검사 근거를 재사용했다. 새 backup/check/restore/forget/prune은 실행하지 않았다.
- T05 control state의 파일 해시, daily/Chrony 설정·유닛 해시, 운영 catalog의 파일 신원,
  Chrony/두 수집 supervisor PID·invocation, 사용자/시스템 자동 정리 mask 4개가 전후 동일하다.
  실행 중 운영 데이터 전체의 내용 해시가 고정돼 있다고 주장하지 않는다.

## 실행 범위 확인

기존 검증된 파일별 inode 대조·`dir_fd` 접근·삭제 journal 함수를 재사용했고, 그 도구의 과거 승인이나 `main`은 실행하지 않았다.
이번 사용자 요청을 정리 근거로 삼아 다섯 경로를 명시적으로 고정했으며 와일드카드 삭제·상위 공용 폴더 삭제는 하지 않았다.

실제 호스트에서 symlink·특수 파일·중첩 mount·장치 경계·공유 inode·다중 hardlink가 없는 목록을 확정했다.
운영 설정의 이 시험 경로 참조는 없었다. 협력적 archive/시험 디렉터리/미리보기 writer·scheduler 잠금을 잡고 삭제 직전에 같은 목록을 재확인했다.
프로세스 조회에서 사용 참조는 없었다. 동일 사용자 210개 프로세스를 조사했으며 기존 systemd·인증 보조·음성 프로세스 3개의 상세 fd/maps 읽기 제한은 기록했다.
이를 모든 사용자·비협력적 프로세스의 완전한 사용 배제 보장으로 확대하지 않는다.

삭제 journal은 [deletion-journal.jsonl](deletion-journal.jsonl)에 남겼다.
**의도 6개·완료 6개, 미완료 0개**, 삭제 항목 262개가 파일 104개+폴더 158개와 일치했다.
각 시험 루트의 부재와 공용 복원 부모의 존재를 마지막으로 확인했다.

| 파일시스템 | 삭제 전 여유 바이트 | 삭제 후 여유 바이트 | 관측 증가 |
|---|---:|---:|---:|
| `/tmp`가 있는 파일시스템 | 54,693,085,184 | 74,139,025,408 | 19,445,940,224 |
| 백업 SSD | 854,166,274,048 | 856,846,934,016 | 2,680,659,968 |

TODO의 T13 시험 사본 정리 항목은 이 결과를 남긴 뒤 제거했다.
T05 운영 checkpoint 정리, 자동 삭제 재개, 다른 시험 폴더, 고정 실행본과 보고서의 삭제는 이번 범위에 포함하지 않았다.
이전 보고서의 “시험 사본 보존 중”은 당시 상태이며 현재 보존 여부는 이 정리 결과를 따른다.
