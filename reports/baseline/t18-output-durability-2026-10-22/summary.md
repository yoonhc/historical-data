# T18 출력 공유 후보 — 동기화 재호출과 경로 방어 보완

**10/22 00:04 KST 후보 수정·관련 격리 검증 완료. 운영 미설치·timer 재개 미승인.** 현재 수집·pack 복구의 완료와 과거 공백 후속은 [기존 복구 기록](../t18-capacity-recovery-2026-10-19/status.md)을 유지한다. 이번 작업은 운영 파일·서비스·원장·기존 receipt를 변경하지 않았다.

후속 **10/22 08:13 관리자 시험과 전환 도구 준비 결과**는 [최종 배포 검토안](../t18-archive-deploy-2026-10-22/review.md)에 연결했다. 아래는 이 후보 보완 당시의 원본 범위이며, 운영 적용·timer 재개는 계속 별도 승인 대기다.

## 확인한 경계와 수정

실제 이전 helper에서도 body 링크 생성 후 디렉터리 fsync 실패 → 재호출의 `FileExistsError` 분기에서 동기화 생략 경로를 확인했다. 공유 경로의 새 부모 디렉터리 항목도 동기화하지 않았고, `..`가 포함된 경로는 기존 문자열 관계 검사로 통과할 수 있었다. 실제 UUID 호출부에서 경로 이탈이 발생했다는 증거는 아니다.

[수정 helper](candidate/archive_output.py)는 다음 순서로 처리한다.

1. reports root와 stdout 경로의 `..`를 파일 변경 전에 거부한다. 기존 두 단계 경로·stdout 확장자·symlink·소유권·receipt 검사는 유지한다.
2. 기존 reports root 아래 공유 객체의 세 디렉터리를 만든다. 새 body와 기존 body 모두 같은 바이트·소유권·모드 검사와 파일 fsync를 거친다.
3. body 디렉터리부터 repository·`manifest-objects-v1`·reports root까지 동기화한다. 재호출에서 이미 존재하는 경로도 생략하지 않는다. 따라서 최초 mkdir/link 뒤 실패한 부모 항목도 성공으로 추정하지 않는다.
4. 원래 출력 경로를 공유 body에 연결한 뒤 run 디렉터리를 반드시 동기화한다. **replace 성공 후 fsync 실패로 임시 링크가 이미 없어진 재호출**도 포함한다.
5. 이 단계들이 성공한 뒤에만 현재 명령의 receipt 주석을 원자적으로 기록한다. 동기화 실패는 전파하며 기존 출력 바이트와 receipt를 보존한다.

