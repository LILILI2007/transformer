import io
import json
import threading
import unittest
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from modlingo.core import ProjectError
from modlingo.provider import APIConfig, Translator, batches


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.config = APIConfig("https://example.test/v1", "test-model", "private-test-key")

    def test_endpoint_validation(self):
        self.assertEqual(self.config.endpoint(), "https://example.test/v1/chat/completions")
        for url in ["http://example.test/v1", "https://user:secret@example.test", "https://example.test?key=x", "file:///tmp/key"]:
            with self.assertRaises(ProjectError):
                APIConfig(url, "model", "key").endpoint()
        self.assertEqual(APIConfig("http://127.0.0.1:1000/v1", "local").endpoint(), "http://127.0.0.1:1000/v1/chat/completions")

    def test_mask_restore_and_relevant_glossary(self):
        translator = Translator(self.config)

        def fake(messages):
            payload = json.loads(messages[1]["content"])
            self.assertEqual(payload["glossary"], {"Starfruit": "杨桃"})
            masked = payload["items"]["0"]["text"]
            self.assertNotIn("{{PlayerName}}", masked)
            return json.dumps({"0": masked.replace("Starfruit", "杨桃").replace("Hello", "你好")})

        with patch.object(translator, "_request", side_effect=fake):
            result = translator.translate([("greeting", "Hello {{PlayerName}}! Starfruit.$h")], {"Starfruit": "杨桃", "Pumpkin": "南瓜"})
        self.assertEqual(result[0][2], "你好 {{PlayerName}}! 杨桃.$h")

    def test_missing_ids_and_damaged_markers_are_rejected(self):
        translator = Translator(self.config)
        for response in ['{}', '{"0":"你好"}', '{"0":4}', '{"0":"a","0":"b"}']:
            with patch.object(translator, "_request", return_value=response):
                with self.assertRaises(ProjectError):
                    translator.translate([("greeting", "Hello {{name}}$h")], {})

    def test_item_and_character_batch_limits(self):
        self.assertEqual([len(batch) for batch in batches([(str(i), "x") for i in range(19)])], [8, 8, 3])
        self.assertEqual(len(list(batches([("a", "x" * 10000), ("b", "x" * 10000)]))), 2)

    def test_invalid_port_and_header_injection_are_rejected(self):
        for url in ["https://example.test:invalid", "https://example.test:70000", "https://[bad", "https://example.test/\npath"]:
            with self.assertRaises(ProjectError):
                APIConfig(url, "model", "key").endpoint()
        with self.assertRaises(ProjectError):
            APIConfig("https://example.test", "model", "key\r\nInjected: yes").endpoint()

    def test_truncated_http_response_has_bounded_sanitized_failure(self):
        opener = unittest.mock.MagicMock()
        opener.open.return_value.__enter__.return_value.read.side_effect = IncompleteRead(b"private-test-key", 100)
        with patch("modlingo.provider.build_opener", return_value=opener), patch("modlingo.provider.time.sleep"):
            with self.assertRaises(ProjectError) as caught:
                Translator(self.config)._request([])
        self.assertEqual(opener.open.call_count, 3)
        self.assertNotIn("private-test-key", str(caught.exception))

    def test_authentication_error_is_sanitized_and_not_retried(self):
        error = HTTPError(self.config.endpoint(), 401, "private-test-key", {}, io.BytesIO(b"sensitive response"))
        opener = unittest.mock.Mock()
        opener.open.side_effect = error
        with patch("modlingo.provider.build_opener", return_value=opener):
            with self.assertRaises(ProjectError) as caught:
                Translator(self.config)._request([])
        self.assertNotIn("private-test-key", str(caught.exception))
        self.assertNotIn("sensitive", str(caught.exception))
        self.assertEqual(opener.open.call_count, 1)

    def test_network_failure_retries_are_bounded(self):
        opener = unittest.mock.Mock()
        opener.open.side_effect = URLError("private-test-key")
        with patch("modlingo.provider.build_opener", return_value=opener), patch("modlingo.provider.time.sleep"):
            with self.assertRaises(ProjectError) as caught:
                Translator(self.config)._request([])
        self.assertEqual(opener.open.call_count, 3)
        self.assertNotIn("private-test-key", str(caught.exception))

    def test_real_local_http_transport_without_external_network(self):
        captured = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                captured.append((self.path, self.headers.get("Authorization"), payload))
                inputs = json.loads(payload["messages"][1]["content"])["items"]
                translated = {key: value["text"].replace("Hello", "你好") for key, value in inputs.items()}
                body = json.dumps({"choices": [{"message": {"content": json.dumps(translated)}}]}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            config = APIConfig(f"http://127.0.0.1:{server.server_port}/v1", "local-test", "local-secret")
            output = Translator(config).translate([("hello", "Hello {{name}}$h")], {})
            self.assertEqual(output[0][2], "你好 {{name}}$h")
            self.assertEqual(captured[0][0], "/v1/chat/completions")
            self.assertEqual(captured[0][1], "Bearer local-secret")
            self.assertEqual(captured[0][2]["model"], "local-test")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
