# T05 첫 수동 정리 — 사본 검사 후 첫 rename의 EXDEV 실패

**2026-10-17 17:15 KST: run은 exit 1로 종료했고 삭제는 0개다.**
이번 실패는 시각 보류나 repository 잠금 경합이 아니라, 원본 checkpoint를 staging으로 옮기는
첫 `rename()`의 `OSError: [Errno 18] Invalid cross-device link`다. 재시도·운영 수정은 하지 않았다.
T05 첫 수동 정리와 반복 자동화는 미완료이며 네 자동 정리 mask를 유지한다.

**17:44 실제 관리자 시험 통과, 후속 복구안 준비 완료:** UID 1000의 두 임시 system unit에서
기존 split mount의 EXDEV와 공통 부모 mount의 rename 성공·경계 밖 쓰기 거부를 확인했다.
운영 입력은 같고 두 유닛·시험 경로는 제거됐다. 이후 기존 계획/journal을 유지하는 네 구성 파일 수정·
명시적 scope 전환·한 번의 재개 도구를 만들고 27개 격리 검사와 실제 호스트 읽기 대조를 통과했다.
[새 운영 승인 검토안](../t05-mount-recovery-2026-10-17/review.md)을 따른다.
수정 설치·정리 재개는 아직 하지 않았다.

## 실제 결과와 보존 확인

| 항목 | 결과 |
|---|---|
| 삭제 계획 | `31766af4d1fec615372a7c7cb37c380e13f734a62b548986cd858c3e8937ba08` |
| 실제 run | `5d7deaf59b1f4c99847a732327ce77af` |
| root attempt | `94319a1f731c4812bce709f6722fefbe` |
| 실행 | 16:50:43.605–17:15:01.873 KST, 약 24분 18초 |
| 서비스 | UID 1000, PID 2533542, invocation `b11b0fee028643d8aa0bb2ebb4dbf8a2` |
| 첫 실패 대상 | 10/13 checkpoint `10dd21822fcc0371772f3cbd3dc5b0ec0a8d086829fb78e1e0a1cab023685665` |
| journal | `interrupted`, attempt 1개, 대상 10개 전부 `prepared`, `unlinked_files=0` |
| 사본 대조 | 후보 10·유지 3·제외 3 모두 원래 위치, 16개 manifest 일치, 후보 root 신원 일치 |
| staging | 계획 디렉터리는 생성됐으나 비어 있음, 대상별 이동 폴더 0개 |
| inventory | 후보 10개 생성·각 승인 tree hash 일치, 원본 control state에 보존 |
| 권한·보호 | manual 원래 비활성 복원, recurring 비활성/승인 after 일치, 네 mask 유지 |
| 다른 운영 상태 | 보호 입력 91개·기존 근거/등록 파일 15개·Chrony/두 supervisor 신원 유지 |

[root 결과](evidence/root-attempt/result.json), [실제 run](evidence/run/run.json),
[중단 journal](evidence/retirement-journal.json), [17:22 읽기 대조](audit.json),
[원본 경로·해시·사본 목록](evidence-manifest.json), [manager 기록](manager-journal.jsonl).
큰 restic 명령 stdout은 운영 원본을 그대로 보존하고 해시/크기만 색인했다.
약 157MB인 inventory도 운영 경로와 해시를 색인했으며 저장소에 중복 복사하지 않았다.

설치된 `Retirement.apply()`의 순서는 유지 3개 `verify_backup()` → 후보 10개 manifest/tree 검사 및
inventory 저장 → journal 저장 → 첫 `retire_item()` → rename이다. `verify_backup()`는 파일/pack 검증에
실패하면 예외를 발생시킨다. 따라서 이번 journal 생성·첫 rename 도달은 그 앞선 검사들이 반환했음을 보여 준다.
개별 유지 사본의 새 검증 receipt가 따로 생성된 것처럼 표시하지는 않는다. 이 조사에서 전체 검사를 반복하지 않았다.
실패 당시 마지막 시각 관측은 ready, U **0.1392528615초**이며 당일 backup/restic 조건도 통과했다.

16:58/17:10 CPU·파일 읽기 관찰은 실제 진행이었다. 최종 성공을 뜻하지 않았으며,
최종 manager 기록의 CPU **21분 4.828초**, peak memory **11.8GiB**도 보존했다.
검증 비용 최적화는 T14 후속이며 이번 오류 해결을 위해 보존 검사를 생략하지 않는다.

## 원인과 격리 재현

실효 유닛에는 다음 두 경로가 각각 `ReadWritePaths`로 있다.

```ini
ReadWritePaths=/mnt/backup/marketdata-historical/automatic /mnt/backup/marketdata-historical/.automatic.retiring
```

