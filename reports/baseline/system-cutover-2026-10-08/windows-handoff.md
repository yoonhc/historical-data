# 첫 Windows 작업과 Linux 복귀

이 문서는 `system-bootstrap-20261007-f85eb8321a19c1ee`의 승인된 전환 작업창에서 사용할 인계 안내다.
**Linux 최종 백업·restic 확인이 끝나고 `handoff.json`이 `ready_for_first_windows`가 된 뒤 진행한다.**
아직 Windows 설정이나 RTC를 확인했다는 근거로 사용하지 않는다.

## Windows에서 할 일

1. Windows로 부팅한 뒤 **관리자 권한 명령 프롬프트(CMD)**를 연다.
   아래 명령으로 기존 값과 시간대를 먼저 확인하고 결과를 보관한다.

   ```cmd
   reg query "HKLM\SYSTEM\CurrentControlSet\Control\TimeZoneInformation" /v RealTimeIsUniversal
   tzutil /g
   w32tm /query /status
   ```

   첫 명령에서 값이 없다는 오류가 나면 기존 값은 없었던 것으로 기록한다.
   나머지 실패를 성공으로 간주하지 않는다. 현재 표시 날짜·시각도 함께 기록한다.

2. 다음 값으로 변경하고 조회해 확인한다. Windows 화면의 시간대는 한국시간을 유지한다.

   ```cmd
   reg add "HKLM\SYSTEM\CurrentControlSet\Control\TimeZoneInformation" /v RealTimeIsUniversal /t REG_DWORD /d 1 /f
   reg query "HKLM\SYSTEM\CurrentControlSet\Control\TimeZoneInformation" /v RealTimeIsUniversal
   ```

   기대값은 `RealTimeIsUniversal    REG_DWORD    0x1`이다.
   이 명령 형태는 [AWS Windows 안내](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/change-time-zone-of-instance.html)에 명시돼 있다.
   EC2 대상 안내이므로 이 PC의 실제 왕복 부팅 확인을 생략하는 근거로 사용하지 않는다.

3. 시작 메뉴에서 **다시 시작**을 선택하고, 부팅 메뉴에서 **다시 Windows**를 선택한다.
   최대절전·절전 복귀로 대신하지 않는다. Windows로 돌아오면 날짜/시간 설정에서 자동 시각 동기화를 실행한다.
   관리자 CMD에서는 다음으로 동기화 요청·상태를 확인할 수 있다.

   ```cmd
   w32tm /resync
   w32tm /query /status
   reg query "HKLM\SYSTEM\CurrentControlSet\Control\TimeZoneInformation" /v RealTimeIsUniversal
   tzutil /g
   ```

   `w32tm` 조회·재동기화 의미는 [Microsoft 공식 안내](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings)를 따른다.
   명령 실패·시간원 미확인·큰 시각 차이가 지속되면 결과를 그대로 전달하고, 값을 추가로 바꾸거나 시계를 수동으로 맞춰 숨기지 않는다.
   아직 Linux는 RTC를 LOCAL로 해석하므로 전환 중 일시적인 9시간 차이가 나타날 수 있다. 값 존재만으로 전환 완료라고 하지 않는다.

4. Windows의 실제 KST 표시·동기화·재부팅 뒤 `0x1` 유지가 확인되면 다시 시작하여 Linux로 돌아온다.
   이 대화에 **Windows 재시작 완료 여부, 레지스트리 값, 시간대, 동기화 상태/실패 내용**을 알려준다.
   Linux에서 수집기나 예약을 직접 다시 켜지 않는다.

## Linux로 돌아온 뒤 이어갈 작업

에이전트는 기존 20개 mask와 현재 부팅·마운트·프로세스·timesyncd의 실제 외부 동기화를 확인한다.
정확한 시스템 시각이 확인된 뒤 승인 범위에 따라 시스템→RTC 방향으로 UTC 설정을 반영한다.
`--adjust-system-clock`로 RTC를 시스템 시각의 기준으로 삼지 않는다.

그다음 Windows/Linux 왕복으로 UTC 해석 유지 여부를 확인하고, 실제 근거를 얻은 후에만 RTC 확인 자료를 만든다.
Chrony 설치·고정 배포본 설치·installation hold 등록·최종 Linux 부팅은 그 이후 단계다.
이번 첫 Windows 방문만으로 수집을 재개하거나 운영 전환 완료로 기록하지 않는다.

## 보존과 되돌리기

원래 유닛·링크·활성화 상태는 [before.json](before.json), `saved-user-units/`, `unit-content/`, `held-user-units/`에 있다.
mask는 `/home/yoonhc/.config/systemd/user/<unit>`의 `/dev/null` 링크다. `/run`의 임시 mask가 아니다.
이 원본들은 새 배포 후 업무 유닛을 복원할 때 사용하며, 일괄 unmask로 수집을 시작하지 않는다.

중간에 전환을 중단하더라도 Windows RTC flag와 Linux의 RTC 해석이 다르면 먼저 그 상태를 정리한다.
현재 시각을 검증한 뒤 원래 유닛/링크·활성화 상태를 명시적으로 복원한다.
옛 수집과 새 수집을 동시에 시작하지 않으며 자동 checkpoint 정리는 별도 상시 시각 검사 승인 전까지 보류한다.
원문·revision·manifest/source_root·기존 사본·grant·복구 키트는 수정·삭제하지 않는다.
