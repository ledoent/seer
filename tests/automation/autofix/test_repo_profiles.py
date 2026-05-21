from seer.automation.autofix.repo_profiles import (
    PROFILES,
    get_first_matching_profile,
    get_profile_for_full_name,
    get_repo_profile,
)
from seer.automation.models import RepoDefinition


def _repo(full_name: str) -> RepoDefinition:
    owner, name = full_name.split("/", 1)
    return RepoDefinition(provider="github", owner=owner, name=name, external_id=full_name)


class TestGetProfileForFullName:
    def test_returns_none_for_none(self):
        assert get_profile_for_full_name(None) is None

    def test_returns_none_for_empty(self):
        assert get_profile_for_full_name("") is None

    def test_returns_none_for_unknown(self):
        assert get_profile_for_full_name("getsentry/sentry") is None

    def test_matches_ledoent_openupgrade(self):
        profile = get_profile_for_full_name("ledoent/OpenUpgrade")
        assert profile is not None
        assert profile.branch_prefix == "19.0-fix-"
        assert "OU-FIX" in profile.title_format_hint

    def test_matches_oca_openupgrade(self):
        profile = get_profile_for_full_name("OCA/OpenUpgrade")
        assert profile is not None
        assert profile is PROFILES[0]

    def test_matches_openupgrade_lab_substring(self):
        # Catches forks like `someone/openupgrade-lab-19.0` without needing to
        # enumerate every fork name in the registry.
        profile = get_profile_for_full_name("dkendall/my-openupgrade-lab-fork")
        assert profile is not None


class TestGetRepoProfile:
    def test_none_repo(self):
        assert get_repo_profile(None) is None

    def test_matches_via_repodefinition(self):
        profile = get_repo_profile(_repo("ledoent/OpenUpgrade"))
        assert profile is not None
        assert profile.branch_prefix == "19.0-fix-"

    def test_no_match_via_repodefinition(self):
        assert get_repo_profile(_repo("foo/bar")) is None


class TestGetFirstMatchingProfile:
    def test_empty_list(self):
        assert get_first_matching_profile([]) is None

    def test_no_matches(self):
        assert get_first_matching_profile([_repo("foo/bar"), _repo("baz/qux")]) is None

    def test_picks_first_matching_repo(self):
        # Order matters: the first repo with a registered profile wins, even
        # if a later repo would also match.
        profile = get_first_matching_profile(
            [_repo("foo/bar"), _repo("ledoent/OpenUpgrade"), _repo("OCA/OpenUpgrade")]
        )
        assert profile is PROFILES[0]


class TestOpenUpgradeProfileContents:
    """Lock down the OpenUpgrade profile's wire format.

    These notes ship into the system prompt every run — a regression here
    would silently change Gemini's behavior, so we pin the load-bearing
    strings.
    """

    def setup_method(self):
        self.profile = get_profile_for_full_name("ledoent/OpenUpgrade")
        assert self.profile is not None

    def test_branch_prefix_is_oca_style(self):
        assert self.profile.branch_prefix == "19.0-fix-"

    def test_title_hint_names_all_tags(self):
        for tag in ("[OU-FIX]", "[OU-ADD]", "[OU-IMP]", "[MIG]"):
            assert tag in self.profile.title_format_hint

    def test_familiarity_notes_cover_known_gotchas(self):
        notes = self.profile.familiarity_notes
        assert "openupgrade_framework/odoo_patch/" in notes
        assert "monkey patches" in notes
        assert "Multi-DB" in notes
