import json
import os
import stat
import sys
import tempfile
import time
import unittest
from pathlib import Path

from bookmark_wiki import subscription


class SubscriptionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.schema = self.root / "schema.json"
        self.schema.write_text(json.dumps(subscription.MODEL_RESPONSE_SCHEMA), encoding="utf-8")
        self.executable = self.root / "codex"

    def tearDown(self):
        self.tmp.cleanup()

    def _command(self, script: str, images=()):
        self.executable.write_text(f"#!{sys.executable}\n{script}\n", encoding="utf-8")
        self.executable.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
        for image in images:
            Path(image).write_bytes(b"synthetic image")
        return subscription.build_codex_command(str(self.executable), str(self.schema), list(images))

    def _run(self, script: str, *, prompt="synthetic prompt", timeout=5, output_cap=1_000_000, images=()):
        command = self._command(script, images)
        env = subscription.build_child_env(str(self.root / "home"), str(self.root / "tmp"))
        Path(env["TMPDIR"]).mkdir(exist_ok=True)
        return subscription.run_codex(
            command,
            prompt,
            env,
            cwd=self.root,
            attempt_id="model-000001",
            timeout=timeout,
            output_cap=output_cap,
        )

    @staticmethod
    def _jsonl(response, *, usage=None, provider="openai", model="gpt-6-sol"):
        events = [
            {"type": "thread.started", "provider": provider, "model": model},
            {"type": "turn.started"},
            {
                "type": "item.completed",
                "item": {"id": "item-1", "type": "agent_message", "text": json.dumps(response)},
            },
            {"type": "turn.completed", **({"usage": usage} if usage is not None else {})},
        ]
        return "".join(json.dumps(event) + "\n" for event in events)

    def test_environment_and_command_use_exact_sanitized_contract(self):
        env = subscription.build_child_env(home="/synthetic/home", tmpdir="/synthetic/tmp")
        self.assertEqual(set(env), {"HOME", "CODEX_HOME", "PATH", "TMPDIR", "LANG", "LC_ALL"})
        self.assertEqual(env["CODEX_HOME"], "/synthetic/home/.codex")
        self.assertNotIn("OPENAI_API_KEY", env)
        command = self._command("import sys; sys.stdin.buffer.read()")
        self.assertIn("--ignore-user-config", command)
        self.assertIn("--ignore-rules", command)
        self.assertIn("--strict-config", command)
        self.assertIn("--ephemeral", command)
        self.assertIn("--output-schema", command)
        self.assertIn('model_provider="openai"', command)
        self.assertIn('forced_login_method="chatgpt"', command)
        config_text = " ".join(command)
        self.assertIn('web_search="disabled"', command)
        self.assertNotIn("features.web_search_request=false", config_text)
        self.assertIn('mcp_servers={}', command)
        features = set(subscription.DISABLED_FEATURES)
        for feature in features:
            self.assertIn(f"features.{feature}=false", config_text)
            self.assertNotIn(f"-c {feature}=false", config_text)
        self.assertNotIn("--disable", command)

    def test_model_response_schema_is_strict_and_validator_handles_boundaries(self):
        def assert_strict(node):
            if isinstance(node, dict):
                if node.get("type") == "object":
                    self.assertIs(node.get("additionalProperties"), False)
                for value in node.values():
                    assert_strict(value)
            elif isinstance(node, list):
                for value in node:
                    assert_strict(value)

        assert_strict(subscription.MODEL_RESPONSE_SCHEMA)
        self.assertNotIn("uniqueItems", json.dumps(subscription.MODEL_RESPONSE_SCHEMA))
        response = {
            "schema_version": 1,
            "results": [
                {
                    "unit_id": "unit-1",
                    "outcome": "succeeded_no_findings",
                    "findings": [],
                    "reason": "No bounded claim.",
                    "limitations": ["synthetic fixture"],
                },
                {
                    "unit_id": "unit-2",
                    "outcome": "succeeded_findings",
                    "findings": [{"claim": "A synthetic claim.", "evidence_refs": ["artifact-002"]}],
                    "reason": "One cited finding.",
                    "limitations": [],
                },
            ],
        }
        self.assertEqual(
            subscription.validate_model_response(
                response, ["unit-1", "unit-2"], {"unit-1": ["artifact-001"], "unit-2": ["artifact-002"]}
            ),
            response,
        )
        bad_responses = [
            {**response, "schema_version": True},
            {**response, "results": [dict(response["results"][0], reason="   "), response["results"][1]]},
            {**response, "results": [dict(response["results"][0], limitations=[" "]), response["results"][1]]},
            {**response, "results": [response["results"][0], dict(response["results"][1], findings=[{"claim": "x", "evidence_refs": [["artifact-002"]]}])]},
            {**response, "results": [response["results"][0], dict(response["results"][1], findings=[{"claim": "x", "evidence_refs": ["artifact-002", "artifact-002"]}])]},
        ]
        for bad in bad_responses:
            with self.assertRaises(subscription.SchemaError):
                subscription.validate_model_response(bad, ["unit-1", "unit-2"], {"unit-1": ["artifact-001"], "unit-2": ["artifact-002"]})

    def test_citation_reuse_is_per_finding_list_not_across_findings(self):
        response = {
            "schema_version": 1,
            "results": [{
                "unit_id": "screenshot-image",
                "outcome": "succeeded_findings",
                "findings": [
                    {"claim": "First bounded claim.", "evidence_refs": ["artifact-002"]},
                    {"claim": "Second bounded claim.", "evidence_refs": ["artifact-002"]},
                ],
                "reason": "Synthetic structural fixture.",
                "limitations": ["No semantic inspection."],
            }],
        }
        self.assertIs(
            subscription.validate_model_response(
                response, ["screenshot-image"], {"screenshot-image": ["artifact-001", "artifact-002"]}
            ),
            response,
        )

        invalid_responses = [
            {**response, "results": [dict(response["results"][0], findings=[
                {"claim": "Repeated in one list.", "evidence_refs": ["artifact-002", "artifact-002"]},
            ])]},
            {**response, "results": [dict(response["results"][0], findings=[
                {"claim": "Unknown artifact.", "evidence_refs": ["artifact-999"]},
            ])]},
            {**response, "results": [dict(response["results"][0], findings=[
                {"claim": "Cross-unit artifact.", "evidence_refs": ["artifact-other-unit"]},
            ])]},
            {"schema_version": 1, "results": [{"unit_id": "screenshot-image", "outcome": "succeeded_findings", "findings": [{"claim": 7, "evidence_refs": ["artifact-002"]}], "reason": "Malformed.", "limitations": []}]},
        ]
        for invalid in invalid_responses:
            with self.assertRaises(subscription.SchemaError):
                subscription.validate_model_response(
                    invalid, ["screenshot-image"], {"screenshot-image": ["artifact-001", "artifact-002"]}
                )

    def test_parser_accepts_only_the_known_web_search_startup_diagnostic(self):
        response = {"schema_version": 1, "results": []}
        advisory = {
            "type": "item.completed",
            "item": {
                "type": "error",
                "message": "`[features].web_search_request` is deprecated because web search is enabled by default. (Set `web_search` to `\"live\"`, `\"indexed\"`, `\"cached\"`, or `\"disabled\"` at the top level (or under a profile) in config.toml if you want to override it.)"
            },
        }
        raw = "\n".join([json.dumps({"type": "thread.started", "provider": "openai", "model": "gpt-6-sol"}), json.dumps(advisory), json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(response)}}), json.dumps({"type": "turn.completed"})]) + "\n"
        parsed = subscription.parse_jsonl(raw)
        self.assertEqual(parsed.response, response)
        for message in ("unknown startup warning", "authentication required", "quota exhausted", "prefix `" + advisory["item"]["message"], advisory["item"]["message"] + " suffix", advisory["item"]["message"].replace("[features]", "features"), advisory["item"]["message"].replace("deprecated", "DEPRECATED")):
            bad = dict(advisory, item={"type": "error", "message": message})
            bad_raw = raw.replace(json.dumps(advisory), json.dumps(bad))
            with self.assertRaises(subscription.ProtocolError):
                subscription.parse_jsonl(bad_raw)
        tool = dict(advisory, item={"type": "command_execution", "command": "unsafe"})
        with self.assertRaises(subscription.ProtocolError) as raised:
            subscription.parse_jsonl(raw.replace(json.dumps(advisory), json.dumps(tool)))
        self.assertEqual(str(raised.exception), "unexpected_tool")

    def test_parser_requires_actual_codex_agent_message_and_turn_completion(self):
        response = {"schema_version": 1, "results": []}
        parsed = subscription.parse_jsonl(self._jsonl(response, usage={"input_tokens": 4, "output_tokens": 2}))
        self.assertEqual(parsed.response, response)
        self.assertEqual(parsed.usage["input_tokens"], 4)
        self.assertIsNone(subscription.parse_jsonl(self._jsonl(response)).usage)
        made_up = json.dumps({"type": "final", "response": response})
        with self.assertRaises(subscription.ProtocolError):
            subscription.parse_jsonl(made_up)
        for raw in (
            "{",
            json.dumps({"type": "item.completed", "item": {"type": "command_execution"}}),
            self._jsonl(response, provider="other"),
            self._jsonl(response, model="wrong-model"),
            self._jsonl(response).replace('"turn.completed"', '"turn.failed"', 1),
            self._jsonl(response).replace('"item.completed"', '"error"', 1),
        ):
            with self.assertRaises(subscription.ProtocolError):
                subscription.parse_jsonl(raw)

    def test_runner_streams_stdin_and_stops_on_success_timeout_overflow(self):
        response = {"schema_version": 1, "results": []}
        success = self._run(f"import sys; print({self._jsonl(response)!r}, end='', flush=True)")
        self.assertEqual(success.status, "candidate")
        self.assertIsNone(success.usage)
        timeout = self._run("import time; time.sleep(5)", prompt="x" * 60000, timeout=0.05)
        self.assertEqual(timeout.error_code, "timeout")
        overflow = self._run("import sys; sys.stdout.write('x' * 10000); sys.stdout.flush()", output_cap=1000)
        self.assertEqual(overflow.error_code, "output_overflow")
        self.assertLessEqual(len(overflow.stdout) + len(overflow.stderr), 1000)

    def test_runner_stops_unexpected_events_and_bad_utf8_while_streaming(self):
        tool_event = json.dumps({"type": "item.completed", "item": {"type": "command_execution", "command": "unsafe"}})
        result = self._run(f"import sys,time; print({tool_event!r}, flush=True); time.sleep(5)", timeout=2)
        self.assertEqual(result.error_code, "unexpected_tool")
        self.assertLess(result.wall_seconds, 1.5)
        malformed = self._run("import sys,time; sys.stdout.buffer.write(b'\\xff\\n'); sys.stdout.flush(); time.sleep(5)", timeout=2)
        self.assertEqual(malformed.error_code, "malformed_response")

    def test_group_cleanup_kills_grandchild_after_leader_exits(self):
        marker = self.root / "grandchild-survived"
        script = (
            "import os,time; "
            f"pid=os.fork(); "
            f"(time.sleep(0.5), open({str(marker)!r}, 'w').write('survived')) if pid == 0 else os._exit(0)"
        )
        result = self._run(script, timeout=0.1)
        self.assertEqual(result.error_code, "timeout")
        time.sleep(0.1)
        self.assertFalse(marker.exists())

    def test_nonzero_and_envelope_errors_are_classified_without_success(self):
        for message, expected in (
            ("authentication required", "auth"),
            ("quota exhausted", "quota"),
            ("rate limit 429", "rate_limit"),
        ):
            result = self._run(f"import sys; sys.stderr.write({message!r}); sys.exit(1)")
            self.assertEqual(result.error_code, expected)
        malformed = self._run("import sys; sys.stdout.write('{'); sys.exit(1)")
        self.assertEqual(malformed.error_code, "malformed_response")
        empty = self._run("import sys; sys.exit(0)")
        self.assertEqual(empty.error_code, "malformed_response")

    def test_run_rejects_unsafe_inputs_and_image_budget(self):
        command = self._command("import sys; sys.stdin.buffer.read()")
        env = subscription.build_child_env(str(self.root / "home"), str(self.root / "tmp"))
        Path(env["TMPDIR"]).mkdir(exist_ok=True)
        with self.assertRaises(ValueError):
            subscription.run_codex(command, " ", env, cwd=self.root, attempt_id="a")
        with self.assertRaises(ValueError):
            subscription.run_codex(command, "x" * 60001, env, cwd=self.root, attempt_id="a")
        bad_env = dict(env, OPENAI_API_KEY="secret")
        with self.assertRaises(ValueError):
            subscription.run_codex(command, "x", bad_env, cwd=self.root, attempt_id="a")
        images = [self.root / f"image-{index}.png" for index in range(9)]
        with self.assertRaises(ValueError):
            self._command("import sys; sys.stdin.buffer.read()", images)
        large = self.root / "large.png"
        with large.open("wb") as handle:
            handle.truncate(32 * 1024 * 1024 + 1)
        with self.assertRaises(ValueError):
            self._command("import sys; sys.stdin.buffer.read()", [large])


if __name__ == "__main__":
    unittest.main()
