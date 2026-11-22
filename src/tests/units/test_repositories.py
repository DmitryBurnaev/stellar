"""Comprehensive tests for src/db/repositories.py module."""

from typing import Any

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.repositories import (
    BaseRepository,
    UserRepository,
    TokenRepository,
    FilterT,
)
from src.db.models import User, Token


def session_mock(repository: BaseRepository) -> AsyncMock:
    """Narrow the spec-based session double without treating real methods as mocks."""
    assert isinstance(repository.session, AsyncMock)
    return repository.session


class TestBaseRepository:
    """Tests for BaseRepository class."""

    @pytest.fixture
    def mock_session(self) -> AsyncMock:
        """Create mock AsyncSession."""
        return AsyncMock(spec=AsyncSession)

    @pytest.fixture
    def mock_user(self) -> MagicMock:
        """Create mock User instance."""
        user = MagicMock(spec=User)
        user.id = 1
        user.username = "testuser"
        user.email = "test@example.com"
        return user

    @pytest.fixture
    def base_repo(self, mock_session: AsyncMock) -> BaseRepository:
        """Create BaseRepository instance for testing."""

        class TestRepository(BaseRepository):
            model = User

        return TestRepository(mock_session)

    @pytest.mark.asyncio
    async def test_get_success(self, base_repo: BaseRepository, mock_user: MagicMock) -> None:
        """Test successful get operation."""
        with patch.object(base_repo, "first", AsyncMock(return_value=mock_user)) as mock_first:

            result = await base_repo.get(1)

            assert result == mock_user
            mock_first.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_get_not_found(self, base_repo: BaseRepository) -> None:
        """Test get operation when instance not found."""
        with patch.object(base_repo, "first", AsyncMock(return_value=None)) as mock_first:
            with pytest.raises(NoResultFound):
                await base_repo.get(1)
            mock_first.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_first_found(self, base_repo: BaseRepository, mock_user: MagicMock) -> None:
        """Test first operation when instance found."""
        mock_result = MagicMock()
        mock_result.fetchone.return_value = (mock_user,)
        session_mock(base_repo).execute = AsyncMock(return_value=mock_result)

        result = await base_repo.first(1)

        assert result == mock_user
        session_mock(base_repo).execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_first_not_found(self, base_repo: BaseRepository) -> None:
        """Test first operation when instance not found."""
        mock_result = MagicMock()
        mock_result.fetchone.return_value = None
        session_mock(base_repo).execute = AsyncMock(return_value=mock_result)

        result = await base_repo.first(1)

        assert result is None

    @pytest.mark.asyncio
    async def test_all_with_filters(self, base_repo: BaseRepository, mock_user: MagicMock) -> None:
        """Test all operation with filters."""
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [(mock_user,)]
        session_mock(base_repo).execute = AsyncMock(return_value=mock_result)

        # Mock _prepare_statement to avoid complex SQLAlchemy logic
        with (patch.object(base_repo, "_prepare_statement", MagicMock(return_value=MagicMock())),):
            result = await base_repo.all(username="testuser")

            assert result == [mock_user]
            session_mock(base_repo).execute.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create(self, base_repo: BaseRepository) -> None:
        """Test create operation."""
        user_data = {"username": "testuser", "email": "test@example.com"}

        with patch("src.db.repositories.logger") as mock_logger:
            result = await base_repo.create(user_data)

            assert isinstance(result, User)
            session_mock(base_repo).add.assert_called_once_with(result)
            mock_logger.debug.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_or_create_existing(
        self, base_repo: BaseRepository, mock_user: MagicMock
    ) -> None:
        """Test get_or_create when instance exists."""
        with patch.object(base_repo, "first", AsyncMock(return_value=mock_user)) as mock_first:

            result = await base_repo.get_or_create(1, {"username": "testuser"})

            assert result == mock_user
            mock_first.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_get_or_create_new(self, base_repo: BaseRepository, mock_user: MagicMock) -> None:
        """Test get_or_create when instance doesn't exist."""
        with (
            patch.object(base_repo, "first", AsyncMock(return_value=None)) as mock_first,
            patch.object(base_repo, "create", AsyncMock(return_value=mock_user)) as mock_create,
            patch.object(base_repo, "get", AsyncMock(return_value=mock_user)) as mock_get,
        ):
            result = await base_repo.get_or_create(1, {"username": "testuser"})

            assert result == mock_user
            mock_first.assert_awaited_once_with(1)
            mock_create.assert_awaited_once_with({"username": "testuser", "id": 1})
            mock_get.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_update(self, base_repo: BaseRepository, mock_user: MagicMock) -> None:
        """Test update operation."""
        update_data: dict[str, Any] = {"username": "newuser", "email": "new@example.com"}

        await base_repo.update(mock_user, **update_data)

        assert mock_user.username == "newuser"
        assert mock_user.email == "new@example.com"
        session_mock(base_repo).add.assert_called_once_with(mock_user)

    @pytest.mark.asyncio
    async def test_delete(self, base_repo: BaseRepository, mock_user: MagicMock) -> None:
        """Test delete operation."""
        await base_repo.delete(mock_user)

        session_mock(base_repo).delete.assert_awaited_once_with(mock_user)

    @pytest.mark.asyncio
    async def test_delete_by_ids(self, base_repo: BaseRepository) -> None:
        """Test delete_by_ids operation."""
        ids = [1, 2, 3]

        await base_repo.delete_by_ids(ids)

        session_mock(base_repo).execute.assert_awaited_once()

    def test_prepare_statement_with_ids_filter(self, base_repo: BaseRepository) -> None:
        """Test _prepare_statement with ids filter."""
        filters: dict[str, FilterT] = {"ids": [1, 2, 3], "username": "testuser"}

        base_repo._prepare_statement(filters)

        assert "ids" not in filters  # Should be popped from filters

    def test_prepare_statement_without_ids_filter(self, base_repo: BaseRepository) -> None:
        """Test _prepare_statement without ids filter."""
        filters: dict[str, FilterT] = {"username": "testuser"}

        base_repo._prepare_statement(filters)

        assert "username" in filters


