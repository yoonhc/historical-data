# 첫 작업용 checkpoint 정리 승인 계획

**검토용이며 실제 적용은 승인 전이다. 이 문서는 삭제 명령을 실행하지 않는다.**

[점검 요약](summary.md) · [원본 계획](operational-plan.json) · [승인용 정책](approval-policy.toml)

- 전체 계획 ID: `720264ba82bd7a994c0ac3dc73e157b814a7b63042b3a56cb5c96da1b787a200`
- 공통 판정 시각: `2026-10-05 02:37:17.145302+09:00` (KST)
- 정책 해시: `005ccc904a8f3cde5e8180812a3b1740f30d1f3ea9dedbdc49620cf0f5eccd88`
- 프로필 해시: `3dff81754b14edef5b03c1827a9d1654ae5d694433caa34550c9195c0bf138be`
- 코드 commit: `c2d02e784b0456560ec734b9ef88f4d960dc6b78`
- 코드 ID: `00553a864e7de180f07d21f05f73e1dbe6f7890df8ef70a01e0e71d290f3a267`
- 실행 소스 해시: `a6eef42793890b4b8dcc73d39a0eefab857cacbe2d1fcede2aeb22f3d04bdca1`
- 마운트: `/mnt/backup`, UUID `d90aaf81-2358-411f-8022-4e1a3f7b0973`
- 저장소 ID: `b5a4434c95fcf4a9eb1339689cdea42c1da08bd34b845193dffce2aa8203e558`
- archive scope: `historical-data-original-v1`

기존 preview-policy는 false로 보존했다. 별도 approval-policy의 `apply_enabled=true`는
정책 변경 후 계획이 무효화되는 일을 피하기 위한 최종 입력이다. 그 파일을 참조하는 서비스·
타이머는 설치하지 않았으며 `apply`는 실행하지 않았다. 이 특정 계획의 승인이 필요하다.

정리 범위는 아래 작업용 사본 5개다. 원본 데이터·revision, 편입 전 사본 3개, 시험 자료·
복구 키트와 운영 restic 지점 8개는 정리 대상이 아니다. `forget`·`prune`도 포함하지 않는다.

예상 회수량은 **5.56 GB**이며 일반 파일 4.88 GB + 디렉터리 0.68 GB다.
논리 파일 크기 합계 75.27 GB와 구분한다. 실제 삭제 0건이다.

## 정리 후보

### 2026-09-27

- 정확한 경로: `/mnt/backup/marketdata-historical/automatic/2026-09-27/0650d76dddf94cbeb7ff1d998f9065e5`
- backup ID: `07af2f97fe8471c91f409bb7ac81c16b34add0ec8c549f2415b92f22122aefe3`
- restic snapshot ID: `726fe6039418d328006f11146c44e188560b3a4f4aff39ef9b87620abeedb8a1`
- checkpoint 기준 시각: `2026-09-27 13:23:59.463214+09:00`
- 완료 시각: `2026-09-27 13:25:23.118468+09:00`
- 완료 후 경과: **181.20시간** (`652314.026834`초)
- 선정 사유: 검증된 최근 3개 밖이며 완료 후 72시간 이상

### 2026-09-28

- 정확한 경로: `/mnt/backup/marketdata-historical/automatic/2026-09-28/0c547ccdd29647d2a76f7083d786f140`
- backup ID: `44261aaa58545c21e0a83f243beffb5579d31281c32a7339b5219cd7a9df2e5d`
- restic snapshot ID: `7ddea5243f9262eb058b15e97df1976eb5c369de386010c736b449a10a250713`
- checkpoint 기준 시각: `2026-09-28 13:27:09.053633+09:00`
- 완료 시각: `2026-09-28 13:28:57.205984+09:00`
- 완료 후 경과: **157.14시간** (`565699.939318`초)
- 선정 사유: 검증된 최근 3개 밖이며 완료 후 72시간 이상

### 2026-09-29

- 정확한 경로: `/mnt/backup/marketdata-historical/automatic/2026-09-29/0d84de71d71643a38d0250e061cc68e8`
- backup ID: `12156f6dd0a71fba1181c1e201f6c02163220bcae90d290993127c0b5c689af5`
- restic snapshot ID: `bbb553b48c9ea943e64dbf1e2f2d021b46d7c2c78313c505029d7c842c9d8df2`
- checkpoint 기준 시각: `2026-09-29 13:31:34.994030+09:00`
- 완료 시각: `2026-09-29 13:33:49.751226+09:00`
- 완료 후 경과: **133.06시간** (`479007.394076`초)
- 선정 사유: 검증된 최근 3개 밖이며 완료 후 72시간 이상

### 2026-09-30

- 정확한 경로: `/mnt/backup/marketdata-historical/automatic/2026-09-30/294884c6224e4e60a18522d8446e9892`
- backup ID: `11b8326f40d3c00e3526fad36e7377b5ae211e7375baa76745ec644fd01186cf`
- restic snapshot ID: `648d7c93eadab36b79295808bfa54b0e6f45b22e4e38e5ed363b540572361f9e`
- checkpoint 기준 시각: `2026-09-30 13:33:35.779067+09:00`
- 완료 시각: `2026-09-30 13:36:00.658048+09:00`
- 완료 후 경과: **109.02시간** (`392476.487254`초)
- 선정 사유: 검증된 최근 3개 밖이며 완료 후 72시간 이상

