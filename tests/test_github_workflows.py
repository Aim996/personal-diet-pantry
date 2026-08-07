from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_ci_is_read_only_and_runs_all_source_gates() -> None:
    workflow = read(".github/workflows/ci.yml")
    for phrase in (
        "pull_request:",
        "branches: [main]",
        "workflow_dispatch:",
        "contents: read",
        "scan_sensitive_content.py",
        "npm run build",
        "vitest.mjs",
        "python -m pytest",
        "validate_skill.py",
        "release_audit.py",
        "npm pack --dry-run",
    ):
        assert phrase in workflow
    assert "contents: write" not in workflow
    assert "gh release create" not in workflow


def test_release_workflow_separates_build_from_publish() -> None:
    workflow = read(".github/workflows/release.yml")
    for phrase in (
        'tags: ["v*"]',
        "workflow_dispatch:",
        "check_release_ref.py",
        "scripts/build_release.py",
        "actions/upload-artifact@v7",
        "actions/download-artifact@v8",
        "contents: write",
        "gh release view",
        "gh release create",
        "--verify-tag",
    ):
        assert phrase in workflow
    for asset in (
        "personal-diet-pantry-0.7.5-installable.tgz",
        "personal-diet-pantry-0.7.5-source.tar.gz",
        "release-manifest.json",
        "TEST-SUMMARY-v0.7.5.zh-CN.md",
        "SHA256SUMS",
    ):
        assert asset in workflow
    assert "github.event_name == 'push'" in workflow
    assert "refs/tags/" in workflow


def test_community_templates_keep_reports_bounded_and_safe() -> None:
    bug = read(".github/ISSUE_TEMPLATE/bug_report.yml")
    feature = read(".github/ISSUE_TEMPLATE/feature_request.yml")
    pull_request = read(".github/pull_request_template.md")
    for phrase in (
        "产品版本",
        "技术包版本",
        "OpenClaw",
        "Node.js",
        "Python",
        "操作系统",
        "数据库",
        "令牌",
        "个人饮食数据",
    ):
        assert phrase in bug
    for phrase in ("用户场景", "用户价值", "当前替代方案", "受保护行为"):
        assert phrase in feature
    for phrase in (
        "PRODUCT-BEHAVIOR-INVARIANTS",
        "产品版本",
        "技术版本",
        "Migration",
        "数据安全",
        "回滚",
    ):
        assert phrase in pull_request


def test_first_user_runtime_verifies_the_default_safe_prompt_hook() -> None:
    verification = read("docker/first-user/verify.sh")

    assert '"before_prompt_build"' in verification
    assert "allowConversationAccess" not in verification
