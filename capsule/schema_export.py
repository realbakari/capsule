"""JSON Schema Exporter for Capsule definitions.

Exports Draft-07 compliant JSON Schemas for:
  1. SKILL.md frontmatter metadata
  2. evals.json test suite definitions
  3. capsule-index.json run contexts

Allows IDEs (VS Code, Cursor) to provide instant autocomplete & validation.
"""

from __future__ import annotations

import json
from pathlib import Path


def skill_frontmatter_schema() -> dict:
    """JSON Schema for SKILL.md frontmatter."""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "CapsuleSkillFrontmatter",
        "type": "object",
        "required": ["name", "description"],
        "properties": {
            "name": {
                "type": "string",
                "pattern": "^[a-z0-9]+(-[a-z0-9]+)*$",
                "description": "Kebab-case skill identifier name.",
            },
            "description": {
                "type": "string",
                "description": "Summary of what the skill does and when the agent should trigger it.",
            },
            "license": {
                "type": "string",
                "default": "apache-2.0",
                "description": "License governing skill redistribution (e.g. apache-2.0, MIT).",
            },
            "lifecycle": {
                "type": "string",
                "enum": ["stable", "in-progress", "deprecated"],
                "default": "stable",
                "description": "Lifecycle stage of the skill.",
            },
            "compatibility": {
                "type": "string",
                "description": "Host/model compatibility notes (e.g. Claude Code 3.5+, Cursor).",
            },
            "allowed-tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of tool names the skill is permitted to invoke.",
            },
        },
        "additionalProperties": False,
    }


def evals_schema() -> dict:
    """JSON Schema for evals.json test suite files."""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "CapsuleEvalSuite",
        "type": "object",
        "required": ["skill_name", "cases"],
        "properties": {
            "skill_name": {"type": "string"},
            "version": {"type": "string", "default": "1"},
            "cases": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["id", "prompt", "assertions"],
                    "properties": {
                        "id": {"type": "string"},
                        "prompt": {"type": "string"},
                        "description": {"type": "string"},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "assertions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "required": ["kind", "pattern"],
                                "properties": {
                                    "kind": {
                                        "type": "string",
                                        "enum": ["contains", "not_contains", "regex", "not_regex"],
                                    },
                                    "pattern": {"type": "string"},
                                    "message": {"type": "string"},
                                },
                            },
                        },
                    },
                },
            },
        },
    }


def run_context_schema() -> dict:
    """JSON Schema for capsule-index.json files."""
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "CapsuleRunContext",
        "type": "object",
        "required": ["roots", "records", "built_at"],
        "properties": {
            "roots": {
                "type": "array",
                "items": {"type": "string"},
            },
            "built_at": {"type": "string"},
            "records": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": ["source_type", "source_path", "name", "category", "purpose"],
                    "properties": {
                        "source_type": {
                            "type": "string",
                            "enum": ["skill", "doc", "instruction", "registry"],
                        },
                        "source_path": {"type": "string"},
                        "name": {"type": "string"},
                        "category": {"type": "string"},
                        "purpose": {"type": "string"},
                        "lifecycle": {
                            "type": "string",
                            "enum": ["stable", "in-progress", "deprecated"],
                        },
                        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "license_class": {"type": "string"},
                        "reconstructable": {"type": "boolean"},
                    },
                },
            },
        },
    }


def export_schemas(output_dir: str | Path) -> dict[str, str]:
    """Export all JSON schema files into output_dir."""
    dest = Path(output_dir)
    dest.mkdir(parents=True, exist_ok=True)

    schemas = {
        "skill-frontmatter.schema.json": skill_frontmatter_schema(),
        "evals.schema.json": evals_schema(),
        "capsule-index.schema.json": run_context_schema(),
    }

    out_paths = {}
    for filename, schema_obj in schemas.items():
        filepath = dest / filename
        content = json.dumps(schema_obj, indent=2)
        filepath.write_text(content)
        out_paths[filename] = str(filepath)

    return out_paths
