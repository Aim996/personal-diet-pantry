from __future__ import annotations

import hashlib
import json
from packaging.version import Version
from pathlib import Path
import tomllib
import zipfile

import pytest

from personal_diet_pantry import __product_version__, __version__
from personal_diet_pantry.data_export import (
    CONTRACT_VERSION,
    EXPORT_SCHEMA_VERSION,
    PORTABLE_TABLES,
)
from personal_diet_pantry.data_import import _canonical_json, _validate_bundle


ROOT = Path(__file__).resolve().parents[1]
EXPECTED = "0.8.28"
PRODUCT_VERSION = "0.7.4.28"
PREVIOUS_EXPECTED = "0.8.27"
PREVIOUS_PRODUCT_VERSION = "0.7.4.27"


def test_each_iteration_reads_constraints_and_registers_preserved_features() -> None:
    invariants = (
        ROOT / "docs" / "PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md"
    ).read_text(encoding="utf-8")
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    for phrase in (
        "每一个变更批次开始前，维护者必须完整阅读本文件",
        "不得以记忆、摘要、旧版本或他人转述代替",
        "当用户明确表示某项功能、格式、流程或行为需要保留",
        "必须在同一个变更批次、开始实现前",
        "登记到本文件的“受保护行为”",
        "未经用户针对该受保护项作出的明确修改许可",
        "不得删除、弱化、改序、缩减、替换或改变语义",
        "整体优化、重构、简化、提升体验",
        "不构成修改授权",
        "必须明确点名受保护项、具体修改内容和允许范围",
    ):
        assert phrase in invariants

    assert package["productVersion"] == PRODUCT_VERSION
    assert package["version"] == EXPECTED


def test_every_material_change_requires_a_new_immutable_version() -> None:
    invariants = (
        ROOT / "docs" / "PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md"
    ).read_text(encoding="utf-8")
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))

    for phrase in (
        "版本目录一经创建即视为不可变",
        "无论是否已经上传、安装或发布",
        "文档、Skill、reference、规则、配置、源码、测试或构建脚本",
        "必须创建新的产品版本目录",
        "产品版本和技术版本必须同时递增",
        "不得以相同版本号或相同制品文件名覆盖",
        "一次连续实现中的多次编辑属于同一个变更批次",
    ):
        assert phrase in invariants

    assert package["productVersion"] == PRODUCT_VERSION
    assert package["version"] == EXPECTED
    assert Version(PRODUCT_VERSION) > Version(PREVIOUS_PRODUCT_VERSION)
    assert Version(EXPECTED) > Version(PREVIOUS_EXPECTED)


def test_all_version_sources_use_the_dual_0745_contract() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    plugin = json.loads(
        (ROOT / "openclaw.plugin.json").read_text(encoding="utf-8")
    )
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert package["version"] == EXPECTED
    assert package["productVersion"] == PRODUCT_VERSION
    assert lock["version"] == EXPECTED
    assert lock["packages"][""]["version"] == EXPECTED
    assert plugin["version"] == EXPECTED
    assert project["project"]["version"] == EXPECTED
    assert __version__ == EXPECTED
    assert __product_version__ == PRODUCT_VERSION
    assert Version(EXPECTED)
    assert (ROOT / "RELEASE.zh-CN.md").read_text(
        encoding="utf-8"
    ).startswith("# 食序管家（Personal Diet Pantry）v0.7.4.28\n")
    installation = (ROOT / "docs" / "INSTALLATION.zh-CN.md").read_text(
        encoding="utf-8"
    )
    assert EXPECTED in installation
    assert "personal-diet-pantry-0.6.1-installable.tgz" not in installation
    assert "diet_weight" in installation
    assert (ROOT / "UPDATE-v0.7.4.28.zh-CN.md").is_file()


def test_v0740_update_document_names_core_simplification_boundaries() -> None:
    text = (ROOT / "UPDATE-v0.7.4.0.zh-CN.md").read_text(encoding="utf-8")
    for phrase in (
        "没有新增 migration",
        "v0.7.3.6 可安装包",
        "六项两行进度回执",
        "40 个日常公开动作",
        "运行时只读主 `SKILL.md`",
        "过期库存不会进入食用和推荐候选",
        "纠错的净变动",
        "不会自动部署",
    ):
        assert phrase in text


