"""Tests for the vacancy_search slice (VSA pilot)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from job_bot.shared.config.settings import Settings
from job_bot.shared.storage.database import Database, create_database
from job_bot.vacancy_search.models.search_profile import (
    SearchProfileCreate,
)
from job_bot.vacancy_search.slice import (
    VacancySearchSlice,
    create_vacancy_search_slice,
)


class TestVacancySearchSlice:
    """Test the vacancy_search slice."""

    @pytest.fixture
    def temp_db_path(self) -> Path:
        """Create a temporary database path."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            return Path(f.name)

    @pytest.fixture
    def database(self, temp_db_path: Path) -> Database:
        """Create a database instance."""
        return create_database(temp_db_path)

    @pytest.fixture
    def settings(self, temp_db_path: Path) -> Settings:
        """Create test settings."""
        return Settings()
        # The database path will be overridden by the temp_db_path

    @pytest.fixture
    def slice_instance(self, database: Database) -> VacancySearchSlice:
        """Create a vacancy search slice instance."""
        return VacancySearchSlice(database=database)

    def test_create_slice(self, database: Database) -> None:
        """Test creating a slice instance."""
        slice_instance = VacancySearchSlice(database=database)
        assert slice_instance is not None
        assert slice_instance.database is database
        assert slice_instance.search_profiles is not None
        assert slice_instance.vacancies is not None
        assert slice_instance.search is not None

    def test_create_slice_factory(self, temp_db_path: Path) -> None:
        """Test creating a slice using the factory function."""
        settings = Settings()
        settings.database.path = temp_db_path

        slice_instance = create_vacancy_search_slice(settings=settings)
        assert slice_instance is not None
        assert slice_instance.database.path == temp_db_path

    def test_search_profile_crud(
        self, slice_instance: VacancySearchSlice
    ) -> None:
        """Test search profile CRUD operations."""
        # Create
        create_data = SearchProfileCreate(
            name="Test Profile",
            keywords="python developer",
            schedule=["remote"],
            experience=["between3And6"],
            employment=["full"],
            area=["1"],  # Moscow
            salary=200000,
        )
        profile = slice_instance.search_profiles.create_profile(create_data)
        assert profile.id is not None
        assert profile.name == "Test Profile"
        assert profile.keywords == "python developer"
        assert profile.schedule == ["remote"]

        # Get by ID
        retrieved = slice_instance.search_profiles.get_profile(profile.id)
        assert retrieved is not None
        assert retrieved.id == profile.id
        assert retrieved.name == "Test Profile"

        # Get by name
        retrieved_by_name = slice_instance.search_profiles.get_profile_by_name(
            "Test Profile"
        )
        assert retrieved_by_name is not None
        assert retrieved_by_name.id == profile.id

        # List
        profiles = slice_instance.search_profiles.list_profiles()
        assert len(profiles) >= 1
        assert any(p.id == profile.id for p in profiles)

        # Update
        from job_bot.vacancy_search.models.search_profile import (
            SearchProfileUpdate,
        )

        update = SearchProfileUpdate(keywords="senior python developer")
        updated = slice_instance.search_profiles.update_profile(
            profile.id, update
        )
        assert updated is not None
        assert updated.keywords == "senior python developer"

        # Delete
        deleted = slice_instance.search_profiles.delete_profile(profile.id)
        assert deleted is True

        # Verify deleted
        retrieved_after_delete = slice_instance.search_profiles.get_profile(
            profile.id
        )
        assert retrieved_after_delete is None

    def test_vacancy_crud(self, slice_instance: VacancySearchSlice) -> None:
        """Test vacancy CRUD operations."""
        from job_bot.vacancy_search.models.vacancy import VacancyCreate

        # Create
        create_data = VacancyCreate(
            hh_id="12345",
            name="Python Developer",
            employer_name="Test Company",
            employer_id="67890",
            area_name="Moscow",
            salary_from=150000,
            salary_to=250000,
            currency="RUR",
            experience="between3And6",
            employment="full",
            schedule="remote",
            description="Test description",
            key_skills=["Python", "Django", "PostgreSQL"],
        )
        vacancy = slice_instance.vacancies.create_vacancy(create_data)
        assert vacancy.id is not None
        assert vacancy.hh_id == "12345"
        assert vacancy.name == "Python Developer"

        # Get by ID
        retrieved = slice_instance.vacancies.get_vacancy(vacancy.id)
        assert retrieved is not None
        assert retrieved.id == vacancy.id

        # Get by HH ID
        retrieved_by_hh = slice_instance.vacancies.get_vacancy_by_hh_id("12345")
        assert retrieved_by_hh is not None
        assert retrieved_by_hh.id == vacancy.id

        # List
        vacancies = slice_instance.vacancies.list_vacancies()
        assert len(vacancies) >= 1
        assert any(v.id == vacancy.id for v in vacancies)

        # Search
        search_results = slice_instance.vacancies.search_vacancies(
            keywords="Python"
        )
        assert len(search_results) >= 1

        # Exists
        exists = slice_instance.vacancies.vacancy_exists("12345")
        assert exists is True

        # Count
        count = slice_instance.vacancies.count_vacancies()
        assert count >= 1

        # Delete
        deleted = slice_instance.vacancies.delete_vacancy(vacancy.id)
        assert deleted is True

    def test_search_profile_to_api_params(
        self, slice_instance: VacancySearchSlice
    ) -> None:
        """Test converting search profile to API parameters."""
        create_data = SearchProfileCreate(
            name="API Test",
            keywords="python",
            schedule=["remote", "flexible"],
            experience=["between1And3", "between3And6"],
            employment=["full", "part"],
            area=["1", "2"],
            salary=150000,
            currency="RUR",
            only_with_salary=True,
            search_period=30,
            per_page=50,
        )
        profile = slice_instance.search_profiles.create_profile(create_data)
        params = profile.to_api_params()

        assert params["text"] == "python"
        assert params["schedule"] == ["remote", "flexible"]
        assert params["experience"] == ["between1And3", "between3And6"]
        assert params["employment"] == ["full", "part"]
        assert params["area"] == ["1", "2"]
        assert params["salary"] == 150000
        assert params["currency"] == "RUR"
        assert params["only_with_salary"] is True
        assert params["search_period"] == 30
        assert params["per_page"] == 50


