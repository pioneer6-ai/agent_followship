"""
Guard the documentation against drifting away from the code.

Every test here pins a class of defect that was actually found by hand:

* ``.env.example`` advertised ``SMTP_PRESET``, which no code read, so a clinic
  following the docs got no effect at all.
* ``.env.example`` declared seven variables twice, and a later empty declaration
  blanks an earlier populated one under ``load_env_file(override=True)``.
* The README told users to run ``python app.py`` -- a file that does not exist --
  and to open port 5000 while the dashboard listens on 8080.
* Six routes, including the booking flow, were missing from the API reference.

These are cheap static checks over the real files, so they cannot silently rot.
"""

import os
import re
from typing import Dict, List, Set, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DOC_FILES = ["README.md", "QUICKSTART.md", "ARCHITECTURE.md"]

# Variables a person may legitimately see in these docs that this project does
# not read itself. ``SSL_CERT_FILE`` is read by ``tools.tls`` (so it is *not*
# here); these are the environment variables of other software.
FOREIGN_ENV_VARS: Set[str] = {
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_PROFILE",
    "AWS_SESSION_TOKEN",
    "CLINIC_SETUP_FILE",
    "PYTHONPATH",
    "VIRTUAL_ENV",
}


def _read(relative: str) -> str:
    with open(os.path.join(PROJECT_ROOT, relative), "r", encoding="utf-8") as fh:
        return fh.read()


def _iter_source_files() -> List[str]:
    """Every Python file belonging to the project, tests excluded."""
    roots = ["agent", "core", "tools", "utils", "web", "scripts"]
    found: List[str] = []
    for root in roots:
        base = os.path.join(PROJECT_ROOT, root)
        for dirpath, _dirnames, filenames in os.walk(base):
            if "__pycache__" in dirpath:
                continue
            for name in filenames:
                if name.endswith(".py"):
                    found.append(os.path.join(dirpath, name))
    for top in ("hospital_setup.py", "demo.py"):
        path = os.path.join(PROJECT_ROOT, top)
        if os.path.exists(path):
            found.append(path)
    return found


def _all_upper_literals() -> Set[str]:
    """Upper-case string literals appearing anywhere in the project's code."""
    literals: Set[str] = set()
    for path in _iter_source_files():
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
        literals |= set(re.findall(r"""["']([A-Z][A-Z0-9_]{3,})["']""", text))
    return literals


def _declared_env_vars() -> Set[str]:
    """``NAME=`` declarations in ``.env.example``."""
    text = _read(".env.example")
    return set(re.findall(r"^\s*([A-Z][A-Z0-9_]{3,})\s*=", text, re.M))


def _duplicate_env_vars() -> Dict[str, int]:
    text = _read(".env.example")
    counts: Dict[str, int] = {}
    for name in re.findall(r"^\s*([A-Z][A-Z0-9_]{3,})\s*=", text, re.M):
        counts[name] = counts.get(name, 0) + 1
    return {name: n for name, n in counts.items() if n > 1}


def _code_routes() -> Set[Tuple[str, str]]:
    """``(method, path)`` for every Flask route, normalised to ``{param}``."""
    text = _read("web/app.py")
    routes: Set[Tuple[str, str]] = set()
    pattern = re.compile(
        r"@app\.route\(\s*[\"']([^\"']+)[\"']\s*(?:,\s*methods=\[([^\]]*)\])?\s*\)"
    )
    for path, methods in pattern.findall(text):
        path = re.sub(r"<[^>]+>", "{param}", path)
        if methods:
            for method in re.findall(r"[\"']([A-Z]+)[\"']", methods):
                routes.add((method, path))
        else:
            routes.add(("GET", path))
    return routes


def _documented_routes() -> Set[Tuple[str, str]]:
    """``(method, path)`` read from the API endpoint table in the README."""
    text = _read("README.md")
    routes: Set[Tuple[str, str]] = set()
    pattern = re.compile(
        r"^\|\s*(GET|POST|PUT|PATCH|DELETE)\s*\|\s*`([^`]+)`", re.M
    )
    for method, path in pattern.findall(text):
        path = re.sub(r"\{[^}]+\}", "{param}", path)
        routes.add((method, path))
    return routes


class TestEnvironmentTemplate:
    """The template must document what the code reads, and nothing more."""

    def test_the_template_is_not_empty(self):
        assert len(_declared_env_vars()) > 30

    def test_every_documented_variable_is_actually_read(self):
        """
        A variable in the template that no code mentions sends a clinic on a
        wild goose chase -- this is the ``SMTP_PRESET`` defect.
        """
        referenced = _all_upper_literals()
        dead = sorted(
            name
            for name in _declared_env_vars() - FOREIGN_ENV_VARS
            if name not in referenced
        )
        assert dead == [], f"documented but never read by any code: {dead}"

    def test_no_variable_is_declared_twice(self):
        """
        A later empty declaration silently blanks an earlier populated one when
        the file is loaded with ``override=True``.
        """
        assert _duplicate_env_vars() == {}

    def test_the_urgency_thresholds_are_documented(self):
        """Clinic-facing policy knobs must be visible without reading source."""
        declared = _declared_env_vars()
        assert "AGENT_HIGH_URGENCY_THRESHOLD_DAYS" in declared
        assert "AGENT_CRITICAL_URGENCY_THRESHOLD_DAYS" in declared

    def test_the_documented_policy_defaults_match_the_code(self):
        from core.config import ClinicPolicyConfig

        defaults = ClinicPolicyConfig()
        text = _read(".env.example")

        def documented(name: str) -> int:
            match = re.search(rf"^{name}=(\d+)", text, re.M)
            assert match, f"{name} is not declared with a numeric default"
            return int(match.group(1))

        assert documented("AGENT_HIGH_URGENCY_THRESHOLD_DAYS") == (
            defaults.high_urgency_threshold_days
        )
        assert documented("AGENT_CRITICAL_URGENCY_THRESHOLD_DAYS") == (
            defaults.critical_urgency_threshold_days
        )
        assert documented("AGENT_MAX_REMINDERS_BEFORE_ESCALATION") == (
            defaults.max_reminders_before_escalation
        )
        assert documented("AGENT_REMINDER_INTERVAL_DAYS") == (
            defaults.reminder_interval_days
        )

    def test_the_documented_aws_defaults_match_the_code(self):
        from tools.config import MessagingConfig

        config = MessagingConfig.from_env(env={})
        text = _read(".env.example")
        assert config.aws_sms_allowlist == [
            line.split("=", 1)[1]
            for line in text.splitlines()
            if line.startswith("AWS_SMS_ALLOWED_NUMBERS=")
        ]
        assert config.aws_email_allowlist == [
            line.split("=", 1)[1]
            for line in text.splitlines()
            if line.startswith("AWS_EMAIL_ALLOWED_ADDRESSES=")
        ]


