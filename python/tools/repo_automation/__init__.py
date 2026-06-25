"""
Repository Automation Package

This package provides modularized functionality for GitHub and Forgejo
repository automation including issue management, analysis, builds, and sweeps.

Modules:
    - base: Constants and logger
    - providers: Credential classes and provider detection
    - github: GitHub API operations
    - forgejo: Forgejo API operations
    - git_ops: Git clone/update operations
    - analysis: Issue analysis and expert generation
    - utils: Utility functions for search, deduplication, etc.
    - build: TDD build task triggering
    - sweeps: Sweep coordination and status management
    - integration: Stage 4 multi-merge operations
    - deployment: Stage 5 cloud deployment
    - monitoring: Stage 6 health monitoring
"""

# Base exports
from .base import (
    DEFAULT_EXCLUDES,
    ROOT_INCLUDES,
    MAX_CODEBASE_WORDS,
    REPO_ACTIONS,
    logger,
)

# Provider exports
from .providers import (
    GitHubCredentials,
    ForgejoCredentials,
    detect_provider,
    detect_provider_from_params,
    load_github_credentials,
    load_forgejo_credentials,
)

# GitHub exports
from .github import (
    list_issues_github,
    get_issue_github,
    comment_github,
    create_issue_github,
    list_comments_raw_github,
    check_triage_status_github,
    list_branches_github,
)

# Forgejo exports
from .forgejo import (
    list_issues_raw_forgejo,
    list_comments_raw_forgejo,
    get_issue_forgejo,
    comment_forgejo,
    create_issue_forgejo,
    upload_attachment_forgejo as upload_attachment_forgejo_api,
    list_branches_forgejo,
)

# Git operations exports
from .git_ops import (
    get_authenticated_url,
    clone_or_update_repo,
    verify_clone_context,
    ensure_repo_context,
    remove_worktree,  # RCA-20260612 Issue 12: worktree cleanup
)

# Analysis exports
from .analysis import (
    parse_issue_text,
    validate_analysis,
    format_sources_for_prompt,
    extract_relevant_context,
    generate_expert_analysis,
    get_codebase_context,
    get_codebase_context_fallback,
)

# Utils exports
from .utils import (
    ripgrep_search,
    check_duplicate_comment,
    process_mermaid_blocks,
    generate_comment_hash,
    generate_hash_tag,
    extract_hash_from_body,
    has_agix_marker,
    generate_branch_name,
    is_final_summary,
    expand_body_variables,
    truncate_body,
    upload_attachment_forgejo,
    validate_issue_content,
)

# Build exports
from .build import (
    build_tdd_prompt,
    build_system_prompt_for_tdd,
    build_acknowledgment_comment,
    generate_implementation_code,
    generate_user_story_and_uat,
    check_build_authorization,
    parse_title_from_issue_text,
    construct_repo_url,
)

# Sweep exports
from .sweeps import (
    SweepCoordinator,
    parse_issue_numbers_from_list,
    build_sweep_summary,
    build_expert_sweep_summary,
    check_for_expert_analysis_tag,
    check_for_build_trigger,
    check_for_integration_trigger,
    check_for_merge_trigger,
    check_for_deploy_trigger,
    check_for_monitor_trigger,
)

# Integration exports
from .integration import (
    integration_manager,
    list_ready_for_merge,
    create_integration_branch,
    batch_merge,
)

# Deployment exports
from .deployment import (
    deploy_to_cloud,
)

# Monitoring exports
from .monitoring import (
    monitor_deployment_health,
    autonomous_remediation,
)

__all__ = [
    # Base
    "DEFAULT_EXCLUDES",
    "ROOT_INCLUDES",
    "MAX_CODEBASE_WORDS",
    "REPO_ACTIONS",
    "logger",
    
    # Providers
    "GitHubCredentials",
    "ForgejoCredentials",
    "detect_provider",
    "detect_provider_from_params",
    "load_github_credentials",
    "load_forgejo_credentials",
    
    # GitHub
    "list_issues_github",
    "get_issue_github",
    "comment_github",
    "create_issue_github",
    "list_comments_raw_github",
    "check_triage_status_github",
    "list_branches_github",
    
    # Forgejo
    "list_issues_raw_forgejo",
    "list_comments_raw_forgejo",
    "get_issue_forgejo",
    "comment_forgejo",
    "create_issue_forgejo",
    "upload_attachment_forgejo_api",
    "list_branches_forgejo",
    
    # Git ops
    "get_authenticated_url",
    "clone_or_update_repo",
    "verify_clone_context",
    "ensure_repo_context",
    
    # Analysis
    "parse_issue_text",
    "validate_analysis",
    "format_sources_for_prompt",
    "extract_relevant_context",
    "generate_expert_analysis",
    "get_codebase_context",
    "get_codebase_context_fallback",
    
    # Utils
    "ripgrep_search",
    "check_duplicate_comment",
    "process_mermaid_blocks",
    "generate_comment_hash",
    "generate_hash_tag",
    "extract_hash_from_body",
    "has_agix_marker",
    "generate_branch_name",
    "is_final_summary",
    "expand_body_variables",
    "truncate_body",
    "upload_attachment_forgejo",
    "validate_issue_content",
    
    # Build
    "build_tdd_prompt",
    "build_system_prompt_for_tdd",
    "build_acknowledgment_comment",
    "generate_implementation_code",
    "generate_user_story_and_uat",
    "check_build_authorization",
    "parse_title_from_issue_text",
    "construct_repo_url",
    
    # Sweeps
    "SweepCoordinator",
    "parse_issue_numbers_from_list",
    "build_sweep_summary",
    "build_expert_sweep_summary",
    "check_for_expert_analysis_tag",
    "check_for_build_trigger",
    "check_for_integration_trigger",
    "check_for_merge_trigger",
    "check_for_deploy_trigger",
    "check_for_monitor_trigger",
]