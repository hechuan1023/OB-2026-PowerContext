# PowerContext 状态写入与复用记录

命令：`python -m powercontext_sdd handover export/check`（CLI 入口）

## 1. 空状态开始：保存任务记录（export）
```
快照已写入 minimax\records\handover_snapshot.json（id=92e10227dc1ccb3f，共 2 条制品）
exit=0
```

快照文件（节选）：
```json
{
  "snapshot_version": 1,
  "created_at": "2026-10-29T16:00:33+00:00",
  "project": "sample-app",
  "code_root": "E:\\MINIOB\\mini-ob\\OB-2026-PowerContext\\examples\\sdd-context-manager\\sample-app",
  "task": {
    "text": "订单明细按小计筛选（ORDER-FILTER-001）",
    "session_id": "record-000033"
  },
  "artifacts": {
    "SPEC-001": {
      "kind": "spec",
      "title": "订单总价与折扣的计算规则（v2）",
      "version": 2,
      "status": "active",
      "effective": true,
      "scope": [
        "app/pricing.py",
        "app/order.py"
      ]
    },
    "ADR-003": {
      "kind": "decision",
      "title": "折
```

## 2. 重启后读取并使用（check，重新拉起进程加载快照回查）
```
{
  "snapshot_id": "92e10227dc1ccb3f",
  "task": "订单明细按小计筛选（ORDER-FILTER-001）",
  "total": 2,
  "still_valid": 2,
  "expired": [],
  "code_changed": [
    "app/order.py"
  ],
  "clean": false
}
exit=0
```

说明：export 写入任务状态（已完成事项、决策、验证结论及其指纹）；
check 在新的模块进程中读取同一快照，回查其中哪些结论因代码/规格变化已过期。
