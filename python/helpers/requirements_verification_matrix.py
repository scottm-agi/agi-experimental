"""
Requirements Verification Matrix

Aggregates verification results from Literals, BDD, TDD, and PDV layers
into a unified per-requirement score: STRONG, WEAK, or UNTESTED.

Part of the universal development philosophy:
  Prompt → Requirements → Testability → Code → Validate → Loop

Usage:
  matrix = build_verification_matrix(project_dir)
  # Returns: { "REQ-001": { "literals": True, "bdd": False, "tdd": True, "pdv": False, "overall": "STRONG" }, ... }

Scoring:
  - STRONG: requirement covered by ≥2 verification layers
  - WEAK: requirement covered by exactly 1 layer
  - UNTESTED: requirement has 0 coverage across all layers
"""

import json
import logging
import os
import re
from glob import glob
from typing import Any, Dict, List, Optional, Set

from python.helpers.planning_paths import get_path as _planning_path

logger = logging.getLogger("agix.verification_matrix")


# ─── REQ-ID Pattern ──────────────────────────────────────────────────────
_REQ_ID_PATTERN = re.compile(r"\[?(REQ-\d+)\]?")


# ─── Phase C: Test Coverage by Requirement ───────────────────────────────


def check_test_coverage_by_requirement(
    project_dir: str,
) -> Optional[Dict[str, Dict]]:
    """Scan test files for REQ-ID references and map coverage.

    Walks all test files (*.test.ts, *.test.js, *.test.tsx, *.spec.*,
    test_*.py, *_test.py) looking for [REQ-XXX] strings.

    Args:
        project_dir: Path to the project directory.

    Returns:
        Dict mapping REQ-ID → {covered: bool, test_files: [...]}
        None if no ledger found (not applicable).
    """
    ledger = _load_ledger(project_dir)
    if not ledger:
        return None

    requirements = ledger.get("requirements", [])
    if not requirements:
        return None

    # Initialize coverage map
    coverage = {}
    for req in requirements:
        req_id = req.get("id", "")
        if req_id:
            coverage[req_id] = {"covered": False, "test_files": []}

    # Find all test files
    test_files = _find_test_files(project_dir)

    # Scan each test file for REQ-ID references
    for test_file in test_files:
        try:
            with open(test_file, "r", errors="replace") as f:
                content = f.read()

            found_ids = set(_REQ_ID_PATTERN.findall(content))
            rel_path = os.path.relpath(test_file, project_dir)

            for req_id in found_ids:
                if req_id in coverage:
                    coverage[req_id]["covered"] = True
                    coverage[req_id]["test_files"].append(rel_path)
        except (IOError, OSError):
            continue

    # F-2F: Implementation existence verification
    # "testing must be equal to implementing — tests without implementation = FAIL"
    mapping = _load_requirement_test_mapping(project_dir)
    if mapping:
        for req_id, entry in coverage.items():
            if not entry["covered"]:
                continue  # Already not covered, no need to check
            if req_id not in mapping:
                continue  # No mapping entry, keep grep-based result
            map_entry = mapping[req_id]
            impl_file = map_entry.get("implementation_file", "")
            key_func = map_entry.get("key_function", "")
            if impl_file:
                if not _verify_implementation_exists(
                    project_dir, impl_file, key_func
                ):
                    # Override: tests exist but implementation does NOT
                    coverage[req_id]["covered"] = False
                    logger.warning(
                        f"[TDD COVERAGE] {req_id}: test files found but "
                        f"implementation file '{impl_file}' missing or "
                        f"key function '{key_func}' not found — marking uncovered"
                    )

    return coverage


# ─── Phase D: Verification Matrix ────────────────────────────────────────


