# WINGS Self-hosted Runner 部署说明（去 SSH 化核心）

## 原理
GitHub Actions self-hosted runner 在学校服务器主动连 GitHub 拉任务，GitHub 不需从外网 SSH 进服务器。
只要服务器能访问 github.com，runner 就在线等任务。CI 永远在 GitHub-hosted(`ubuntu-latest`) 跑，
只有 `deploy-staging` / `deploy-production` / `rollback` 三类流水线落 `[self-hosted, wings-deploy]`。
**严禁任何 `pull_request` 触发的 job 使用 self-hosted runner**（避免 PR 代码在生产服务器执行）。

## 分支模型（实测仓库无 develop/main，据此拍板）
- `develop`：本仓库新建，基于 `release/stable`(=生产当前真身 e7725bc) 已知良好基线。staging 自动发布源。
- `release/stable`：当前生产等价分支（原方案写的 main 不存在，故 production 闸门放宽为 develop|tag|release/stable）。
- staging 仅接受 origin/develop 顶端 SHA；production 仅接受曾过 staging(develop) 或为正式 tag / release/stable 顶端。

## 在服务器一次性执行（需 break-glass root）

1. 建专用低权用户 `wings-runner`（不持完整 root，只允许调受限部署执行器）：
   ```bash
   sudo useradd -m -s /bin/bash wings-runner
   ```

2. 下载 runner 二进制（官方，校验 SHA）：
   ```bash
   sudo -u wings-runner mkdir -p /opt/wings-runner && cd /opt/wings-runner
   curl -o actions-runner.tar.gz -L https://github.com/actions/runner/releases/download/v2.319.1/actions-runner-linux-x64-2.319.1.tar.gz
   # 校验 checksum 后解压
   sudo -u wings-runner tar xzf actions-runner.tar.gz
   ```

3. 注册（token 由用户在 GitHub 仓库 Settings → Actions → Runners 生成，
   **直接复制到本机 SSH 窗口执行 `./config.sh`，绝不经聊天/脚本/commit**）：
   ```bash
   sudo -u wings-runner ./config.sh --url https://github.com/zhoupipi-del/wing3.0-.git \
     --token <TOKEN> --name wings-prod-runner --labels self-hosted,wings-deploy --unattended
   ```

4. 以服务方式运行（用 `wings-runner` 用户）：
   ```bash
   sudo ./svc.sh install wings-runner
   sudo ./svc.sh start
   ```

5. 受限部署执行器（root-owned 755，本仓库 `deploy/wings-deploy` 同步而来）：
   ```bash
   install -m 0755 -o root -g root deploy/wings-deploy /usr/local/sbin/wings-deploy
   ```
   注意：服务器现有 `/usr/local/sbin/wings-deploy`(251B 旧版) 需被本仓库版本覆盖。

6. 最小权限 sudoers（仅放行受限执行器，不给完整 root）：
   ```bash
   echo 'wings-runner ALL=(root) NOPASSWD: /usr/local/sbin/wings-deploy' > /etc/sudoers.d/wings-runner
   visudo -c
   ```

7. 生产凭据收权（SEC-STG-001 类）：确保 `/opt/wings3/config/production.env` 仅 root 可读，
   `wings-runner` 不可读；wrapper 默认 `WINGS_PROD_AUTO` 未置位 → production/rollback 直接 DEFERRED 零改动。

## 验证
- GitHub 仓库 Actions → Runners 显示 `Idle` 即在线。
- 之后 `git push origin develop` 自动触发 Deploy Staging。
- 临时服务器 `/s3/` 漂移（/home/wings-deploy/staging_fe_fix 等）须在本流水线首次成功部署后删除，
  以证明 "Git commit = staging 实际运行版本"。

## 注意
- Runner 不需要完整 root 密码，只通过 `wings-deploy` 受限执行器操作。
- 即使临时 SSH 密钥坏 / 云助手抽风，发布不受影响。
- break-glass(root) 仍保留，但日常不碰。