class TestVacancySearchSliceIntegration:
    """Integration tests for the vacancy_search slice."""

    def test_full_workflow(self) -> None:
        """Test a full workflow: create profile, search, store vacancies."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = Path(f.name)

        try:
            settings = Settings()
            settings.database.path = db_path

            slice_instance = create_vacancy_search_slice(settings=settings)

            # Create a search profile
            profile_data = SearchProfileCreate(
                name="Integration Test",
                keywords="python",
                per_page=10,
            )
            profile = slice_instance.search_profiles.create_profile(
                profile_data
            )

            # Verify profile was created
            assert profile is not None
            assert profile.name == "Integration Test"

            # Manually create a vacancy (simulating API response)
            from job_bot.vacancy_search.models.vacancy import VacancyCreate

            vacancy_data = VacancyCreate(
                hh_id="99999",
                name="Senior Python Developer",
                employer_name="Integration Corp",
                area_name="Moscow",
                salary_from=200000,
                salary_to=300000,
            )
            vacancy = slice_instance.vacancies.create_vacancy(vacancy_data)

            # Verify vacancy was stored
            assert vacancy is not None
            assert vacancy.hh_id == "99999"

            # Search for it
            results = slice_instance.vacancies.search_vacancies(
                keywords="Python"
            )
            assert len(results) >= 1
            assert any(v.hh_id == "99999" for v in results)

        finally:
            # Cleanup
            if db_path.exists():
                db_path.unlink()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
