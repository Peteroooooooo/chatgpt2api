from __future__ import annotations

import base64
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from services.config import ConfigStore
from services.image_storage_service import ImageStorageService, StoredImage
from services.protocol.conversation import format_image_result


def png_bytes(color: tuple[int, int, int] = (255, 0, 0)) -> bytes:
    path = Path(tempfile.gettempdir()) / f"chatgpt2api-retention-{color[0]}.png"
    Image.new("RGB", (2, 2), color=color).save(path, format="PNG")
    return path.read_bytes()


class FormatImageResultStorageTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch("services.protocol.conversation.image_storage_service")
        self.storage = patcher.start()
        self.addCleanup(patcher.stop)
        self.storage.save.return_value = StoredImage(rel="r.png", url="http://app.test/images/r.png", storage="local", size=1)
        self.items = [{"b64_json": base64.b64encode(png_bytes()).decode("ascii")}]

    def test_b64_json_result_writes_no_file_and_has_no_url(self):
        result = format_image_result(self.items, "cat", "b64_json", "http://app.test")

        self.storage.save.assert_not_called()
        self.assertEqual(len(result["data"]), 1)
        self.assertEqual(result["data"][0]["b64_json"], self.items[0]["b64_json"])
        self.assertNotIn("url", result["data"][0])

    def test_url_result_saves_file_and_returns_url_only(self):
        result = format_image_result(self.items, "cat", "url", "http://app.test")

        self.storage.save.assert_called_once()
        self.assertEqual(result["data"][0]["url"], "http://app.test/images/r.png")
        self.assertNotIn("b64_json", result["data"][0])


class RetentionConfigTests(unittest.TestCase):
    def make_store(self, data: dict[str, object]) -> ConfigStore:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "config.json"
        path.write_text(json.dumps({"auth-key": "test-auth", **data}), encoding="utf-8")
        return ConfigStore(path)

    def test_default_is_one_hour(self):
        self.assertEqual(self.make_store({}).image_retention_hours, 1)

    def test_old_days_key_is_ignored(self):
        store = self.make_store({"image_retention_days": 30})

        self.assertEqual(store.image_retention_hours, 1)
        public = store.get()
        self.assertEqual(public["image_retention_hours"], 1)
        self.assertNotIn("image_retention_days", public)

    def test_saving_settings_drops_old_days_key(self):
        store = self.make_store({"image_retention_days": 30})

        store.update({"image_retention_hours": 6})

        saved = json.loads(store.path.read_text(encoding="utf-8"))
        self.assertEqual(saved["image_retention_hours"], 6)
        self.assertNotIn("image_retention_days", saved)
        self.assertEqual(store.image_retention_hours, 6)

    def test_minimum_is_one_hour(self):
        self.assertEqual(self.make_store({"image_retention_hours": 0}).image_retention_hours, 1)
        self.assertEqual(self.make_store({"image_retention_hours": "bad"}).image_retention_hours, 1)


class ImageCleanupTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)
        config_path = self.data_dir / "config.json"
        config_path.write_text(json.dumps({"auth-key": "test-auth", "image_retention_hours": 2}), encoding="utf-8")
        patches = [mock.patch("services.config.DATA_DIR", self.data_dir)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.config = ConfigStore(config_path)
        self.storage = ImageStorageService(self.data_dir / "image_index.json")
        for target, value in (
            ("services.image_service.config", self.config),
            ("services.image_storage_service.config", self.config),
            ("services.image_service.image_storage_service", self.storage),
            ("services.image_tags_service.TAGS_FILE", self.data_dir / "image_tags.json"),
        ):
            patch = mock.patch(target, value)
            patch.start()
            self.addCleanup(patch.stop)

    def age(self, rel: str, hours: float) -> None:
        old = time.time() - hours * 3600
        os.utime(self.config.images_dir / rel, (old, old))

    def test_cleanup_deletes_expired_images_and_their_metadata(self):
        from services import image_service
        from services.image_tags_service import load_tags, set_tags

        old_rel = self.storage.save(png_bytes((255, 0, 0)), "http://app.test").rel
        time.sleep(1.1)  # file names carry the second, keep the two apart
        new_rel = self.storage.save(png_bytes((0, 255, 0)), "http://app.test").rel
        self.age(old_rel, 3)
        self.age(new_rel, 0.5)
        image_service.ensure_thumbnail(old_rel)
        image_service.ensure_thumbnail(new_rel)
        set_tags(old_rel, ["cat"])
        set_tags(new_rel, ["dog"])

        result = image_service.run_image_cleanup()

        self.assertEqual(result["expired"], 1)
        self.assertFalse((self.config.images_dir / old_rel).exists())
        self.assertTrue((self.config.images_dir / new_rel).is_file())
        self.assertFalse((self.config.image_thumbnails_dir / f"{old_rel}.png").exists())
        self.assertTrue((self.config.image_thumbnails_dir / f"{new_rel}.png").is_file())
        index = json.loads((self.data_dir / "image_index.json").read_text(encoding="utf-8"))["items"]
        self.assertEqual(set(index), {new_rel})
        self.assertEqual(load_tags(), {new_rel: ["dog"]})
        listed = [item["rel"] for item in self.storage.list_items("http://app.test")]
        self.assertEqual(listed, [new_rel])

    def test_cleanup_worker_runs_every_five_minutes(self):
        from services import image_service

        self.assertLessEqual(image_service.IMAGE_CLEANUP_INTERVAL_SECS, 300)


class ImageTaskRetentionTests(unittest.TestCase):
    def test_finished_tasks_expire_after_retention_hours(self):
        from datetime import datetime, timedelta

        from services.image_task_service import ImageTaskService

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "image_tasks.json"
            fmt = "%Y-%m-%d %H:%M:%S"
            old = (datetime.now() - timedelta(hours=2)).strftime(fmt)
            recent = (datetime.now() - timedelta(minutes=10)).strftime(fmt)
            path.write_text(json.dumps({"tasks": [
                {"id": "old", "owner_id": "u", "status": "success", "created_at": old, "updated_at": old},
                {"id": "recent", "owner_id": "u", "status": "success", "created_at": recent, "updated_at": recent},
            ]}), encoding="utf-8")

            service = ImageTaskService(path, retention_hours_getter=lambda: 1)

            result = service.list_tasks({"id": "u"}, ["old", "recent"])
            self.assertEqual([item["id"] for item in result["items"]], ["recent"])
            self.assertEqual(result["missing_ids"], ["old"])


if __name__ == "__main__":
    unittest.main()