### 2026-10-01

- 정확한 경로: `/mnt/backup/marketdata-historical/automatic/2026-10-01/4f6a16890e6146e788e13e1af117490c`
- backup ID: `d3455e94e474ff5356c63fb86af6b80e016c23aea0ade8c617cf70d992ffa509`
- restic snapshot ID: `711137213f16926f19d58e4a07e1a8a28c10e6b1dd5446c0d26561d142692e94`
- checkpoint 기준 시각: `2026-10-01 13:37:11.944342+09:00`
- 완료 시각: `2026-10-01 13:39:56.328078+09:00`
- 완료 후 경과: **84.96시간** (`305840.817224`초)
- 선정 사유: 검증된 최근 3개 밖이며 완료 후 72시간 이상

## 유지 사본

### 2026-10-02

- 정확한 경로: `/mnt/backup/marketdata-historical/automatic/2026-10-02/7b570c997da346f28e0708d04289e9af`
- backup ID: `801cfce84d4a4b862c8578f8358ac1ff29c104d8ce25b9f98629cf43b7c96263`
- restic snapshot ID: `0c9f8bc23d1125cbfd7e0d3c442af4376298e22c8116f5526170797f0d9b9135`
- checkpoint 기준 시각: `2026-10-02 13:40:34.273336+09:00`
- 완료 시각: `2026-10-02 13:43:39.323607+09:00`
- 완료 후 경과: **60.89시간** (`219217.821695`초)
- 보호 사유: 검증된 최근 3개이며 완료 후 72시간 미만 (두 조건 모두 해당)

### 2026-10-03

- 정확한 경로: `/mnt/backup/marketdata-historical/automatic/2026-10-03/7e178c962a2d4869903423e3d62fe93d`
- backup ID: `e971e8b4b008fd5144f0ee6e8125ace882d2bdf1c6bf9f1fba23f0f5a1ac2e30`
- restic snapshot ID: `d27a0a557e1795a4651132016491f1b2c6735e8181b71303f3adccd414f0f8ad`
- checkpoint 기준 시각: `2026-10-03 13:47:03.758567+09:00`
- 완료 시각: `2026-10-03 13:50:27.280109+09:00`
- 완료 후 경과: **36.78시간** (`132409.865193`초)
- 보호 사유: 검증된 최근 3개이며 완료 후 72시간 미만 (두 조건 모두 해당)

### 2026-10-04

- 정확한 경로: `/mnt/backup/marketdata-historical/automatic/2026-10-04/a28359289b804f4e82d900878a820f23`
- backup ID: `70fc23fed2fdba282d5a1adfbd168df8666077f4b4f99984ce56831b74c48b9f`
- restic snapshot ID: `057cd08adc8e07dda2a93eb97f4ecfacce76a60971beb5c0d795566d1202f57b`
- checkpoint 기준 시각: `2026-10-04 13:47:56.871489+09:00`
- 완료 시각: `2026-10-04 13:51:41.756915+09:00`
- 완료 후 경과: **12.76시간** (`45935.388387`초)
- 보호 사유: 검증된 최근 3개이며 완료 후 72시간 미만 (두 조건 모두 해당)

## 편입 전 제외·보존

- `/mnt/backup/marketdata-historical/automatic/2026-09-25/initial` — backup ID `e6d4a8f8936990f158cb5a8cc91f419a933eb0f19610e5c4ad30c71a877a2240`, `before_enrollment`
- `/mnt/backup/marketdata-historical/automatic/2026-09-25/linked` — backup ID `a3f90efe758c2bc584d7e57e5ec1b5e98b8384637e65e5e547591699d5b9636c`, `before_enrollment`
- `/mnt/backup/marketdata-historical/automatic/2026-09-26/d4e81e6b027349cdb2e372da4f47288d` — backup ID `e6e2e02c95a0498aa88c6199866cddde05f3a0bba67b81d0566f57289dddfac7`, `before_enrollment`

## 승인 이후에도 적용 직전에 수행할 확인

새 Python 프로세스로 동일한 정책·코드·계획을 로드하고, 현재 저장소 ID·snapshot·복구 증거와
미해결 상태를 대조한다. checkpoint reader와 외부 복사 여부를 다시 확인하고, 남길 사본의
파일 해시·카탈로그·pack 검사를 통과해야 한다. 검사나 잠금에 실패하면 삭제하지 않는다.

새 daily나 코드·정책 변경으로 승인 대상 선정·연결이 달라지면 이 계획을 적용하지 않고 새 계획을
검토한다. 현재 계획은 commit을 포함하므로 **추가 commit 후에도 그대로 사용할 수 없다**.
승인은 이 전체 계획 ID의 수동 적용에 한정하며 자동 정리·restic 만료를 승인하는 것은 아니다.