class TestDocumentedEndpoints:
    """The API reference must match the routes the app actually serves."""

    def test_the_readme_documents_some_endpoints(self):
        assert len(_documented_routes()) >= 10

    def test_documented_endpoints_all_exist(self):
        invented = sorted(_documented_routes() - _code_routes())
        assert invented == [], f"documented but not implemented: {invented}"

    def test_every_route_is_documented(self):
        undocumented = sorted(_code_routes() - _documented_routes())
        assert undocumented == [], f"implemented but undocumented: {undocumented}"


class TestEntryPointsAndPorts:
    """Users copy these commands verbatim, so they must be runnable."""

    def test_the_docs_never_reference_a_missing_top_level_app(self):
        for name in DOC_FILES:
            text = _read(name)
            offenders = re.findall(r"python\s+app\.py", text)
            assert offenders == [], f"{name} tells users to run a missing file"

    def test_the_dashboard_file_exists_where_the_docs_say(self):
        assert os.path.exists(os.path.join(PROJECT_ROOT, "web", "app.py"))
        assert not os.path.exists(os.path.join(PROJECT_ROOT, "app.py"))

    def test_the_documented_port_matches_the_application(self):
        text = _read("web/app.py")
        match = re.search(r"^\s*port\s*=\s*(\d+)", text, re.M)
        assert match, "could not find the dashboard port"
        expected = match.group(1)
        for name in DOC_FILES:
            doc = _read(name)
            if "localhost:" not in doc:
                continue
            ports = set(re.findall(r"localhost:(\d+)", doc))
            assert ports <= {expected}, f"{name} names a stale port: {ports}"

    def test_no_doc_promises_an_unsupported_port_flag(self):
        """web/app.py has no argument parser, so --port is silently ignored."""
        app_source = _read("web/app.py")
        assert "argparse" not in app_source
        for name in DOC_FILES:
            doc = _read(name)
            assert not re.search(r"app\.py\s+--port", doc), (
                f"{name} suggests a --port flag the dashboard ignores"
            )


class TestMarkdownHygiene:
    """Cheap structural checks so the docs stay renderable."""

    def test_code_fences_are_balanced(self):
        for name in DOC_FILES:
            fences = _read(name).count("\n```")
            assert fences % 2 == 0, f"{name} has an odd number of code fences"

    def test_internal_anchors_resolve(self):
        """Every ``#section`` link must have a matching heading."""
        for name in DOC_FILES:
            text = _read(name)
            slugs = set()
            for line in text.splitlines():
                if line.startswith("#"):
                    title = line.lstrip("#").strip().lower()
                    title = re.sub(r"[^\w\s-]", "", title)
                    slugs.add(re.sub(r"\s+", "-", title))
            broken = sorted(
                anchor
                for anchor in re.findall(r"\]\(#([^)]+)\)", text)
                if anchor not in slugs
            )
            assert broken == [], f"{name} has broken anchors: {broken}"


class TestDocumentedTestCount:
    """
    ``ARCHITECTURE.md`` advertises the suite size, and it silently went stale
    (it read 525 while the suite had grown past 560), which makes the docs look
    unmaintained and hides how much is actually covered.

    The band is deliberately wide because this very test is part of the count it
    measures, so an exact-equality assertion would fail the moment it is added.
    A 39-test drift is what this catches; adding one test is not drift.
    """

    TOLERANCE = 12

    def test_the_advertised_suite_size_is_believable(self):
        import subprocess
        import sys

        result = subprocess.run(
            [sys.executable, "-m", "pytest", "tests/", "--collect-only", "-q"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        match = re.search(r"(\d+)\s+tests?\s+collected", result.stdout)
        assert match, f"could not count the collected tests:\n{result.stdout[-800:]}"
        actual = int(match.group(1))

        documented = re.search(
            r"pytest tests/ -q\s*#\s*(\d+)\s+tests?", _read("ARCHITECTURE.md")
        )
        assert documented, "ARCHITECTURE.md no longer advertises a test count"
        claimed = int(documented.group(1))

        assert abs(claimed - actual) <= self.TOLERANCE, (
            f"ARCHITECTURE.md claims {claimed} tests but {actual} are collected; "
            f"update the count (tolerance ±{self.TOLERANCE})"
        )
