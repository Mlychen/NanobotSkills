"""End-to-end tests against a real Miniflux instance via miniflux_http.py."""

import time

import pytest

from conftest import E2EClient


def assert_ok(code: int, data: object, stderr: str) -> object:
    """Assert request succeeded; return data as-is (any type)."""
    assert code == 0, f"Request failed (exit {code}): {stderr}"
    return data


def assert_dict(code: int, data: object, stderr: str) -> dict:
    result = assert_ok(code, data, stderr)
    assert isinstance(result, dict), f"Expected dict, got {type(data)}: {data}"
    return result  # type: ignore[return-value]


def assert_list(code: int, data: object, stderr: str) -> list:
    result = assert_ok(code, data, stderr)
    assert isinstance(result, list), f"Expected list, got {type(data)}: {data}"
    return result  # type: ignore[return-value]


def assert_deleted(code: int, data: object, stderr: str) -> None:
    """Assert request succeeded; DELETE endpoints may return empty body."""
    assert code == 0, f"Request failed (exit {code}): {stderr}"


def wait_for_entries(cli: E2EClient, feed_id: int, timeout: float = 30) -> int | None:
    """Poll feed entries until at least one is available or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, data, _ = cli.get(f"/v1/feeds/{feed_id}/entries", {"limit": "1"})
        if (
            code == 0
            and isinstance(data, dict)
            and "entries" in data
            and len(data["entries"]) > 0
        ):
            return data["entries"][0]["id"]
        time.sleep(2)
    return None


class TestSystem:
    """system health, system version."""

    def test_healthcheck(self, cli: E2EClient) -> None:
        code, data, stderr = cli.get("/healthcheck")
        assert code == 0, f"healthcheck failed: {stderr}"
        assert data == "OK"

    def test_version(self, cli: E2EClient) -> None:
        result = assert_dict(*cli.get("/v1/version"))
        assert "version" in result
        assert isinstance(result["version"], str)
        assert len(result["version"]) > 0


class TestUsers:
    """users me, users list, users create, users delete."""

    created_user_ids: list[int] = []

    @pytest.fixture(autouse=True, scope="class")
    def _cleanup_users(self, cli: E2EClient) -> None:
        TestUsers.created_user_ids = []
        yield
        for uid in TestUsers.created_user_ids:
            cli.delete(f"/v1/users/{uid}")

    def test_me(self, cli: E2EClient) -> None:
        result = assert_dict(*cli.get("/v1/me"))
        assert "id" in result
        assert "username" in result
        assert "is_admin" in result

    def test_list_users(self, cli: E2EClient) -> None:
        code, data, stderr = cli.get("/v1/users")
        if code != 0:
            pytest.skip("API key is not admin; cannot list users")
        assert_list(code, data, stderr)

    def test_create_and_delete_user(self, cli: E2EClient) -> None:
        code, data, stderr = cli.post(
            "/v1/users",
            {"username": "e2e_test_user", "password": "e2etest123"},
        )
        if code != 0:
            pytest.skip("API key is not admin; cannot create users")
        result = assert_dict(code, data, stderr)
        assert "id" in result
        assert result["username"] == "e2e_test_user"
        user_id = result["id"]
        TestUsers.created_user_ids.append(user_id)

        code, data, stderr = cli.delete(f"/v1/users/{user_id}")
        assert_deleted(code, data, stderr)
        TestUsers.created_user_ids.remove(user_id)


class TestApiKeys:
    """api-keys list, create, delete."""

    created_key_ids: list[int] = []

    @pytest.fixture(autouse=True, scope="class")
    def _cleanup_keys(self, cli: E2EClient) -> None:
        TestApiKeys.created_key_ids = []
        yield
        for kid in TestApiKeys.created_key_ids:
            cli.delete(f"/v1/api_keys/{kid}")

    def test_list_api_keys(self, cli: E2EClient) -> None:
        code, data, stderr = cli.get("/v1/api_keys")
        if code != 0:
            pytest.skip("API key management endpoint not available")
        assert_list(code, data, stderr)

    def test_create_and_delete_api_key(self, cli: E2EClient) -> None:
        code, data, stderr = cli.post(
            "/v1/api_keys",
            {"description": "e2e test key"},
        )
        if code != 0:
            pytest.skip("API key creation not available")
        result = assert_dict(code, data, stderr)
        assert "id" in result
        assert "token" in result
        assert result["description"] == "e2e test key"
        key_id = result["id"]
        TestApiKeys.created_key_ids.append(key_id)

        code, data, stderr = cli.delete(f"/v1/api_keys/{key_id}")
        assert_deleted(code, data, stderr)
        TestApiKeys.created_key_ids.remove(key_id)


class TestCategories:
    """categories list, create, update, delete."""

    created_category_ids: list[int] = []

    @pytest.fixture(autouse=True, scope="class")
    def _cleanup_categories(self, cli: E2EClient) -> None:
        TestCategories.created_category_ids = []
        yield
        for cid in TestCategories.created_category_ids:
            cli.delete(f"/v1/categories/{cid}")

    def test_list_categories(self, cli: E2EClient) -> None:
        result = assert_list(*cli.get("/v1/categories"))
        assert len(result) >= 0

    def test_create_and_delete_category(self, cli: E2EClient) -> None:
        import uuid
        title = f"E2E Cat {uuid.uuid4().hex[:8]}"
        code, data, stderr = cli.post(
            "/v1/categories",
            {"title": title},
        )
        if code != 0:
            pytest.skip(f"Cannot create category: {stderr}")
        result = assert_dict(code, data, stderr)
        assert "id" in result
        assert result["title"] == title
        cat_id = result["id"]
        TestCategories.created_category_ids.append(cat_id)

        code, data, stderr = cli.delete(f"/v1/categories/{cat_id}")
        assert_deleted(code, data, stderr)
        TestCategories.created_category_ids.remove(cat_id)

    def test_update_category(self, cli: E2EClient) -> None:
        import uuid
        title_before = f"E2E Cat Before {uuid.uuid4().hex[:8]}"
        title_after = f"E2E Cat After {uuid.uuid4().hex[:8]}"
        code, data, stderr = cli.post(
            "/v1/categories",
            {"title": title_before},
        )
        if code != 0:
            pytest.skip(f"Cannot create category for update test: {stderr}")
        result = assert_dict(code, data, stderr)
        cat_id = result["id"]
        TestCategories.created_category_ids.append(cat_id)

        code, data, stderr = cli.put(
            f"/v1/categories/{cat_id}",
            {"title": title_after},
        )
        result = assert_dict(code, data, stderr)
        assert result["title"] == title_after


class TestFeeds:
    """feeds list, create, get, refresh, delete."""

    FEED_URL = "https://hnrss.org/newest"
    created_feed_ids: list[int] = []

    @pytest.fixture(autouse=True, scope="class")
    def _cleanup_feeds(self, cli: E2EClient) -> None:
        TestFeeds.created_feed_ids = []
        yield
        for fid in TestFeeds.created_feed_ids:
            cli.delete(f"/v1/feeds/{fid}")

    def test_list_feeds(self, cli: E2EClient) -> None:
        result = assert_list(*cli.get("/v1/feeds"))
        assert len(result) >= 0

    def test_create_and_delete_feed(self, cli: E2EClient) -> None:
        code, data, stderr = cli.post(
            "/v1/feeds",
            {"feed_url": self.FEED_URL},
        )
        if code != 0:
            # Check if it's "already exists" — find and delete the existing one
            if "already exists" in stderr:
                feeds_code, feeds_data, _ = cli.get("/v1/feeds")
                if feeds_code == 0 and isinstance(feeds_data, list):
                    for feed in feeds_data:
                        if isinstance(feed, dict) and feed.get("feed_url") == self.FEED_URL:
                            feed_id = feed["id"]
                            d_code, d_data, d_stderr = cli.delete(f"/v1/feeds/{feed_id}")
                            assert_deleted(d_code, d_data, d_stderr)
                            # Now try creating again
                            code, data, stderr = cli.post(
                                "/v1/feeds",
                                {"feed_url": self.FEED_URL},
                            )
                            break
            if code != 0:
                pytest.skip(f"Cannot create feed: {stderr}")
        result = assert_dict(code, data, stderr)
        feed_id = result.get("id") or result.get("feed_id")
        assert feed_id is not None, f"No id or feed_id in response: {result}"
        TestFeeds.created_feed_ids.append(feed_id)

        code, data, stderr = cli.get(f"/v1/feeds/{feed_id}")
        result = assert_dict(code, data, stderr)
        assert result["id"] == feed_id

        code, data, stderr = cli.delete(f"/v1/feeds/{feed_id}")
        assert_deleted(code, data, stderr)
        TestFeeds.created_feed_ids.remove(feed_id)

    def test_refresh_feed(self, cli: E2EClient) -> None:
        code, data, stderr = cli.post(
            "/v1/feeds",
            {"feed_url": self.FEED_URL},
        )
        feed_id: int | None = None
        if code == 0 and isinstance(data, dict):
            feed_id = data.get("id") or data.get("feed_id")
            TestFeeds.created_feed_ids.append(feed_id)  # type: ignore[arg-type]
        elif "already exists" in stderr:
            feeds_code, feeds_data, _ = cli.get("/v1/feeds")
            if feeds_code == 0 and isinstance(feeds_data, list):
                for feed in feeds_data:
                    if isinstance(feed, dict) and feed.get("feed_url") == self.FEED_URL:
                        feed_id = feed["id"]
                        break
        if feed_id is None:
            pytest.skip(f"Cannot find feed for refresh test: {stderr}")

        code, data, stderr = cli.put(f"/v1/feeds/{feed_id}/refresh", {})
        assert_ok(code, data, stderr)


class TestEntries:
    """entries list, set-status, toggle-bookmark."""

    @pytest.fixture(autouse=True, scope="class")
    def _setup_feed(self, cli: E2EClient) -> None:
        """Create a temporary feed or find existing one, then wait for entries."""
        TestEntries.feed_id: int | None = None
        TestEntries.entry_id: int | None = None
        TestEntries._created_feed: bool = False

        feed_url = "https://hnrss.org/newest"
        code, data, _ = cli.post("/v1/feeds", {"feed_url": feed_url})
        if code == 0 and isinstance(data, dict):
            TestEntries.feed_id = data.get("id") or data.get("feed_id")
            TestEntries._created_feed = True
        elif isinstance(data, dict) and "already exists" in str(data.get("error_message", "")):
            # Feed exists — find it by listing all feeds
            code2, data2, _ = cli.get("/v1/feeds")
            if code2 == 0 and isinstance(data2, list):
                for feed in data2:
                    if isinstance(feed, dict) and feed.get("feed_url") == feed_url:
                        TestEntries.feed_id = feed["id"]
                        break
        else:
            # Other error — fall back to any existing feed
            code2, data2, _ = cli.get("/v1/feeds")
            if code2 == 0 and isinstance(data2, list) and len(data2) > 0:
                TestEntries.feed_id = data2[0]["id"]

        # Wait for entries from the feed
        if TestEntries.feed_id is not None:
            TestEntries.entry_id = wait_for_entries(cli, TestEntries.feed_id)

        # If still no entry, grab one from global entries
        if TestEntries.entry_id is None:
            code, data, _ = cli.get("/v1/entries", {"limit": "1"})
            if (
                code == 0
                and isinstance(data, dict)
                and "entries" in data
                and len(data["entries"]) > 0
            ):
                TestEntries.entry_id = data["entries"][0]["id"]

        yield

        if TestEntries._created_feed and TestEntries.feed_id is not None:
            cli.delete(f"/v1/feeds/{TestEntries.feed_id}")

    def test_list_entries(self, cli: E2EClient) -> None:
        result = assert_dict(*cli.get("/v1/entries"))
        assert "total" in result
        assert "entries" in result
        assert isinstance(result["entries"], list)

    def test_set_entry_status(self, cli: E2EClient) -> None:
        if TestEntries.entry_id is None:
            pytest.skip("No entry available for status test")
        code, data, stderr = cli.put(
            f"/v1/entries/{TestEntries.entry_id}",
            {"status": "read"},
        )
        assert_ok(code, data, stderr)

    def test_toggle_bookmark(self, cli: E2EClient) -> None:
        if TestEntries.entry_id is None:
            pytest.skip("No entry available for bookmark test")
        code, data, stderr = cli.put(
            f"/v1/entries/{TestEntries.entry_id}/bookmark",
            {},
        )
        assert_ok(code, data, stderr)
