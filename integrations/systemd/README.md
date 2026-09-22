# 사용자 systemd 실행 예제

이 디렉터리의 서비스는 최초 검증 머신의 설치 경로를 사용한 예제입니다.
다른 위치에서 사용하려면 두 `.service` 파일의 `WorkingDirectory`와 `ExecStart`를
실제 프로젝트 경로로 수정하세요. `.venv/bin/marketdata`가 해당 위치에 설치되어 있어야 합니다.

- `marketdata-daily.timer`: 매일 UTC 03:20 이후 최대 5분의 분산 지연을 두고 일별 자료를 갱신합니다.
- `marketdata-oi.timer`: 매 5분 경계에서 45초 뒤에 최근 REST OI를 수집합니다.
- 최초 버전에서는 문법 검사만 완료했으며, 서비스와 타이머는 활성화하지 않았습니다.
- 사용자 서비스가 로그아웃 뒤에도 실행되는지는 user-manager/linger 설정에 따릅니다.

설치 및 상태 확인 명령은 [프로젝트 README](../../README.md#갱신과-운영)에 있습니다.