def build_verification_matrix(project_dir: str) -> Dict[str, Dict]:
    """Build unified verification matrix across all layers.

    Aggregates:
      - TDD layer: test files with REQ-ID references
      - Literals layer: requirements_contract.json assertion results
      - BDD layer: bdd-scenarios.md REQ-ID references
      - PDV layer: gate_results.json from verification pipeline

    Scores each requirement:
      - STRONG: ≥2 layers pass
      - WEAK: exactly 1 layer passes
      - UNTESTED: 0 layers pass

    Args:
        project_dir: Path to the project directory.

    Returns:
        Dict mapping REQ-ID → {tdd: bool, literals: bool, bdd: bool,
                                pdv: bool, overall: "STRONG"|"WEAK"|"UNTESTED"}
    """
    ledger = _load_ledger(project_dir)
    if not ledger:
        return {}

    requirements = ledger.get("requirements", [])
    if not requirements:
        return {}

    # Gather all REQ IDs
    req_ids = [r.get("id", "") for r in requirements if r.get("id")]
    if not req_ids:
        return {}

    # Layer 1: TDD coverage
    tdd_coverage = check_test_coverage_by_requirement(project_dir) or {}

    # Layer 2: Literal assertions
    literal_coverage = _check_literal_coverage(project_dir)

    # Layer 3: BDD scenario coverage
    bdd_coverage = _check_bdd_coverage(project_dir)

    # Layer 4: PDV / gate results
    pdv_coverage = _check_pdv_coverage(project_dir)

    # Build matrix
    matrix = {}
    for req_id in req_ids:
        tdd_pass = tdd_coverage.get(req_id, {}).get("covered", False)
        literal_pass = literal_coverage.get(req_id, False)
        bdd_pass = req_id in bdd_coverage
        pdv_pass = pdv_coverage.get(req_id, False)

        layers_passing = sum([tdd_pass, literal_pass, bdd_pass, pdv_pass])

        if layers_passing >= 2:
            overall = "STRONG"
        elif layers_passing == 1:
            overall = "WEAK"
        else:
            overall = "UNTESTED"

        matrix[req_id] = {
            "tdd": tdd_pass,
            "literals": literal_pass,
            "bdd": bdd_pass,
            "pdv": pdv_pass,
            "overall": overall,
            "layers_passing": layers_passing,
        }

    # Persist
    _persist_matrix(project_dir, matrix)

    logger.info(
        f"[VERIFICATION MATRIX] {len(matrix)} requirements: "
        f"STRONG={sum(1 for v in matrix.values() if v['overall'] == 'STRONG')}, "
        f"WEAK={sum(1 for v in matrix.values() if v['overall'] == 'WEAK')}, "
        f"UNTESTED={sum(1 for v in matrix.values() if v['overall'] == 'UNTESTED')}"
    )

    return matrix


# ─── Gate Results Persistence ────────────────────────────────────────────


def persist_gate_results(
    project_dir: str,
    results: Dict[str, Dict[str, bool]],
) -> None:
    """Persist gate verification results for matrix consumption.

    Gate results come from the orchestrator's quality gates (PDV,
    route verification, etc.). They are stored in
    .agix.proj/verification/gate_results.json.

    Args:
        project_dir: Path to the project directory.
        results: Dict of gate name → {REQ-ID: pass/fail bool}.
            Example: {"pdv": {"REQ-001": True, "REQ-002": False}}
    """
    verification_dir = os.path.join(
        project_dir, ".agix.proj", "verification"
    )
    os.makedirs(verification_dir, exist_ok=True)

    gate_path = os.path.join(verification_dir, "gate_results.json")
    with open(gate_path, "w") as f:
        json.dump(results, f, indent=2)

    logger.info(
        f"[GATE RESULTS] Persisted gate results to {gate_path}"
    )


# ─── Internal Helpers ────────────────────────────────────────────────────