파일 fsync와 디렉터리 항목 fsync가 별개라는 [Linux 문서](https://man7.org/linux/man-pages/man2/fsync.2.html)의 경계에 맞춘 변경이다. 기존 body 내용 검증을 생략하거나 단순히 오류를 무시하는 방식이 아니다. 이번 시험은 실제 파일/하드링크와 동기화 오류 주입·재호출 검사이며 **정전 후 복구 시험은 아니다**. 사용자 제공 Python 3.13 재현 묶음을 이 호스트에서 실행했다고 주장하지 않는다.

## 후보와 검사 근거

이전 [후보·검사 원본](../t18-capacity-recovery-2026-10-19/candidate-review.md)은 그대로 보존했다. 실제 설치 archiver 기반의 [호출부](candidate/archive_backups.py)는 이전 후보와 동일하며 T05 후처리와 조회·전체 읽기 검사 계약을 유지한다.

| 파일 | SHA-256 |
|---|---|
| `archive_output.py` | `e01a905faf122798539c211809d32c0b08e9d2271332227285ca8228d338de9f` |
| `archive_backups.py` | `941d425e5fab10272b92608de88c225f0d10ee592f85e29dccebf476424dee06` |

[원본/후보 식별](candidate-source.json), [빌드 입력](build-inputs.json), [검사 소스·결과 해시](verification.json), [재현 빌더](build_candidate.py)를 연결한다. workspace helper와 후보 helper의 바이트는 같다.

Python **3.12.3**에서 다음을 확인했다.

| 검사 | 결과 |
|---|---|
| 수정 전 새 회귀 8개 | 모두 실패해 기존 경계 재현. [원본](before-regressions.xml) |
| 수정 후 helper 23개 | 기존 15개와 새 8개 모두 통과. 네 디렉터리 각각의 반복 fsync 실패·회복, 기존 body 파일 동기화 실패, replace 후 동기화 실패·재호출, stdout/root의 `..` 거부 포함. [결과](helper-tests.xml) |
| 실제 설치 preservation 모듈 + 새 후보 | 위 23개와 기존 실제 restic no-op/T05 bundle·요청 호환 2개, **25개 통과**. [결과](installed-candidate-tests.xml), [실행기](check_candidate.py) |
| 정적 검사 | 변경한 helper·호출부·검사·빌더·실행기의 ruff check 및 git diff --check 통과 |

23개와 25개는 중복되는 검사이므로 독립된 48개로 합산하지 않는다. 실제 restic 격리 경로에서는 매번 새 dump/해시 확인, 큰 본문 할당 유지, 이전 완료 receipt와 T05 bundle의 보존을 다시 확인했다. 운영 repository에 추가 백업·전체 검사를 실행하지 않았다. 수정하지 않은 전체 업무 회귀는 이전 근거를 재사용한다.

## 다음 적용 범위와 남은 승인

**최종 배포 도구와 operation plan은 아직 고정하지 않았다.** 다음은 아래 연결만 검증하고 정확한 변경 목록·해시를 제출하는 단계다.

- 실제 UID 1000과 최종 서비스 보호 옵션의 임시 경로에서 hardlink/replace·import·guard를 확인한다. 같은 device 번호만으로 namespace 호환을 완료 처리하지 않는다.
- 현재 restic request와 코드/설정 identity를 대조하고, command 주석 → run → request 종결 순서를 유지한다. 과거 확정 receipt·원장·T05 근거를 다시 쓰지 않는다.
- 재개 직전 `/home` 실제 가용량과 현재 snapshot별 manifest 크기로 첫 body 생성·최대 임시 stdout·실행 중 증가 여유를 확인한다. backup mount의 공간 검사로 대신하지 않는다. 이 후보는 영구 중복 할당을 줄이며 실제 조회·쓰기·임시 출력은 남는다.
- 실행 시각과 향후 보관 판단은 receipt의 시작/종료로 한다. 공유 stdout의 mtime으로 새 조회 시각을 판단하지 않으며, 공유된 경로의 제자리 덮어쓰기·권한 일괄 변경을 하지 않는다.
- 원복은 먼저 예약 요청을 막고 진행 상태를 확인한 뒤 이전 코드를 복원하며, **timer 보류 상태로 종료**한다. 새 body·receipt·run·request·원장은 보존한다. 이전 코드로 돌아가며 timer를 자동 재개하지 않는다.
- 배포·timer 재개는 고정 계획에 대한 **별도 승인 후** 진행한다. 기존 `OnActiveSec=5s`·`Persistent=true`에 따른 빠른 요청 가능성을 포함하고, 같은 업무의 수동 start를 추가하지 않는다.
- 10/21 23:23 관찰에서 미보관인 10/20·21의 [정확한 두 backup ID](../t18-capacity-recovery-2026-10-19/current-preservation-20261021.json)를 우선 연결한다. 재개 시 추가 사본이 생겼다면 기존 batch 제한으로 구분한다. 각 snapshot·전체 검사·T05 bundle·요청 종결을 확인한 뒤, snapshot 집합이 같은 자연 no-op에서 첫 body 생성 비용과 이후 작은 기록 증가를 분리해 측정한다.

T05의 보관 조건·자동화, Chrony·수집기·시각 정책은 유지한다. 조회 빈도 조정·전체 과거 출력 정리·일반 로그 기간/총량 제한·과거 수집 공백은 [TODO](../../../TODO.md)의 별도 후속이며 이 최소 수정의 배포 선행 조건으로 확대하지 않는다.