`ProtectSystem=strict` 아래에서 이 두 쓰기 경계는 별도 bind mount가 된다.
호스트에서는 하나의 ext4 mount이고 device 번호도 같지만, rename은 서로 다른 mount 사이를 넘지 못한다.
현재 엔진의 `st_dev` 비교는 이 경우를 발견하지 못한다. 첫 이동에서 실패했으므로 복사 후 삭제로 우회하지 않는다.
실패한 서비스 namespace는 이미 종료됐으므로 그 namespace의 mount ID를 직접 채집한 것은 아니다.

실제 Linux private user/mount namespace에서 작은 `/tmp` fixture로 확인했다.

| 조건 | 결과 |
|---|---|
| 원본·staging 각각 writable bind mount | 같은 device, 다른 mount ID, 실제 rename `EXDEV`, 원본 유지 |
| 공통 부모 하나의 writable bind mount | 같은 device/mount ID, 실제 rename 성공, 내용 동일 |
| 두 경우의 경계 밖 쓰기 | 모두 `EROFS`, 원래 내용 유지 |

[재현 코드](namespace_probe.py), [실제 결과](namespace-results.json).
시험 경로는 제거했고 운영 데이터·설정·서비스는 변경하지 않았다.
이는 실제 kernel 동작 시험이며 system manager의 최종 보호 환경 시험과 구분한다.
이전 관리자 사전 점검은 조회·미리보기 중심이어서 첫 물리 이동을 시험하지 못한 공백이 있었다.

## 수정 후보와 아직 남은 범위

후보는 수동/자동 서비스의 쓰기 경계를 다음 공통 부모로 바꾼다.

```ini
ReadWritePaths=/mnt/backup/marketdata-historical
```

해당 부모에는 현재 `automatic`, `.automatic.retiring` 두 항목만 있다. 부모 자체의 쓰기 범위가 추가되므로
이를 숨기지 않고 변경 범위에 포함한다. 다른 보호 옵션·보존 정책·삭제 엔진은 동일하다.
[정확한 두 유닛 diff](candidate/units.diff), [후보와 scope 차이](candidate-review.json).
후보 유닛 2개 `systemd-analyze verify`는 exit 0이다. 실제 시스템 보호 환경의 작은 rename 시험도
[17:44 원본](system-rename-result-6b0b3591a09c42dc.json)에서 통과했다.

canonical unit 해시를 바꾸면 승인 scope가 `da7b03db…`에서 후보 `84a0b92a…`로 바뀐다.
현재 `manual_proposal()`는 계획의 `automation.scope_id`와 현재 scope가 다르면 거부한다.
따라서 기존 명령 반복이나 기존 승인 JSON 재사용으로 적용할 수 있다고 표시하지 않는다.
기존 계획·journal·inventory를 지우거나 scope 필드만 고쳐 승인 검사를 우회하지 않는다.
새 구성의 승인·중단 journal 복구는 [별도 검토안](../t05-mount-recovery-2026-10-17/review.md)으로 준비했다.
새 operation plan은 `eced87a499269ebe5da84be1b0795ebfd2c109ce06f37db4e5e26f8027b9e231`이며
운영 승인 대기다. 원래 삭제 계획과 EXDEV run을 함께 명시해야 하며 일반 scope 검사를 고치지 않는다.

완료한 [system_rename_preflight.py](system_rename_preflight.py) 시험은 실제 system manager를 사용했다.
두 임시 unit이 UID 1000·설치본과 같은 보호 옵션으로, `/mnt/backup/.md-t05-rename-*`의 작은 fixture만 처리한다.
기존 경계의 EXDEV와 공통 경계의 성공·외부 쓰기 차단을 확인하고 시험 경로/unit을 정리한다.
transient unit을 사용해 운영 daemon-reload를 하지 않는다. 운영 정리 실행·grant 갱신·timer 제어도 없다.
도구 세션의 최초 `sudo -n`은 인증 부재로 실행되지 않았다. 이후 사용자가 아래 명령을 실행해
위 원본의 통과 결과를 남겼다. 이미 통과한 시험을 다시 요청하는 명령은 아니다.

```sh
sudo /usr/bin/python3 -B -I /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t05-first-manual-exdev-2026-10-17/system_rename_preflight.py --run
```

그 결과를 확인하고 변경 scope·현재 journal을 함께 다루는 설치/복구안을 고정했다.
이전 `manual_resume.py`는 journal/inventory/staging이 없던 Busy 상태만 허용하므로 이번 실패에 재사용하지 않는다.
실제 수정·정리 재개·반복 자동화는 각각 해당 승인 범위를 따라야 한다.
