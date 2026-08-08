# 食序管家生成动作索引

> 本页由 `contracts/tools.yaml` 生成，请勿手工编辑。

日常默认清单固定为 40 个动作；其余动作仅保留内部兼容。

| 领域 | 默认动作 |
| --- | --- |
| `meal` | `record`, `preview_record`, `commit_record`, `query`, `update`, `delete`, `record_cooking`, `record_prepared` |
| `water` | `record`, `query`, `update`, `delete` |
| `weight` | `record`, `query`, `update`, `delete` |
| `pantry` | `add`, `preview_add`, `commit_add`, `query`, `search`, `adjust`, `deduct`, `discard`, `open`, `freeze`, `thaw`, `preview_update_metadata`, `commit_update_metadata`, `preview_link_nutrition`, `commit_link_nutrition` |
| `transaction` | `get_recent`, `undo`, `redo` |
| `report` | `progress`, `expiring_inventory`, `insights` |
| `system` | `query_goals`, `preview_update_goals`, `commit_update_goals`, `query_preferences`, `update_preferences`, `forget_preference` |

| 工具 | 领域 | 动作 | 模式 | 确认 | 重试 |
| --- | --- | --- | --- | --- | --- |
| `diet_meal` | `meal` | `commit_record` | `mutation` | `workflow_handle` | `operation_receipt` |
| `diet_meal` | `meal` | `delete` | `mutation` | `none` | `operation_receipt` |
| `diet_meal` | `meal` | `nutrition_estimate` | `read` | `none` | `safe_read` |
| `diet_meal` | `meal` | `preview_record` | `read` | `conditional` | `safe_read` |
| `diet_meal` | `meal` | `query` | `read` | `none` | `safe_read` |
| `diet_meal` | `meal` | `record` | `mutation` | `conditional` | `operation_receipt` |
| `diet_meal` | `meal` | `record_cooking` | `mutation` | `conditional` | `operation_receipt` |
| `diet_meal` | `meal` | `record_prepared` | `mutation` | `none` | `operation_receipt` |
| `diet_meal` | `meal` | `save_recipe` | `mutation` | `none` | `operation_receipt` |
| `diet_meal` | `meal` | `suggest_recipes` | `read` | `none` | `safe_read` |
| `diet_meal` | `meal` | `preview_meal_plan` | `read` | `none` | `safe_read` |
| `diet_meal` | `meal` | `update` | `mutation` | `none` | `operation_receipt` |
| `diet_water` | `water` | `delete` | `mutation` | `none` | `operation_receipt` |
| `diet_water` | `water` | `query` | `read` | `none` | `safe_read` |
| `diet_water` | `water` | `record` | `mutation` | `none` | `operation_receipt` |
| `diet_water` | `water` | `update` | `mutation` | `none` | `operation_receipt` |
| `diet_weight` | `weight` | `delete` | `mutation` | `workflow_handle` | `operation_receipt` |
| `diet_weight` | `weight` | `query` | `read` | `none` | `safe_read` |
| `diet_weight` | `weight` | `record` | `mutation` | `none` | `operation_receipt` |
| `diet_weight` | `weight` | `update` | `mutation` | `none` | `operation_receipt` |
| `diet_pantry` | `pantry` | `cancel_shopping_list` | `mutation` | `workflow_handle` | `operation_receipt` |
| `diet_pantry` | `pantry` | `commit_shopping_list` | `mutation` | `workflow_handle` | `operation_receipt` |
| `diet_pantry` | `pantry` | `add` | `mutation` | `conditional` | `operation_receipt` |
| `diet_pantry` | `pantry` | `adjust` | `mutation` | `none` | `operation_receipt` |
| `diet_pantry` | `pantry` | `commit_add` | `mutation` | `workflow_handle` | `operation_receipt` |
| `diet_pantry` | `pantry` | `commit_deduct` | `mutation` | `workflow_handle` | `operation_receipt` |
| `diet_pantry` | `pantry` | `commit_link_nutrition` | `mutation` | `workflow_handle` | `operation_receipt` |
| `diet_pantry` | `pantry` | `commit_update_metadata` | `mutation` | `workflow_handle` | `operation_receipt` |
| `diet_pantry` | `pantry` | `discard` | `mutation` | `none` | `operation_receipt` |
| `diet_pantry` | `pantry` | `deduct` | `mutation` | `none` | `operation_receipt` |
| `diet_pantry` | `pantry` | `freeze` | `mutation` | `none` | `operation_receipt` |
| `diet_pantry` | `pantry` | `open` | `mutation` | `none` | `operation_receipt` |
| `diet_pantry` | `pantry` | `preview_add` | `read` | `conditional` | `safe_read` |
| `diet_pantry` | `pantry` | `preview_deduct` | `read` | `none` | `safe_read` |
| `diet_pantry` | `pantry` | `preview_link_nutrition` | `read` | `none` | `safe_read` |
| `diet_pantry` | `pantry` | `preview_shopping_list` | `read` | `none` | `safe_read` |
| `diet_pantry` | `pantry` | `preview_update_metadata` | `read` | `none` | `safe_read` |
| `diet_pantry` | `pantry` | `query` | `read` | `none` | `safe_read` |
| `diet_pantry` | `pantry` | `query_shopping_list` | `read` | `none` | `safe_read` |
| `diet_pantry` | `pantry` | `search` | `read` | `none` | `safe_read` |
| `diet_pantry` | `pantry` | `thaw` | `mutation` | `none` | `operation_receipt` |
| `diet_transaction` | `transaction` | `get_recent` | `read` | `none` | `safe_read` |
| `diet_transaction` | `transaction` | `redo` | `mutation` | `workflow_handle` | `operation_receipt` |
| `diet_transaction` | `transaction` | `undo` | `mutation` | `workflow_handle` | `operation_receipt` |
| `diet_report` | `report` | `cost_summary` | `read` | `none` | `safe_read` |
| `diet_report` | `report` | `daily` | `derived_file` | `none` | `safe_read` |
| `diet_report` | `report` | `expiring_inventory` | `read` | `none` | `safe_read` |
| `diet_report` | `report` | `insights` | `read` | `none` | `safe_read` |
| `diet_report` | `report` | `monthly` | `derived_file` | `none` | `safe_read` |
| `diet_report` | `report` | `progress` | `read` | `none` | `safe_read` |
| `diet_report` | `report` | `today` | `derived_file` | `none` | `safe_read` |
| `diet_report` | `report` | `trend_summary` | `read` | `none` | `safe_read` |
| `diet_report` | `report` | `waste_summary` | `read` | `none` | `safe_read` |
| `diet_report` | `report` | `weekly` | `derived_file` | `none` | `safe_read` |
| `diet_system` | `system` | `backup` | `maintenance` | `none` | `no_blind_retry` |
| `diet_system` | `system` | `commit_delete_data` | `maintenance` | `required_true` | `no_blind_retry` |
| `diet_system` | `system` | `commit_nutrition_backfill` | `mutation` | `workflow_handle` | `operation_receipt` |
| `diet_system` | `system` | `export_data` | `maintenance` | `none` | `no_blind_retry` |
| `diet_system` | `system` | `forget_preference` | `mutation` | `none` | `operation_receipt` |
| `diet_system` | `system` | `import_data` | `maintenance` | `required_true` | `no_blind_retry` |
| `diet_system` | `system` | `initialize` | `maintenance` | `none` | `no_blind_retry` |
| `diet_system` | `system` | `maintenance_history` | `read` | `none` | `safe_read` |
| `diet_system` | `system` | `maintenance_status` | `read` | `none` | `safe_read` |
| `diet_system` | `system` | `migrate` | `maintenance` | `none` | `no_blind_retry` |
| `diet_system` | `system` | `preview_delete_data` | `read` | `workflow_handle` | `safe_read` |
| `diet_system` | `system` | `query_goals` | `read` | `none` | `safe_read` |
| `diet_system` | `system` | `query_nutrition_backfill` | `read` | `none` | `safe_read` |
| `diet_system` | `system` | `query_preferences` | `read` | `none` | `safe_read` |
| `diet_system` | `system` | `repair` | `maintenance` | `none` | `no_blind_retry` |
| `diet_system` | `system` | `restore` | `maintenance` | `required_true` | `no_blind_retry` |
| `diet_system` | `system` | `self_check` | `read` | `none` | `safe_read` |
| `diet_system` | `system` | `commit_update_goals` | `mutation` | `workflow_handle` | `operation_receipt` |
| `diet_system` | `system` | `preview_update_goals` | `read` | `conditional` | `safe_read` |
| `diet_system` | `system` | `update_goals` | `mutation` | `none` | `operation_receipt` |
| `diet_system` | `system` | `update_preferences` | `mutation` | `none` | `operation_receipt` |
| `diet_system` | `system` | `validate_import` | `read` | `workflow_handle` | `safe_read` |
| `diet_system` | `system` | `validate_database` | `read` | `none` | `safe_read` |

## Skill capability routes

| Route | Domain | Action |
| --- | --- | --- |
| `meal_record` | `meal` | `record` |
| `prepared_meal_record` | `meal` | `record_prepared` |
| `cooking_record` | `meal` | `record_cooking` |
| `water_record` | `water` | `record` |
| `weight_record` | `weight` | `record` |
| `pantry_search` | `pantry` | `search` |
| `pantry_add` | `pantry` | `add` |
| `pantry_deduct` | `pantry` | `deduct` |
| `pantry_discard` | `pantry` | `discard` |
| `recent_operations` | `transaction` | `get_recent` |
| `undo` | `transaction` | `undo` |
| `redo` | `transaction` | `redo` |
| `daily_progress` | `report` | `progress` |
| `self_check` | `system` | `self_check` |
