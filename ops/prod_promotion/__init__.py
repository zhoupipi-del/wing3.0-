"""ops/prod_promotion — PROD-SEMANTIC-BRIDGE 版本化生产 promotion 工具链。

包含：
  - secret_redactor.py         审计输出脱敏
  - prod_promotion_preflight.py 生产硬 guard + 上下文 + 证据存储 + 生产连接
  - prod_2210_2220_bridge.py   6 阶段显式状态机（apply/verify/stamp × 2210/2220）
  - prod_promotion_postflight.py bridge 段 coherence 校验
  - RUNBOOK.md                 操作手册

LOCAL/GIT ONLY（PROD-SEMANTIC-BRIDGE-CANDIDATE-001）：本轮仅造候选 + 自测 + diff review，
禁止生产连接写 / stamp / deploy，禁止修改 WINGS_PROD_AUTO / develop / release/stable / wrapper。
"""