class TestUserRepository:
    """Tests for UserRepository class."""

    @pytest.fixture
    def mock_session(self) -> AsyncMock:
        """Create mock AsyncSession."""
        return AsyncMock(spec=AsyncSession)

    @pytest.fixture
    def user_repo(self, mock_session: AsyncMock) -> UserRepository:
        """Create UserRepository instance."""
        return UserRepository(mock_session)

    @pytest.fixture
    def mock_user(self) -> MagicMock:
        """Create mock User instance."""
        user = MagicMock(spec=User)
        user.id = 1
        user.username = "testuser"
        user.email = "test@example.com"
        return user

    @pytest.mark.asyncio
    async def test_get_by_username_found(
        self, user_repo: UserRepository, mock_user: MagicMock
    ) -> None:
        """Test get_by_username when user found."""
        with patch.object(user_repo, "all", AsyncMock(return_value=[mock_user])) as mock_all:

            with patch("src.db.repositories.logger") as mock_logger:
                result = await user_repo.get_by_username("testuser")

                assert result == mock_user
                mock_all.assert_awaited_once_with(username="testuser")
                mock_logger.debug.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_by_username_not_found(self, user_repo: UserRepository) -> None:
        """Test get_by_username when user not found."""
        with patch.object(user_repo, "all", AsyncMock(return_value=[])) as mock_all:

            with patch("src.db.repositories.logger") as mock_logger:
                result = await user_repo.get_by_username("nonexistent")

                assert result is None
                mock_all.assert_awaited_once_with(username="nonexistent")
                mock_logger.debug.assert_called_once()