def test_v0736_update_document_names_stabilization_and_release_boundaries() -> None:
    text = (ROOT / "UPDATE-v0.7.3.6.zh-CN.md").read_text(encoding="utf-8")
    for phrase in (
        "没有新增 migration",
        "v0.7.3.5 可安装包",
        "受保护的六项两行进度回执保持不变",
        "UTC",
        "IANA",
        "通用时间范围",
        "估算确认",
        "报告完整性",
        "调用预算",
        "不会自动部署",
        "宿主流式重复",
    ):
        assert phrase in text

    migrations = sorted((ROOT / "migrations").glob("*.sql"))
    assert migrations
    assert migrations[-1].name.startswith("021_")
    assert not any(int(path.name.split("_", 1)[0]) > 21 for path in migrations)


def test_v0735_update_document_names_preservation_registration_rules() -> None:
    text = (ROOT / "UPDATE-v0.7.3.5.zh-CN.md").read_text(encoding="utf-8")
    for phrase in (
        "每个变更批次开始前必须完整",
        "同一变更批次、开始实现前登记进约束文件",
        "不能只把承诺留在聊天、计划或开发者记忆中",
        "未经用户明确授权",
        "整体优化、重构、简化、提升体验",
        "有效授权必须点名受保护功能、具体修改内容和允许范围",
        "没有新增 migration",
        "v0.7.3.4 可安装包",
        "不会自动部署",
    ):
        assert phrase in text


def test_v0734_update_document_names_version_iteration_rules() -> None:
    text = (ROOT / "UPDATE-v0.7.3.4.zh-CN.md").read_text(encoding="utf-8")
    for phrase in (
        "强制版本迭代",
        "文档、Skill、reference、规则、配置、源码、测试和构建脚本",
        "产品版本与 npm/OpenClaw/Python 技术版本同时递增",
        "不得用相同版本号或相同制品文件名覆盖",
        "没有新增 migration",
        "v0.7.3.3 可安装包",
        "不会自动部署",
    ):
        assert phrase in text


def test_v0733_update_document_names_the_restored_progress_contract() -> None:
    text = (ROOT / "UPDATE-v0.7.3.3.zh-CN.md").read_text(encoding="utf-8")
    for phrase in (
        "六项",
        "每项固定两行",
        "10 格",
        "最终成功工具结果",
        "不得静默",
        "没有新增 migration",
        "v0.7.3.2 可安装包",
        "不会自动部署",
    ):
        assert phrase in text


def test_v0732_update_document_names_core_fixes_and_deferred_work() -> None:
    text = (ROOT / "UPDATE-v0.7.3.2.zh-CN.md").read_text(encoding="utf-8")
    for phrase in (
        "搜索句柄",
        "1盒",
        "部分营养",
        "未知字段保持 `null`",
        "包装三元组",
        "原 `occurred_at`",
        "行为轨迹",
        "没有新增 migration",
        "不会自动部署",
        "v0.7.3.3",
        "v0.8.0",
    ):
        assert phrase in text


def test_v0731_update_document_has_required_compatibility_contract() -> None:
    text = (ROOT / "UPDATE-v0.7.3.1.zh-CN.md").read_text(encoding="utf-8")
    for phrase in (
        "813 KB",
        "<160000 bytes",
        "确定性运行层",
        "可信系统时间",
        "per_100ml",
        "hydration",
        "同一个 `diet.sqlite`",
        "没有新增 migration",
        "不会自动部署",
        "v0.7.3",
    ):
        assert phrase in text


def test_v073_update_document_has_required_release_contract() -> None:
    text = (ROOT / "UPDATE-v0.7.3.zh-CN.md").read_text(encoding="utf-8")
    headings = (
        "# 食序管家 v0.7.3 更新说明",
        "## 本版目标",
        "## 包装语义持久化",
        "## 商品级扣减与 FEFO",
        "## 日历到期日",
        "## 熟食剩菜直接记录",
        "## 结果与失败恢复",
        "## Skill 路由",
        "## 数据迁移与兼容",
        "## 验证结果",
        "## 升级与回退",
        "## 非目标",
    )
    positions = [text.index(heading) for heading in headings]
    assert positions == sorted(positions)
    assert "migration 021" in text
    assert "同一个 `diet.sqlite`" in text
    assert "不会自动部署" in text
    assert "v0.7.2 可安装包" in text
    assert "升级前数据库备份" in text


