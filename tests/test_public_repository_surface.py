from pathlib import Path
import json
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_public_repository_files_and_mit_metadata_exist() -> None:
    for name in ("LICENSE", "CHANGELOG.md", "SECURITY.md", "CONTRIBUTING.md"):
        assert (ROOT / name).is_file()
    assert "MIT License" in text("LICENSE")
    assert "Copyright (c) 2026 Aim996" in text("LICENSE")
    package = json.loads(text("package.json"))
    project = tomllib.loads(text("pyproject.toml"))
    assert package["private"] is True
    assert package["license"] == "MIT"
    assert project["project"]["license"] == "MIT"


def test_github_workflow_guide_is_a_safe_single_entrypoint() -> None:
    guide = text("GITHUB-WORKFLOW.zh-CN.md")
    for phrase in (
        "docs/PRODUCT-BEHAVIOR-INVARIANTS.zh-CN.md",
        "先判断是否值得修改",
        "授权矩阵",
        "推送不等于发布",
        "发布不等于部署",
        "不得覆盖",
        "失败即停止",
        "交接记录模板",
    ):
        assert phrase in guide
    for forbidden in (
        "EXAMPLE_SECRET_MARKER",
        "192.0.2.1",
        "TO" + "DO",
        "TB" + "D",
    ):
        assert forbidden not in guide


def test_readme_is_user_first_and_keeps_the_protected_receipt() -> None:
    readme = text("README.md")
    headings = [
        "## 当前版本与状态",
        "## 它适合谁",
        "## 核心能力",
        "## 实际回执示例",
        "## 最快开始",
        "## 系统要求",
        "## 数据安全、更新与回滚",
        "## 常见问题",
        "## 文档导航",
        "## 开发者入口",
        "## 许可证",
    ]
    positions = [readme.index(item) for item in headings]
    assert positions == sorted(positions)
    assert "Personal Diet Pantry v0.7.4.28" in readme
    assert "技术包版本 `0.8.28`" in readme
    assert "公开正式版" in readme
    assert "releases/tag/v0.7.4.28" in readme
    assert "发布准备中" not in readme
    assert "GitHub Release 尚未创建" not in readme
    assert "仓库暂时保持 Private" not in readme
    assert "已记录！火腿肠 1根 50克（估算）｜84.8 kcal" in readme
    assert "🔥84.8 / 1900 kcal +84.8kcal +4%" in readme
    assert "本仓库当前没有 `LICENSE`" not in readme


def test_current_release_documents_state_no_migration_and_formal_release() -> None:
    update = text("UPDATE-v0.7.4.28.zh-CN.md")
    release = text("RELEASE.zh-CN.md")
    for phrase in (
        "没有新增 migration",
        "0.7.4.19",
        "继续使用 migrations 001–021",
        "运行时业务行为没有变化",
        "GitHub Release",
    ):
        assert phrase in update
    assert release.startswith("# 食序管家（Personal Diet Pantry）v0.7.4.28\n")
    assert "personal-diet-pantry-0.7.4.28-installable.tgz" in release


def test_changelog_uses_product_versions_and_migration_labels() -> None:
    changelog = text("CHANGELOG.md")
    assert "## [0.7.4.28]" in changelog
    assert "### Changed" in changelog
    assert "### Security" in changelog
    assert "Migration: none" in changelog


def test_install_upgrade_and_release_entries_are_exact() -> None:
    install = text("docs/INSTALL.md")
    upgrade = text("docs/UPGRADING.md")
    releasing = text("docs/RELEASING.md")
    assert ">=22.22.3 <23 || >=24.15.0 <25 || >=25.9.0" in install
    assert "personal-diet-pantry-0.7.4.28-installable.tgz" in install
    assert "SHA256SUMS" in install
    assert "openclaw plugins install npm-pack:" in install
    assert "openclaw plugins enable personal-diet-pantry" in install
    assert "openclaw gateway restart" in install
    assert "openclaw plugins inspect personal-diet-pantry --runtime --json" in install
    assert "dataDir" in install
    assert "七类工具" in install
    assert "明确授权" in install
    assert "self_check" in install
    assert "零业务写入" in install
    assert "升级前冷备份" in upgrade
    assert "0.7.4.19" in upgrade
    assert "记录数量" in upgrade
    assert "git ls-remote --tags origin refs/tags/v0.7.4.28" in releasing
    assert "不得覆盖" in releasing
    assert "GitHub Release" in releasing


def test_ai_prompts_are_complete_and_have_no_placeholders() -> None:
    prompts = text("docs/AI-PROMPTS.zh-CN.md")
    for heading in (
        "## A. 全新安装提示词",
        "## B. 安全更新提示词",
        "## C. 安装验收提示词",
    ):
        assert heading in prompts
    assert prompts.count("```text") == 3
    assert "Aim996/personal-diet-pantry" in prompts
    assert "personal-diet-pantry-0.7.4.28-installable.tgz" in prompts
    assert "openclaw plugins inspect personal-diet-pantry --runtime --json" in prompts
    assert "openclaw plugins enable personal-diet-pantry" in prompts
    assert "用户明确授权" in prompts
    assert "零业务写入" in prompts
    assert "source.tar.gz" in prompts
    for forbidden in (
        "<项目",
        "<版本",
        "TBD",
        "TODO",
        "EXAMPLE_SECRET_MARKER",
        "192.0.2.1",
    ):
        assert forbidden not in prompts