class TestTokenRepository:
    """Tests for TokenRepository class."""

    @pytest.fixture
    def mock_session(self) -> AsyncMock:
        """Create mock AsyncSession."""
        return AsyncMock(spec=AsyncSession)

    @pytest.fixture
    def token_repo(self, mock_session: AsyncMock) -> TokenRepository:
        """Create TokenRepository instance."""
        return TokenRepository(mock_session)

    @pytest.fixture
    def mock_token(self) -> MagicMock:
        """Create mock Token instance."""
        token = MagicMock(spec=Token)
        token.id = 1
        token.token = "hashed_token_value"
        token.is_active = True
        return token

    @pytest.mark.asyncio
    async def test_get_by_token_found(
        self, token_repo: TokenRepository, mock_token: MagicMock
    ) -> None:
        """Test get_by_token when token found."""
        with patch.object(token_repo, "all", AsyncMock(return_value=[mock_token])) as mock_all:

            with patch("src.db.repositories.logger") as mock_logger:
                result = await token_repo.get_by_token("hashed_token_value")

                assert result == mock_token
                mock_all.assert_awaited_once_with(token="hashed_token_value")
                mock_logger.debug.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_by_token_not_found(self, token_repo: TokenRepository) -> None:
        """Test get_by_token when token not found."""
        with patch.object(token_repo, "all", AsyncMock(return_value=[])) as mock_all:

            with patch("src.db.repositories.logger") as mock_logger:
                result = await token_repo.get_by_token("nonexistent")

                assert result is None
                mock_all.assert_awaited_once_with(token="nonexistent")
                mock_logger.debug.assert_not_called()

    @pytest.mark.asyncio
    async def test_set_active_true(self, token_repo: TokenRepository) -> None:
        """Test set_active with is_active=True."""
        token_ids = [1, 2, 3]
        mock_result = MagicMock()
        mock_result.rowcount = 3
        session_mock(token_repo).execute = AsyncMock(return_value=mock_result)
        session_mock(token_repo).flush = AsyncMock()

        with patch("src.db.repositories.logger") as mock_logger:
            await token_repo.set_active(token_ids, True)

            session_mock(token_repo).execute.assert_awaited_once()
            session_mock(token_repo).flush.assert_awaited_once()

            mock_logger.info.assert_any_call("[DB] %s %i tokens: %r", "Activating", 3, [1, 2, 3])
            mock_logger.info.assert_any_call("[DB] Updating %i instances: %r", 3, [1, 2, 3])
            mock_logger.info.assert_any_call("[DB] Updated %i instances", 3)

    @pytest.mark.asyncio
    async def test_set_active_false(self, token_repo: TokenRepository) -> None:
        """Test set_active with is_active=False."""
        token_ids = [1, 2, 3]
        mock_result = MagicMock()
        mock_result.rowcount = 2
        session_mock(token_repo).execute = AsyncMock(return_value=mock_result)
        session_mock(token_repo).flush = AsyncMock()

        with patch("src.db.repositories.logger") as mock_logger:
            await token_repo.set_active(token_ids, False)

            session_mock(token_repo).execute.assert_awaited_once()
            session_mock(token_repo).flush.assert_awaited_once()

            # Check log calls
            mock_logger.info.assert_any_call("[DB] %s %i tokens: %r", "Deactivating", 3, [1, 2, 3])
            mock_logger.info.assert_any_call("[DB] Updating %i instances: %r", 3, [1, 2, 3])
            mock_logger.info.assert_any_call("[DB] Updated %i instances", 2)

    @pytest.mark.asyncio
    async def test_set_active_with_parsed_string_ids(self, token_repo: TokenRepository) -> None:
        """Parse admin-style string IDs before calling the typed repository."""
        token_ids = [int(value) for value in ("1", "2", "3")]
        mock_result = MagicMock()
        mock_result.rowcount = 3
        session_mock(token_repo).execute = AsyncMock(return_value=mock_result)
        session_mock(token_repo).flush = AsyncMock()

        with patch("src.db.repositories.logger"):
            await token_repo.set_active(token_ids, True)

            session_mock(token_repo).execute.assert_awaited_once()
            session_mock(token_repo).flush.assert_awaited_once()
