"""
MCP (Model Context Protocol) settings section builders.

Phase 4 of settings.py modularization.
Extracts external MCP servers, Perplexity, Context7, MCP server, and A2A sections.
"""

import os
from typing import Any

from python.helpers import dotenv_manager as dotenv

from .base import (
    SettingsField,
    SettingsSection,
    SectionBuilderContext,
    API_KEY_PLACEHOLDER,
)


def build_mcp_client_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the External MCP Servers settings section."""
    settings = ctx.settings
    
    mcp_client_fields: list[SettingsField] = []

    mcp_client_fields.append(
        {
            "id": "mcp_servers_config",
            "title": "MCP Servers Configuration",
            "description": "External MCP servers can be configured here.",
            "type": "button",
            "value": "Open",
        }
    )

    mcp_client_fields.append(
        {
            "id": "mcp_servers",
            "title": "MCP Servers",
            "description": "(JSON list of) >> RemoteServer <<: [name, url, headers, timeout (opt), sse_read_timeout (opt), disabled (opt)] / >> Local Server <<: [name, command, args, env, encoding (opt), encoding_error_handler (opt), disabled (opt)]",
            "type": "textarea",
            "value": settings["mcp_servers"],
            "hidden": True,
        }
    )

    mcp_client_fields.append(
        {
            "id": "mcp_client_init_timeout",
            "title": "MCP Client Init Timeout",
            "description": "Timeout for MCP client initialization (in seconds). Higher values might be required for complex MCPs, but might also slowdown system startup.",
            "type": "number",
            "value": settings["mcp_client_init_timeout"],
        }
    )

    mcp_client_fields.append(
        {
            "id": "mcp_client_tool_timeout",
            "title": "MCP Client Tool Timeout",
            "description": "Timeout for MCP client tool execution. Higher values might be required for complex tools, but might also result in long responses with failing tools.",
            "type": "number",
            "value": settings["mcp_client_tool_timeout"],
        }
    )

    return {
        "id": "mcp_client",
        "title": "External MCP Servers",
        "description": "AGIX can use external MCP servers, local or remote as tools.",
        "fields": mcp_client_fields,
        "tab": "mcp",
    }


def build_perplexity_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Perplexity MCP (Built-in) settings section."""
    
    perplexity_fields: list[SettingsField] = []

    perplexity_fields.append(
        {
            "id": "perplexity_api_key",
            "title": "Perplexity API Key",
            "description": "API key for Perplexity MCP server. Perplexity provides AI-powered web search and research capabilities. Get your API key from <a href='https://www.perplexity.ai/settings/api' target='_blank'>Perplexity API Settings</a>.",
            "type": "text",
            "value": (API_KEY_PLACEHOLDER if dotenv.get_dotenv_value("PERPLEXITY_API_KEY") else ""),
        }
    )

    return {
        "id": "perplexity_mcp",
        "title": "Perplexity MCP (Built-in)",
        "description": "Perplexity MCP is a built-in AI research tool that enables agents to search the web and research solutions autonomously. When configured, agents can use Perplexity to find alternative approaches when stuck.",
        "fields": perplexity_fields,
        "tab": "mcp",
    }


def build_context7_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Context7 MCP (Built-in) settings section."""
    
    context7_fields: list[SettingsField] = []

    context7_fields.append(
        {
            "id": "context7_api_key",
            "title": "Context7 API Key",
            "description": "API key for Context7 MCP server. Context7 provides AI-powered documentation fetching and deep search capabilities. Get your API key from <a href='https://context7.com' target='_blank'>Context7 Dashboard</a>.",
            "type": "text",
            "value": (API_KEY_PLACEHOLDER if dotenv.get_dotenv_value("CONTEXT7_API_KEY") else ""),
        }
    )

    return {
        "id": "context7_mcp",
        "title": "Context7 MCP (Built-in)",
        "description": "Context7 MCP is a built-in documentation fetcher that enables agents to dynamically fetch and research libraries, APIs and frameworks. When configured, agents can use Context7 to read latest documentation on the fly.",
        "fields": context7_fields,
        "tab": "mcp",
    }


def build_mcp_server_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Alex MCP Server settings section."""
    settings = ctx.settings
    
    mcp_server_fields: list[SettingsField] = []

    mcp_server_fields.append(
        {
            "id": "mcp_server_enabled",
            "title": "Enable Alex MCP Server",
            "description": "Expose AGIX as an SSE/HTTP MCP server. This will make this Alex instance available to MCP clients.",
            "type": "switch",
            "value": settings["mcp_server_enabled"],
        }
    )

    mcp_server_fields.append(
        {
            "id": "mcp_server_token",
            "title": "MCP Server Token",
            "description": "Token for MCP server authentication.",
            "type": "text",
            "hidden": True,
            "value": settings["mcp_server_token"],
        }
    )

    return {
        "id": "mcp_server",
        "title": "Alex MCP Server",
        "description": "AGIX can be exposed as an SSE MCP server. See <a href=\"javascript:openModal('settings/mcp/server/example.html')\">connection example</a>.",
        "fields": mcp_server_fields,
        "tab": "mcp",
    }


def build_a2a_section(ctx: SectionBuilderContext) -> SettingsSection:
    """Build the Alex A2A Server settings section."""
    settings = ctx.settings
    
    a2a_fields: list[SettingsField] = []

    a2a_fields.append(
        {
            "id": "a2a_server_enabled",
            "title": "Enable A2A server",
            "description": "Expose AGIX as A2A server. This allows other agents to connect to Alex via A2A protocol.",
            "type": "switch",
            "value": settings["a2a_server_enabled"],
        }
    )

    return {
        "id": "a2a_server",
        "title": "Alex A2A Server",
        "description": "AGIX can be exposed as an A2A server. See <a href=\"javascript:openModal('settings/a2a/a2a-connection.html')\">connection example</a>.",
        "fields": a2a_fields,
        "tab": "mcp",
    }