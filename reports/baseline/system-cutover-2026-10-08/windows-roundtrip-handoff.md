# Linux RTC UTC 반영 후 Windows 왕복 확인

이 안내는 기존에 승인한 전환의 두 번째 왕복 단계다. **Linux에서 RTC UTC 반영과 검증을 마친 뒤에만 진행한다.**
첫 Windows 방문 결과는 [사용자 확인 기록](windows-first-result.json)에 있으며, 이 두 번째 왕복까지 완료했다는 뜻은 아니다.

1. Linux에서 **다시 시작**하여 Windows로 부팅한다. 절전·최대절전 복귀로 대신하지 않는다.
2. Windows에 들어오면 화면의 날짜·한국 시각을 먼저 확인한다. 가능하면 수동 동기화 전에 확인하고,
   처음에 9시간 차이가 있었는지 기록한다. 이미 자동 동기화됐다면 그 사실과 최초 관측 시각을 구분한다.
3. 관리자 CMD에서 다음을 조회한다. 레지스트리 값을 다시 쓰지 않는다.

   ```cmd
   reg query "HKLM\SYSTEM\CurrentControlSet\Control\TimeZoneInformation" /v RealTimeIsUniversal
   tzutil /g
   w32tm /query /status
   ```

   기대값은 `REG_DWORD 0x1`, `Korea Standard Time`, 유효한 외부 시간원·동기화 상태다.
   현재 날짜·시각, 마지막 성공 동기화 시각, 시간원, Stratum을 보관한다.
   추가 동기화가 필요하면 기존 절차의 `w32tm /resync` 후 상태를 다시 조회하되,
   동기화 전후 상태를 구분한다. 큰 오차가 있었다면 동기화 후 사라졌어도 그대로 알려준다.
4. Windows에서 **다시 시작**하여 Linux로 돌아온다. 수집·예약은 직접 켜지 않는다.
5. 대화에 최초 표시 시각의 이상 여부, `0x1` 유지, 시간대, 동기화 결과와 Linux 복귀를 알려준다.

Linux 복귀 후 에이전트가 새로운 boot ID, 20개 persistent mask, `/etc/adjtime=UTC`, `LocalRTC=no`,
실제 RTC/UTC 근접성 및 이번 부팅의 외부 동기화 기록을 확인한다. Windows 쪽 값은 사용자 관측 근거로 기록한다.
두 OS의 해석 유지가 확인된 후에만 RTC 확인 자료와 Chrony·고정 배포본 설치 단계로 진행한다.

자동 checkpoint 정리는 계속 보류한다. 원본·기존 사본·manifest·정리 grant는 변경하지 않는다.