def _load_ledger(project_dir: str) -> Optional[Dict]:
    """Load requirements ledger from project directory."""
    candidates = [
        _planning_path(project_dir, "requirements_ledger"),
        os.path.join(project_dir, ".agix.proj", "requirements_ledger.json"),
        os.path.join(project_dir, ".agix.proj", "requirements-ledger.json"),
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                with open(path, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return None
    return None


def _find_test_files(project_dir: str) -> List[str]:
    """Find all test files in a project directory.

    Patterns:
      - **/*.test.ts, **/*.test.tsx, **/*.test.js, **/*.test.jsx
      - **/*.spec.ts, **/*.spec.tsx, **/*.spec.js, **/*.spec.jsx
      - **/test_*.py, **/*_test.py
    """
    test_patterns = [
        "**/*.test.ts", "**/*.test.tsx", "**/*.test.js", "**/*.test.jsx",
        "**/*.spec.ts", "**/*.spec.tsx", "**/*.spec.js", "**/*.spec.jsx",
        "**/test_*.py", "**/*_test.py",
    ]
    files = []
    for pattern in test_patterns:
        files.extend(glob(os.path.join(project_dir, pattern), recursive=True))

    # Exclude node_modules, .git, etc.
    excluded_dirs = {"node_modules", ".git", ".next", "dist", "build"}
    return [
        f for f in files
        if not any(ex in f.split(os.sep) for ex in excluded_dirs)
    ]


def _check_literal_coverage(project_dir: str) -> Dict[str, bool]:
    """Check literal assertion results from requirements_contract.json.

    GAP-3 FIX: If pre-computed contract results don't exist, invoke
    contract_assertion_runner.search_literal to generate them live.

    Returns:
        Dict mapping REQ-ID → True if literal was found, False otherwise.
    """
    contract_path = os.path.join(
        project_dir, ".agix.proj", "requirements_contract.json"
    )

    # If pre-computed results exist, use them
    if os.path.exists(contract_path):
        try:
            with open(contract_path, "r") as f:
                contract = json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

        result = {}
        for assertion in contract.get("assertions", []):
            req_id = assertion.get("requirement_id", "")
            found = assertion.get("found", False)
            if req_id:
                result[req_id] = found
        return result

    # GAP-3 FIX: Try to run contract_assertion_runner live if available
    try:
        from python.helpers.contract_assertion_runner import search_literal

        ledger = _load_ledger(project_dir)
        if not ledger:
            return {}

        result = {}
        for req in ledger.get("requirements", []):
            req_id = req.get("id", "")
            literals = req.get("literals", [])
            if not req_id or not literals:
                continue

            # Check if any literal is found in source
            any_found = False
            for literal in literals:
                if isinstance(literal, str) and literal.strip():
                    found = search_literal(project_dir, literal)
                    if found:
                        any_found = True
                        break
            result[req_id] = any_found

        return result
    except ImportError:
        logger.debug(
            "[LITERAL COVERAGE] contract_assertion_runner not available — "
            "skipping live literal verification"
        )
        return {}


def _check_bdd_coverage(project_dir: str) -> Set[str]:
    """Scan BDD scenario files for REQ-ID references.

    Returns:
        Set of REQ-IDs found in BDD scenario files.
    """
    bdd_candidates = [
        os.path.join(project_dir, "docs", "bdd-scenarios.md"),
        os.path.join(project_dir, "docs", "bdd-scenarios-skeleton.md"),
    ]
    found_ids: Set[str] = set()

    for bdd_path in bdd_candidates:
        if os.path.exists(bdd_path):
            try:
                with open(bdd_path, "r") as f:
                    content = f.read()
                found_ids.update(_REQ_ID_PATTERN.findall(content))
            except (IOError, OSError):
                continue

    # G-04 (RCA-316b): Warn when web project lacks BDD scenarios.
    # Combined with G-01 (blocks WEAK), this ensures web projects
    # without BDD can't reach STRONG status → delivery is blocked.
    if not found_ids:
        has_pkg = os.path.exists(os.path.join(project_dir, "package.json"))
        if has_pkg:
            logger.warning(
                "[BDD COVERAGE] Web project detected but no bdd-scenarios.md found. "
                "All requirements will lack BDD coverage layer."
            )

    return found_ids


# ─── F-2D: Mapping Template Loading ──────────────────────────────────────


def _load_requirement_test_mapping(project_dir: str) -> Dict:
    """Load requirement_test_mapping.json from .agix.proj/ directory.

    F-2D: The mapping file maps REQ-IDs to their test files,
    implementation files, and key functions. Format:

        {
            "REQ-001": {
                "test_file": "__tests__/outreach.test.ts",
                "implementation_file": "src/lib/outreach.ts",
                "key_function": "generateDripSequence"
            }
        }

    Args:
        project_dir: Path to the project directory.

    Returns:
        Dict mapping REQ-ID → mapping entry, or empty dict if not found.
    """
    mapping_path = os.path.join(
        project_dir, ".agix.proj", "requirement_test_mapping.json"
    )
    if not os.path.exists(mapping_path):
        return {}

    try:
        with open(mapping_path, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except (json.JSONDecodeError, IOError, OSError):
        logger.debug(
            f"[MAPPING] Could not load requirement_test_mapping.json: {mapping_path}"
        )
        return {}


# ─── F-2F: Implementation Existence Verification ────────────────────────


def _verify_implementation_exists(
    project_dir: str,
    implementation_file: str,
    key_function: str,
) -> bool:
    """Verify that an implementation file exists and contains a key function.

    F-2F: "testing must be equal to implementing — tests without implementation = FAIL"

    Args:
        project_dir: Path to the project directory.
        implementation_file: Relative path to the implementation file.
        key_function: Expected function/export name in the file.
            If empty, only checks file existence.

    Returns:
        True if the file exists AND (key_function is empty OR file contains key_function).
    """
    impl_path = os.path.join(project_dir, implementation_file)
    if not os.path.exists(impl_path):
        return False

    # If no key function specified, file existence is sufficient
    if not key_function:
        return True

    # Check if the file contains the key function
    try:
        with open(impl_path, "r", errors="replace") as f:
            content = f.read()
        return key_function in content
    except (IOError, OSError):
        return False


# ─── F-2B: BDD Scenario Field Parsing ────────────────────────────────────


# Patterns for BDD scenario parsing
_BDD_SCENARIO_PATTERN = re.compile(
    r"^##\s+Scenario:\s+(.+)$", re.MULTILINE
)
_BDD_TEST_FILE_PATTERN = re.compile(
    r"^\*\*Test File\*\*:\s*(.+)$", re.MULTILINE
)
_BDD_IMPL_FILE_PATTERN = re.compile(
    r"^\*\*Implementation File\*\*:\s*(.+)$", re.MULTILINE
)


def _parse_bdd_scenarios(project_dir: str) -> List[Dict]:
    """Parse BDD scenario blocks and extract Test File / Implementation File.

    F-2B: Parses bdd-scenarios.md for scenario blocks containing:
        ## Scenario: <name> [REQ-XXX]
        ...
        **Test File**: <path>
        **Implementation File**: <path>

    Args:
        project_dir: Path to the project directory.

    Returns:
        List of scenario dicts with keys:
            req_ids: List[str], test_file: str, implementation_file: str, name: str
    """
    bdd_candidates = [
        os.path.join(project_dir, "docs", "bdd-scenarios.md"),
        os.path.join(project_dir, "docs", "bdd-scenarios-skeleton.md"),
    ]

    content = ""
    for bdd_path in bdd_candidates:
        if os.path.exists(bdd_path):
            try:
                with open(bdd_path, "r") as f:
                    content = f.read()
                break
            except (IOError, OSError):
                continue

    if not content:
        return []

    # Split into scenario blocks by "## Scenario:" headers
    # Each block starts at a ## Scenario line and ends before the next one
    scenario_starts = list(_BDD_SCENARIO_PATTERN.finditer(content))
    if not scenario_starts:
        return []

    scenarios = []
    for i, match in enumerate(scenario_starts):
        start = match.start()
        end = scenario_starts[i + 1].start() if i + 1 < len(scenario_starts) else len(content)
        block = content[start:end]

        # Extract scenario name (the header line)
        name = match.group(1).strip()

        # Extract REQ-IDs from the scenario header
        req_ids = _REQ_ID_PATTERN.findall(name)

        # Extract **Test File**: field
        test_file_match = _BDD_TEST_FILE_PATTERN.search(block)
        test_file = test_file_match.group(1).strip() if test_file_match else ""

        # Extract **Implementation File**: field
        impl_file_match = _BDD_IMPL_FILE_PATTERN.search(block)
        impl_file = impl_file_match.group(1).strip() if impl_file_match else ""

        scenarios.append({
            "name": name,
            "req_ids": req_ids,
            "test_file": test_file,
            "implementation_file": impl_file,
        })

    return scenarios


# ─── F-2E: BDD Hard-Linked TDD Verification ─────────────────────────────

# Assertion patterns for detecting real test assertions
_ASSERTION_PATTERNS = re.compile(
    r"(expect\(|assert[.\(]|should[.\(]|toBe|toEqual|toMatch|toContain"
    r"|assertTrue|assertFalse|assertEqual|assertIn|pytest\.raises)"
)


def _check_bdd_coverage_hardlinked(project_dir: str) -> Dict[str, bool]:
    """Check BDD coverage with hard-linked TDD test verification.

    F-2E: For each BDD scenario, ALL 4 layers must pass:
        1. Test file exists
        2. Test file has real assertions
        3. Implementation file exists
        4. Implementation file has key function (if specified in mapping)

    Args:
        project_dir: Path to the project directory.

    Returns:
        Dict mapping REQ-ID → True if all 4 layers pass.
    """
    scenarios = _parse_bdd_scenarios(project_dir)
    if not scenarios:
        return {}

    result: Dict[str, bool] = {}

    for scenario in scenarios:
        test_file = scenario.get("test_file", "")
        impl_file = scenario.get("implementation_file", "")
        req_ids = scenario.get("req_ids", [])

        if not req_ids:
            continue

        # Layer 1: Test file exists
        test_path = os.path.join(project_dir, test_file) if test_file else ""
        test_exists = bool(test_path) and os.path.exists(test_path)

        # Layer 2: Test file has assertions
        has_assertion = False
        if test_exists:
            try:
                with open(test_path, "r", errors="replace") as f:
                    test_content = f.read()
                has_assertion = bool(_ASSERTION_PATTERNS.search(test_content))
            except (IOError, OSError):
                pass

        # Layer 3: Implementation file exists
        impl_path = os.path.join(project_dir, impl_file) if impl_file else ""
        impl_exists = bool(impl_path) and os.path.exists(impl_path)

        # Layer 4: Implementation has key function (use mapping if available)
        impl_has_function = impl_exists  # Default: file existence is enough
        if impl_exists:
            mapping = _load_requirement_test_mapping(project_dir)
            for rid in req_ids:
                if rid in mapping:
                    key_func = mapping[rid].get("key_function", "")
                    if key_func:
                        impl_has_function = _verify_implementation_exists(
                            project_dir, impl_file, key_func
                        )
                    break

        # ALL 4 layers must pass
        all_pass = test_exists and has_assertion and impl_exists and impl_has_function

        for rid in req_ids:
            result[rid] = all_pass

    return result


def _persist_matrix(project_dir: str, matrix: Dict) -> None:
    """Persist verification matrix to project .agix.proj/."""
    proj_dir = os.path.join(project_dir, ".agix.proj")
    os.makedirs(proj_dir, exist_ok=True)
    matrix_path = os.path.join(proj_dir, "verification_matrix.json")
    with open(matrix_path, "w") as f:
        json.dump(matrix, f, indent=2)


def _check_pdv_coverage(project_dir: str) -> Dict[str, bool]:
    """Check PDV gate results from gate_results.json.

    Reads .agix.proj/verification/gate_results.json and extracts
    per-requirement pass/fail from the 'pdv' and 'route_verification'
    gate sections.

    Returns:
        Dict mapping REQ-ID → True if any gate marks it passed.
    """
    gate_path = os.path.join(
        project_dir, ".agix.proj", "verification", "gate_results.json"
    )
    if not os.path.exists(gate_path):
        return {}

    try:
        with open(gate_path, "r") as f:
            gate_results = json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}

    result: Dict[str, bool] = {}

    # Aggregate across all gate sections
    for gate_name, gate_data in gate_results.items():
        if not isinstance(gate_data, dict):
            continue
        for req_id, passed in gate_data.items():
            if isinstance(passed, bool) and passed:
                result[req_id] = True
            elif req_id not in result:
                result[req_id] = False

    return result