def test_import_accepts_v071_through_v0746_portability_exports() -> None:
    records = {table: [] for table in PORTABLE_TABLES}
    records_sha256 = hashlib.sha256(
        _canonical_json(records).encode("utf-8")
    ).hexdigest()
    for product_version in (
        "0.7.1",
        "0.7.2",
        "0.7.3",
        "0.7.3.1",
        "0.7.3.2",
        "0.7.3.3",
        "0.7.3.4",
        "0.7.3.5",
        "0.7.3.6",
        "0.7.4.0",
        "0.7.4.1",
        "0.7.4.2",
        "0.7.4.3",
        "0.7.4.4",
        "0.7.4.5",
        "0.7.4.6",
        "0.7.4.7",
        "0.7.4.8",
        "0.7.4.9",
        "0.7.4.10",
        "0.7.4.11",
        "0.7.4.12",
        "0.7.4.13",
        "0.7.4.15",
        "0.7.4.16",
        "0.7.4.23",
        "0.7.4.24",
        "0.7.4.27",
        "0.7.4.28",
    ):
        manifest = {
            "export_schema_version": EXPORT_SCHEMA_VERSION,
            "contract_version": CONTRACT_VERSION,
            "product_version": product_version,
            "record_counts": {table: 0 for table in PORTABLE_TABLES},
            "records_sha256": records_sha256,
        }
        validated_manifest, validated_records = _validate_bundle(
            {"manifest": manifest, "records": records}
        )
        assert validated_manifest["product_version"] == product_version
        assert set(validated_records) == set(PORTABLE_TABLES)


@pytest.mark.parametrize("export_format", ("json", "csv"))
def test_real_exports_use_the_current_product_version(
    service, export_format: str
) -> None:
    result = service.dispatch(
        {
            "domain": "system",
            "action": "export_data",
            "payload": {
                "format": export_format,
                "operation_key": f"version-contract-{export_format}",
            },
        }
    )

    assert result["ok"] is True
    export_path = (
        service.data_paths.exports / result["data"]["export"]["name"]
    )
    if export_format == "json":
        manifest = json.loads(export_path.read_text(encoding="utf-8"))["manifest"]
    else:
        with zipfile.ZipFile(export_path) as archive:
            manifest = json.loads(archive.read("manifest.json"))

    assert manifest["product_version"] == __product_version__ == PRODUCT_VERSION


