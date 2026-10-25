# T18 실제 manager 응답 보완·중단된 전환 재개 검토

2026-10-25 **16:02:01–03 KST 승인된 재개 성공·16:03 사후 대조 완료.** 구성과 ledger가 확정됐고 원래 authority 아래 recurring 활성·9개 timer 복원을 확인했다. [실제 적용·후속 운영 관찰](../t18-archive-deploy-2026-10-25/deployment-status.md). 아래는 보완 승인 당시 원인·중단 경계·검증 범위를 보존한 검토안이다. 완료된 재개 명령을 반복하지 않는다.

## 원인과 현재 위치

원래 operation `27dd5101…`의 15:29 실행은 성공한 빈 job 응답의 JSON 해석에서 실패했다. 별도 승인한 recovery `a277ae71…`는 이를 지나 15:43에 구성 파일 5개를 after로 설치하고 daemon-reload했으나, `KeyError: 'ExecStartPre'`로 중단됐다. 실제 systemctl은 빈 명령 배열 속성을 `--all`에서도 생략했다. 이 호스트 출력 경계를 앞선 검사에서 놓쳤다.

현재 파일 5개는 after, recurring은 **after scope의 비활성 문서**, boot ledger는 원래 기록을 `preserved`에 가진 **pending**이다. 새 grant 승인·ledger 확정·timer 복원은 미실시다. 기존 transaction과 두 실패를 보존하며 timer만 별도로 시작하지 않는다.

첫 복구의 `protected_processes_unchanged=false`는 daemon-reload 전후 `After`/`Requires` 출력 순서 차이였다. 순서를 제외한 의존관계와 PID·invocation·활성 상태는 동일했다. [원본 실패 사본](original-failed-recovery.json)을 수정하지 않고 후속 관찰을 연결한다.

## 좁은 수정

- 성공한 빈 job 응답 처리는 유지한다.
- 텍스트에서 생략된 선택 명령은 실제 systemd D-Bus의 형식 있는 값으로 확인한다. 실제 빈 배열만 허용하고 형식 오류·조회 실패·추가 명령은 거부한다.
- 보호 프로세스 대조에서 `After`/`Requires`만 정렬한다. PID·invocation·활성 상태와 다른 필드는 정확히 비교한다.
- 실제 pending transaction·state·authority·두 실패·현재 설정 7개 해시를 복구 진입 조건으로 고정한다. 원래 전환 엔진과 승인 시각을 재사용한다.

[adapter 차이](native_host.py.diff) · [진입점 차이](repair_resume.py.diff). 업무 소스·기존 Python·정책·구성 after·9개 timer·manifest 12개/미보관 6개·공간 조건·batch 3개와 전체 검사는 그대로다.

## 고정 식별자

| 항목 | 값 |
|---|---|
| 새 recovery | `ab49fce3b04087ceb44874b0fde1c452bd457e4b7b9e255dfda4aa7b8ad90eb6` |
| recovery JSON 바이트 SHA-256 | `8a139e1a2aa0c772d6b1e3d264407147d5f50a8e874d9ffd8236257d73545165` |
| 새 도구 | `t18-manager-query-recovery-20261025-2503f88208b70638` |
| native_host.py SHA-256 | `0d4231f0a1d109c7fe7cc04b9862327e2cf4ded347da2e3d140030bded2c229d` |
| repair_resume.py SHA-256 | `0a0b0ed9e15a68a1c3ff49c52802f1cd944443fc9fe15ec9b7ddcb454a78875d` |
| 원래 operation | `27dd5101bfa0b7005ce0a931b075bc9cccad5a5331e9350a30c4765cbf1ee225` |
| 보존할 authority SHA-256 | `5e501e421bb7ad14b43abc5329c123130c72a1f571c0a343a98ac4643caadae4` |
| 기존 승인 after scope | `2875edd74ccef07cd4a1f09917d147ab0a73cde0a6921a5f3f7cd9930344ef83` |

