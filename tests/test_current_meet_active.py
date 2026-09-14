"""Regression tests for webapp/server.py's current_meet_is_active() / current_meet_is_featured().

current_meet_is_active() backs BOTH the family page's "Choose A Meet" list
(public_meets_payload()) and the Officials meet picker (officials_meets_payload() explicitly
reuses it "so the two lists can't disagree") -- one bug here breaks both surfaces, and one fix
verifies both.

The regression: commit 0ade7c1 originally used `date.today() < expires_at` -- correct, since
expires_at is always end_date + 1 day, so a meet is shown through its last competition day and
hidden starting the day after. Commit dcd878b flattened this to `<=` in the same diff that added
current_meet_is_featured()'s *separate* featured_until check (where `<=` is correct), apparently by
copy-pasting that comparison onto expires_at by mistake. The effect: every meet stayed listed one
extra day after it ended.

Dates are computed relative to the real date.today() rather than mocked, so these tests don't need
to patch datetime.date (which parse_iso_date also relies on via date.fromisoformat) and instead
just build meet dicts whose end_date/expires_at sit the right number of days from today.
"""

from datetime import date, timedelta
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from webapp.server import current_meet_is_active, current_meet_is_featured
except ModuleNotFoundError as exc:  # pragma: no cover - environment guard
    raise unittest.SkipTest("webapp.server needs Python 3.12: the stdlib cgi module was removed in 3.13") from exc


def meet_ending(days_ago: int) -> dict:
    """A meet whose last competition day was `days_ago` days ago, with expires_at set the way
    every real entry in data/current_meets.json sets it: end_date + 1 day."""
    end_date = date.today() - timedelta(days=days_ago)
    expires_at = end_date + timedelta(days=1)
    return {"end_date": end_date.isoformat(), "expires_at": expires_at.isoformat()}


class CurrentMeetIsActiveTest(unittest.TestCase):
    def test_meet_is_listed_on_its_last_competition_day(self):
        # today == end_date: the meet is still happening today, must still show.
        self.assertTrue(current_meet_is_active(meet_ending(days_ago=0)))

    def test_meet_is_not_listed_the_day_after_it_ends(self):
        # The exact regression: a meet that ended yesterday (today == end_date + 1, i.e.
        # today == expires_at) must not still be listed as current.
        self.assertFalse(current_meet_is_active(meet_ending(days_ago=1)))

    def test_meet_is_not_listed_two_days_after_it_ends(self):
        self.assertFalse(current_meet_is_active(meet_ending(days_ago=2)))

    def test_end_date_fallback_stays_inclusive_through_the_last_day(self):
        # No expires_at at all: current_meet_is_active() falls back to `<= end_date`, which is
        # already correct and must NOT be touched by this fix -- a meet ending today still shows.
        end_date = date.today()
        self.assertTrue(current_meet_is_active({"end_date": end_date.isoformat()}))

    def test_a_meet_with_neither_date_is_treated_as_always_active(self):
        self.assertTrue(current_meet_is_active({}))


class CurrentMeetIsFeaturedTest(unittest.TestCase):
    def test_featured_until_stays_inclusive_on_its_last_day(self):
        # A separate comparison from current_meet_is_active()'s expires_at check -- `<=` is
        # correct here (today == featured_until still counts as featured), so this pins the
        # distinction and stops the two from being flattened together again.
        meet = {"featured": True, "featured_until": date.today().isoformat()}
        self.assertTrue(current_meet_is_featured(meet))

    def test_featured_until_stops_featuring_the_day_after(self):
        yesterday = date.today() - timedelta(days=1)
        meet = {"featured": True, "featured_until": yesterday.isoformat()}
        self.assertFalse(current_meet_is_featured(meet))

    def test_not_featured_at_all_if_the_featured_flag_is_unset(self):
        meet = {"featured_until": (date.today() + timedelta(days=5)).isoformat()}
        self.assertFalse(current_meet_is_featured(meet))


if __name__ == "__main__":
    unittest.main()
