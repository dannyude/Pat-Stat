from unittest.mock import AsyncMock, patch

from httpx import AsyncClient
import pytest

pytestmark = pytest.mark.asyncio


class TestHealth:
    async def test_health(self, api_client: AsyncClient):
        with patch("src.main.get_redis") as mock_get_redis:
            mock_redis = AsyncMock()
            mock_redis.ping = AsyncMock()
            mock_get_redis.return_value = mock_redis

            response = await api_client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "ok"
