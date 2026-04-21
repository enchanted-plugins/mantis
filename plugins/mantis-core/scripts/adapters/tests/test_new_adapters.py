"""Tests for Java, C++, Ruby, Shell, and Semgrep adapters.

Each adapter is shelled out via subprocess.run; we mock that and feed canned
output to verify parsing + mapping + security guards work.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parents[1]))  # plugins/mantis-core/scripts

from adapters import java, cpp, ruby, shell, semgrep  # noqa: E402


class _FakeCompleted:
    def __init__(self, stdout: str = "", stderr: str = "", returncode: int = 0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


class JavaAdapter(unittest.TestCase):
    def test_detect_missing_returns_none(self):
        with patch("adapters.java.detect_binary", return_value=None):
            self.assertIsNone(java.detect())

    def test_no_class_dir_returns_empty(self):
        with patch("adapters.java.detect_binary", return_value="/fake/spotbugs"), \
             patch("adapters.java._find_class_dir", return_value=None):
            self.assertEqual(java.analyze("/tmp/Foo.java"), [])

    def test_xml_parsed_and_np_rule_mapped(self):
        xml = (
            "<BugCollection>"
            '  <BugInstance type="NP_NULL_ON_SOME_PATH">'
            '    <SourceLine start="42" sourcepath="/tmp/Foo.java"/>'
            "  </BugInstance>"
            "</BugCollection>"
        )
        with patch("adapters.java.detect_binary", return_value="/fake/spotbugs"), \
             patch("adapters.java._find_class_dir", return_value=Path("/tmp/target/classes")), \
             patch("adapters.java.run_subprocess",
                   return_value=_FakeCompleted(stdout=xml)), \
             patch("adapters.java.load_registry",
                   return_value={"NP_NULL_ON_SOME_PATH": ("correctness_m1", "HIGH")}):
            flags = java.analyze("/tmp/Foo.java")
            self.assertEqual(len(flags), 1)
            self.assertEqual(flags[0].severity, "HIGH")
            self.assertEqual(flags[0].line, 42)

    def test_security_bucket_never_mapped(self):
        xml = (
            "<BugCollection>"
            '  <BugInstance type="LEAKED_TOKEN">'
            '    <SourceLine start="10" sourcepath="/tmp/Foo.java"/>'
            "  </BugInstance>"
            "</BugCollection>"
        )
        with patch("adapters.java.detect_binary", return_value="/fake/spotbugs"), \
             patch("adapters.java._find_class_dir", return_value=Path("/tmp/target/classes")), \
             patch("adapters.java.run_subprocess",
                   return_value=_FakeCompleted(stdout=xml)), \
             patch("adapters.java.load_registry",
                   return_value={"LEAKED_TOKEN": ("security_defer_to_reaper", "HIGH")}):
            self.assertEqual(java.analyze("/tmp/Foo.java"), [])


class CppAdapter(unittest.TestCase):
    def test_detect_missing(self):
        with patch("adapters.cpp.detect_binary", return_value=None):
            self.assertIsNone(cpp.detect())

    def test_regex_extracts_rule_and_maps(self):
        stderr = "/tmp/x.cpp:12:5: warning: use-after-move detected [bugprone-use-after-move]\n"
        with patch("adapters.cpp.detect_binary", return_value="/fake/ct"), \
             patch("adapters.cpp._find_compile_db", return_value=None), \
             patch("adapters.cpp.run_subprocess",
                   return_value=_FakeCompleted(stdout="", stderr=stderr)), \
             patch("adapters.cpp.load_registry",
                   return_value={"bugprone-use-after-move": ("correctness_m1", "HIGH")}):
            flags = cpp.analyze("/tmp/x.cpp")
            self.assertEqual(len(flags), 1)
            self.assertEqual(flags[0].severity, "HIGH")
            self.assertEqual(flags[0].line, 12)

    def test_security_guard_refuses(self):
        stderr = "/tmp/x.cpp:5:1: warning: xss risk [cert-msc50-cpp]\n"
        with patch("adapters.cpp.detect_binary", return_value="/fake/ct"), \
             patch("adapters.cpp._find_compile_db", return_value=None), \
             patch("adapters.cpp.run_subprocess",
                   return_value=_FakeCompleted(stdout="", stderr=stderr)), \
             patch("adapters.cpp.load_registry",
                   return_value={"cert-msc50-cpp": ("security_defer_to_reaper", "HIGH")}):
            self.assertEqual(cpp.analyze("/tmp/x.cpp"), [])


class RubyAdapter(unittest.TestCase):
    def test_detect_missing(self):
        with patch("adapters.ruby.detect_binary", return_value=None):
            self.assertIsNone(ruby.detect())

    def test_json_offenses_mapped(self):
        payload = json.dumps({
            "files": [{
                "path": "/tmp/foo.rb",
                "offenses": [{
                    "cop_name": "Lint/UnusedBlockArgument",
                    "severity": "warning",
                    "message": "Unused",
                    "location": {"line": 7},
                }],
            }],
        })
        with patch("adapters.ruby.detect_binary", return_value="/fake/rubocop"), \
             patch("adapters.ruby.run_subprocess",
                   return_value=_FakeCompleted(stdout=payload)), \
             patch("adapters.ruby.load_registry",
                   return_value={"Lint/UnusedBlockArgument": ("correctness_m1", "MED")}):
            flags = ruby.analyze("/tmp/foo.rb")
            self.assertEqual(len(flags), 1)
            self.assertEqual(flags[0].severity, "MED")

    def test_idiom_cop_not_m1(self):
        payload = json.dumps({
            "files": [{"path": "/tmp/foo.rb", "offenses": [{
                "cop_name": "Style/For", "severity": "convention",
                "message": "use each", "location": {"line": 3},
            }]}],
        })
        with patch("adapters.ruby.detect_binary", return_value="/fake/rubocop"), \
             patch("adapters.ruby.run_subprocess",
                   return_value=_FakeCompleted(stdout=payload)), \
             patch("adapters.ruby.load_registry",
                   return_value={"Style/For": ("idiom_m7", "LOW")}):
            self.assertEqual(ruby.analyze("/tmp/foo.rb"), [])


class ShellAdapter(unittest.TestCase):
    def test_detect_missing(self):
        with patch("adapters.shell.detect_binary", return_value=None):
            self.assertIsNone(shell.detect())

    def test_sc_code_mapped(self):
        payload = json.dumps({"comments": [
            {"file": "/tmp/x.sh", "line": 3, "code": 2046,
             "level": "warning", "message": "Quote this"},
        ]})
        with patch("adapters.shell.detect_binary", return_value="/fake/shellcheck"), \
             patch("adapters.shell.run_subprocess",
                   return_value=_FakeCompleted(stdout=payload)), \
             patch("adapters.shell.load_registry",
                   return_value={"SC2046": ("correctness_m1", "MED")}):
            flags = shell.analyze("/tmp/x.sh")
            self.assertEqual(len(flags), 1)
            self.assertEqual(flags[0].line, 3)

    def test_security_guard(self):
        payload = json.dumps({"comments": [
            {"file": "/tmp/x.sh", "line": 1, "code": 9999,
             "level": "error", "message": "credential exposure"},
        ]})
        with patch("adapters.shell.detect_binary", return_value="/fake/shellcheck"), \
             patch("adapters.shell.run_subprocess",
                   return_value=_FakeCompleted(stdout=payload)), \
             patch("adapters.shell.load_registry",
                   return_value={"SC9999": ("security_defer_to_reaper", "HIGH")}):
            self.assertEqual(shell.analyze("/tmp/x.sh"), [])


class SemgrepAdapter(unittest.TestCase):
    def test_detect_missing(self):
        with patch("adapters.semgrep.detect_binary", return_value=None):
            self.assertIsNone(semgrep.detect())

    def test_is_security_rule_dotted_paths(self):
        self.assertTrue(semgrep._is_security_rule("python.django.security.injection.sqli"))
        self.assertTrue(semgrep._is_security_rule("javascript.xss.dom-xss"))
        self.assertTrue(semgrep._is_security_rule("go.crypto.weak-hash"))
        self.assertFalse(semgrep._is_security_rule("python.django.correctness.missing-key"))

    def test_security_dotted_path_never_mapped(self):
        payload = json.dumps({"results": [{
            "check_id": "python.django.security.injection.sqli",
            "start": {"line": 10},
            "extra": {"severity": "ERROR", "message": "SQLi risk"},
        }]})
        with patch("adapters.semgrep.detect_binary", return_value="/fake/semgrep"), \
             patch("adapters.semgrep.run_subprocess",
                   return_value=_FakeCompleted(stdout=payload)), \
             patch("adapters.semgrep._load_framework_registry",
                   return_value={"python.django.security.injection.sqli": "correctness_m1"}):
            # Even though the registry bucket is correctness, the dotted-path guard refuses.
            self.assertEqual(semgrep.analyze("/tmp/views.py"), [])

    def test_offline_env_skips(self):
        import os
        with patch("adapters.semgrep.detect_binary", return_value="/fake/semgrep"), \
             patch.dict(os.environ, {"MANTIS_SEMGREP_OFFLINE": "1"}):
            self.assertEqual(semgrep.analyze("/tmp/views.py"), [])

    def test_correctness_rule_mapped(self):
        payload = json.dumps({"results": [{
            "check_id": "javascript.react.best-practice.react-missing-key",
            "start": {"line": 15},
            "extra": {"severity": "WARNING", "message": "Missing key"},
        }]})
        with patch("adapters.semgrep.detect_binary", return_value="/fake/semgrep"), \
             patch("adapters.semgrep.run_subprocess",
                   return_value=_FakeCompleted(stdout=payload)), \
             patch("adapters.semgrep._load_framework_registry",
                   return_value={"javascript.react.best-practice.react-missing-key": "correctness_m1"}):
            flags = semgrep.analyze("/tmp/App.jsx")
            self.assertEqual(len(flags), 1)
            self.assertEqual(flags[0].severity, "MED")


if __name__ == "__main__":
    unittest.main()
