import unittest
from datetime import date

from watcher.filters import JobFilter, parse_experience
from watcher.models import Job

TODAY = date(2026, 11, 10)


def job(title="Data Scientist", location="Bengaluru, India", posted=TODAY, description=""):
    return Job(company="Acme", id="1", title=title, url="u", location=location,
               posted=posted, description=description)


class ParseExperienceTests(unittest.TestCase):
    def test_range(self):
        self.assertEqual(parse_experience("We need 3-5 years of experience in Python."), (3, 5))

    def test_range_with_to(self):
        self.assertEqual(parse_experience("Experience: 4 to 8 yrs in ML"), (4, 8))

    def test_plus(self):
        self.assertEqual(parse_experience("5+ years of relevant experience"), (5, None))

    def test_minimum(self):
        self.assertEqual(parse_experience("Minimum of 7 years experience required"), (7, None))

    def test_ignores_non_experience_years(self):
        self.assertIsNone(parse_experience("Founded 25 years ago, we serve 10 years of clients."))

    def test_preferred_sentence_ignored(self):
        text = "2-4 years of experience with Python. 6+ years experience preferred."
        self.assertEqual(parse_experience(text), (2, 4))

    def test_highest_minimum_wins(self):
        # Shape of an Optum posting (2026-11-10): the 12+ requirement has no "experience" word.
        text = ("Required Qualifications:\n"
                "12+ years in digital analytics or related field, with deep Adobe expertise\n"
                "3+ years in leadership/managerial roles, building and guiding analytics teams\n")
        self.assertEqual(parse_experience(text), (12, None))

    def test_preferred_section_ignored(self):
        # Shape of a Google posting: minimum vs preferred qualification lists.
        text = ("Minimum qualifications\n"
                "Bachelor's degree or equivalent practical experience.\n"
                "2 years of experience with machine learning.\n"
                "Preferred qualifications\n"
                "8 years of experience in software development.\n"
                "About the job\n"
                "Our team builds things.\n")
        self.assertEqual(parse_experience(text), (2, None))

    def test_company_blurb_ignored(self):
        self.assertIsNone(parse_experience("For over 25 years of excellence in our industry."))

    def test_none(self):
        self.assertIsNone(parse_experience(""))


class JobFilterTests(unittest.TestCase):
    def test_include_is_whole_word(self):
        f = JobFilter({"title_include": ["AI"]})
        self.assertTrue(f.basic_match(job("Senior AI Engineer"), TODAY)[0])
        self.assertFalse(f.basic_match(job("Maintenance Lead"), TODAY)[0])

    def test_symbols_in_keywords(self):
        f = JobFilter({"title_include": ["C++"]})
        self.assertTrue(f.basic_match(job("C++ Developer"), TODAY)[0])

    def test_exclude(self):
        f = JobFilter({"title_exclude": ["intern"]})
        self.assertFalse(f.basic_match(job("Data Science Intern"), TODAY)[0])
        self.assertTrue(f.basic_match(job("Internal Tools Engineer"), TODAY)[0])

    def test_exclude_except(self):
        f = JobFilter({"title_exclude": ["Manager", "Senior"], "title_exclude_except": ["Assistant Manager"]})
        self.assertTrue(f.basic_match(job("Assistant Manager - Data Science"), TODAY)[0])
        self.assertFalse(f.basic_match(job("Manager - Data Science"), TODAY)[0])
        self.assertFalse(f.basic_match(job("Senior Assistant Manager - Analytics"), TODAY)[0])

    def test_location(self):
        f = JobFilter({"locations": ["india"]})
        self.assertTrue(f.basic_match(job(location="Gurugram, Haryana, India"), TODAY)[0])
        self.assertFalse(f.basic_match(job(location="New York, NY"), TODAY)[0])

    def test_unknown_location_is_kept(self):
        f = JobFilter({"locations": ["India"]})
        self.assertTrue(f.basic_match(job(location=""), TODAY)[0])
        self.assertTrue(f.basic_match(job(location="3 Locations"), TODAY)[0])

    def test_age(self):
        f = JobFilter({"max_age_days": 7})
        self.assertFalse(f.basic_match(job(posted=date(2026, 10, 1)), TODAY)[0])
        self.assertTrue(f.basic_match(job(posted=None), TODAY)[0])

    def test_experience(self):
        f = JobFilter({"min_experience": 2, "max_experience": 5})
        self.assertTrue(f.uses_experience)
        self.assertTrue(f.experience_match(job(description="3-6 years experience"))[0])
        self.assertFalse(f.experience_match(job(description="10+ years of experience"))[0])
        self.assertFalse(f.experience_match(job(description="0-1 years experience"))[0])
        self.assertTrue(f.experience_match(job(description="no requirement stated"))[0])


if __name__ == "__main__":
    unittest.main()