def test_v0744_core_gate_keeps_the_protected_release_behaviors() -> None:
    entries = {
        line.strip()
        for line in (ROOT / "contracts" / "v070-core-tests.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert {
        (
            "tests/contracts/test_inventory_search_contracts.py::"
            "test_public_search_returns_bounded_candidates_and_handles"
        ),
        (
            "tests/contracts/test_live_intake_regressions.py::"
            "test_packaged_soy_meal_uses_volume_hydration_inventory_and_"
            "public_undo"
        ),
        "src-tests/schema-size.test.ts",
        (
            "tests/test_tool_contract_generation.py::"
            "test_skill_routes_target_the_expected_domain_actions"
        ),
        (
            "tests/contracts/test_prepared_food_direct_contracts.py::"
            "test_record_prepared_reuses_snapshot_and_only_deducts_leftover"
        ),
        (
            "tests/contracts/test_public_outcome_contracts.py::"
            "test_public_outcomes_distinguish_read_preview_write_and_failure"
        ),
        (
            "tests/test_temporal_queries.py::"
            "test_same_cross_day_scope_returns_meals_water_and_weights"
        ),
        (
            "tests/test_local_time_projection.py::"
            "test_public_meal_water_and_weight_project_shanghai_local_time"
        ),
        (
            "tests/test_expiring_report_completeness.py::"
            "test_expiring_report_includes_all_expired_remaining_batches_and_is_read_only"
        ),
        (
            "tests/test_inventory_lineage_projection.py::"
            "test_prepared_food_search_projects_only_formal_cooking_relation"
        ),
        (
            "tests/test_quantity_resolution.py::"
            "test_multiple_estimates_share_one_preview_and_one_final_commit"
        ),
        (
            "tests/test_skill_stabilization.py::"
            "test_repeated_failure_trace_stops_after_one_diet_call_without_exec_or_files"
        ),
    } <= entries

    verify = (ROOT / "ci" / "verify.ps1").read_text(encoding="utf-8")
    assert "$CoreTypeScriptTests" in verify
    assert "v0.7.4.28 core behavior gate" in verify
    assert '"node_modules/vitest/vitest.mjs"' in verify
    assert "& node $Vitest run" in verify
    assert "& npm test -- --reporter=json" not in verify


def test_supported_runtime_ranges_are_explicit() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert package["engines"]["node"] == ">=22.22.3"
    assert package["peerDependencies"]["openclaw"] == ">=2026.5.17"
    assert package["devDependencies"]["openclaw"] == "2026.7.1-2"
    assert project["project"]["requires-python"] == ">=3.11,<4"


def test_overridable_test_dependencies_pin_audited_safe_versions() -> None:
    package = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))
    acceptance = json.loads(
        (ROOT / "contracts" / "dependency-risk-acceptance.json").read_text(
            encoding="utf-8"
        )
    )

    safe_versions = {"postcss": "8.5.23", "protobufjs": "7.6.5"}
    locked_paths = {
        "node_modules/postcss": "postcss",
        "node_modules/@openclaw/ai/node_modules/protobufjs": "protobufjs",
        "node_modules/openclaw/node_modules/protobufjs": "protobufjs",
    }

    assert package["overrides"] == safe_versions
    for dependency_path, package_name in locked_paths.items():
        assert (
            lock["packages"][dependency_path]["version"]
            == safe_versions[package_name]
        )
    accepted_ids = {item["advisory_id"] for item in acceptance["accepted"]}
    assert "GHSA-f88p-2vq3-8r39" not in accepted_ids
    assert "GHSA-j3f2-48v5-ccww" not in accepted_ids


def test_v07418_rollback_reuses_the_v07417_schema_and_keeps_a_cold_backup() -> None:
    text = (ROOT / "docs" / "INSTALLATION.zh-CN.md").read_text(
        encoding="utf-8"
    )

    assert "PYTHON_BIN" not in text
    assert r".\.venv\Scripts\python.exe scripts/build_release.py" in text
    assert "实例已停止时取得的一致 SQLite 冷备份" in text
    assert "在线 `diet_system backup` 仅用于同版本恢复" in text
    assert "不能替代升级前冷备份" in text
    assert "v0.7.4.28 没有新增 migration" in text
    assert "schema 与 v0.7.4.19 相同" in text
    assert "0.7.4.6" not in text
    assert "升级前一致的 SQLite 数据库备份即可满足版本回滚条件" not in text
    assert "在线 SQLite 快照" not in text

    stop = text.index("停止目标实例")
    backup = text.index("scripts/cold_backup.py backup")
    verify = text.index("校验冷备份")
    rollback = text.index("## 7. 成套回滚")
    install = text.index("personal-diet-pantry-0.7.4.19-installable.tgz", rollback)
    restore = text.index("scripts/cold_backup.py restore", rollback)
    assert stop < backup < verify < rollback < install < restore


def test_cold_backup_docs_use_the_fail_closed_helper() -> None:
    installation = (ROOT / "docs" / "INSTALLATION.zh-CN.md").read_text(
        encoding="utf-8"
    )

    assert "Python 标准库 `sqlite3` backup API" in installation
    assert "尚未 checkpoint 的已提交 WAL 数据" in installation
    assert "scripts/cold_backup.py backup" in installation
    assert "scripts/cold_backup.py restore" in installation
    assert "恢复命令不会检测进程状态" in installation
    assert "`diet.sqlite-journal`" in installation
    assert "未完成目标可能保留" in installation
    for unsafe_fragment in (
        "Copy-Item -LiteralPath",
        "Move-Item -LiteralPath",
        "cp --",
        "mv --",
        "sha256sum",
    ):
        assert unsafe_fragment not in installation