[전체 계획](recovery-plan.json) · [감사 경로·해시](artifact-paths.json). 원래 도구 seal·operation·authority와 두 실패는 보존한다. 새 recovery 수락은 별도 감사 디렉터리에 기록하며 T05 authority 승인 시각을 다시 만들지 않는다.

## 검증 결과와 한계

[31개 관련 검사](recovery-tests.xml)는 이전 18개와 이번 13개를 포함한다. 빈 명령 배열 확인·추가 명령/형식 오류 거부, 의존관계 순서와 실제 변경 구분, 정확한 중단 경계, **after 파일/pending ledger에서 재개와 원복**, authority·실패·신규 자료 보존을 확인했다. 기존 66개와 관리자 시험을 전체 재실행하지 않았다.

[15:50 실제 호스트 읽기 전용 대조](native-transition-probe.json): 최종 소스/기존 Python, 실효 명령·보호·의존관계·유휴 검사와 **UID 1000의 설치 CLI 전체 propose**가 통과했다. 예상 전체 문서와 일치했고 선택 명령 5개의 D-Bus 값은 모두 빈 배열이었다. manifest 12개 해시·8개 요청 종결·pending ledger/비활성 grant·authority도 일치했다. `/home` 가용 **16,631,365,632 B**, 요구 **12,208,032,519 B**다. 새 도구 `--action inspect`의 고정 해시/원래 root 감사 기록 연결도 통과했다.

두 수집 supervisor·Chrony는 원본 실행 전후와 후속 관찰에서 다음 PID/invocation으로 active였다.

| 서비스 | PID | Invocation |
|---|---:|---|
| Chrony | 1245 | `19420480ab3249529a075ca7cb2bd3c1` |
| depth | 3530137 | `8f9f604f45fa44b3833aca627834f391` |
| aggTrade | 3529954 | `3fa620f0e63341b9af76ff35b030b0c6` |

이는 실제 호스트의 **읽기 전용 검사**이며 새 root 전환 성공·timer 재개·편입 확인은 아니다. 실행 시 현재 입력·공간·잠금·전체 propose를 다시 검사한다. pending 중 다음 child 시작이 보류될 수 있으므로 현재 상태를 정상 운영 완료로 표시하지 않는다.

## 별도 승인 후 재개·원복

```sh
sudo /usr/bin/python3 -B -I \
  /home/yoonhc/projects/algoTrade/historical_data/reports/baseline/t18-archive-manager-recovery-2026-10-25/repair_resume.py \
  --action resume \
  --approve-recovery ab49fce3b04087ceb44874b0fde1c452bd457e4b7b9e255dfda4aa7b8ad90eb6
```

정확한 중단 경계 아래 원래 엔진으로 구성/ledger 전환을 이어간다. 실제 전체 propose 일치 후 **원래 authority**로 grant를 연결하고 잠금 해제 뒤 준비·공간을 확인한다. 이전 active 8개 timer 복원 뒤 archiver **timer만** 시작한다. service 직접 시작·모호한 요청 반복은 하지 않는다.

원복을 선택하면 위 명령의 `--action rollback`을 사용한다. before 구성/ledger/grant와 이전 active 8개 timer를 복원하되 archiver는 정지한다. 새 소스·body·receipt·request·자료·근거를 보존하고 원래 operation은 닫혀 같은 승인으로 재활성화하지 않는다. 부분 실패를 전체 원복 완료로 표시하지 않는다.

고정 도구 바이트가 추가로 달라졌으므로 사용자가 지정한 승인 경계에 따라 **이번 recovery 실행만 별도로 확인**한다. 새 정책·기능·백업 요구를 추가하지 않는다. 설치/재개, 미보관 6개 각각의 snapshot/전체 검사/T05 bundle/요청 종결, 자연 no-op의 출력 증가 억제는 각각 미완료다.