def test_troubleshooting_reinstalls_v07417_and_names_all_seven_tools() -> None:
    text = (ROOT / "docs" / "TROUBLESHOOTING.zh-CN.md").read_text(
        encoding="utf-8"
    )

    assert "适用版本：Personal Diet Pantry（食序管家）v0.7.4.28" in text
    assert "personal-diet-pantry-0.7.4.28-installable.tgz" in text
    assert "保留当前专用 `dataDir`" in text
    assert "[成套回滚流程](INSTALLATION.zh-CN.md#7-成套回滚)" in text
    assert "七类工具" in text
    assert "`diet_weight`" in text
    assert "personal-diet-pantry-0.6.0-installable.tgz" not in text
    assert "六类工具" not in text


def test_release_docs_share_the_same_cold_rollback_boundary() -> None:
    documents = (
        ROOT / "UPDATE-v0.7.4.28.zh-CN.md",
        ROOT / "UPDATE-v0.7.4.27.zh-CN.md",
        ROOT / "UPDATE-v0.7.4.7.zh-CN.md",
        ROOT / "UPDATE-v0.7.4.6.zh-CN.md",
        ROOT / "UPDATE-v0.7.4.5.zh-CN.md",
        ROOT / "UPDATE-v0.7.4.4.zh-CN.md",
        ROOT / "UPDATE-v0.7.4.3.zh-CN.md",
        ROOT / "UPDATE-v0.7.4.2.zh-CN.md",
        ROOT / "UPDATE-v0.7.4.1.zh-CN.md",
        ROOT / "UPDATE-v0.7.4.0.zh-CN.md",
        ROOT / "UPDATE-v0.7.3.6.zh-CN.md",
        ROOT / "UPDATE-v0.7.3.5.zh-CN.md",
        ROOT / "UPDATE-v0.7.3.4.zh-CN.md",
        ROOT / "UPDATE-v0.7.3.3.zh-CN.md",
        ROOT / "UPDATE-v0.7.3.2.zh-CN.md",
        ROOT / "UPDATE-v0.7.3.1.zh-CN.md",
        ROOT / "RELEASE.zh-CN.md",
        ROOT / "README.md",
        ROOT / "README.en.md",
        ROOT / "docs" / "ARCHITECTURE.zh-CN.md",
        ROOT / "docs" / "TROUBLESHOOTING.zh-CN.md",
    )

    current_documents = {
        "UPDATE-v0.7.4.28.zh-CN.md",
        "RELEASE.zh-CN.md",
        "README.md",
        "README.en.md",
        "TROUBLESHOOTING.zh-CN.md",
    }
    for path in documents:
        text = path.read_text(encoding="utf-8")
        if path.name in current_documents:
            assert "v0.7.4.19" in text, path
        elif path.name == "UPDATE-v0.7.4.7.zh-CN.md":
            assert "v0.7.4.6" in text, path
        elif path.name == "UPDATE-v0.7.4.6.zh-CN.md":
            assert "v0.7.4.5" in text, path
        elif path.name == "UPDATE-v0.7.4.5.zh-CN.md":
            assert "v0.7.4.4" in text, path
        elif path.name == "UPDATE-v0.7.4.4.zh-CN.md":
            assert "v0.7.4.3" in text, path
        elif path.name == "UPDATE-v0.7.4.3.zh-CN.md":
            assert "v0.7.4.2" in text, path
        elif path.name == "UPDATE-v0.7.4.2.zh-CN.md":
            assert "v0.7.4.1" in text, path
        elif path.name == "UPDATE-v0.7.4.1.zh-CN.md":
            assert "v0.7.4.0" in text, path
        elif path.name == "UPDATE-v0.7.4.27.zh-CN.md":
            assert "v0.7.4.19" in text, path
        else:
            assert "v0.7.3" in text, path
        if path.name == "README.en.md":
            assert "pre-upgrade cold backup" in text, path
            assert "online `diet_system backup`" in text, path
        else:
            assert "升级前冷备份" in text, path
            assert "在线 `diet_system backup`" in text, path
