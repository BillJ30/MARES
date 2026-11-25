from newshub.tagging import tag_headline


def test_common_headlines_get_the_right_tags():
    assert tag_headline("Nvidia beats estimates as data-centre revenue soars") == ["earnings", "news"]
    assert "leadership" in tag_headline("Intel CEO steps down after board clash")
    assert "analyst" in tag_headline("Morgan Stanley downgrades Tesla, cuts price target")
    assert "m-and-a" in tag_headline("Cisco to buy security start-up in $2bn deal")
    assert "legal" in tag_headline("Apple faces antitrust probe in Europe")
    assert "restructuring" in tag_headline("Microsoft cuts 6,000 jobs in restructuring")
    assert "capital-return" in tag_headline("Costco raises dividend and expands buyback")


def test_plain_headline_is_just_news():
    assert tag_headline("What to watch in markets this week") == ["news"]


def test_whole_words_only():
    # "fine" should not match inside "refinery", "ceo" not inside "ceoliths"
    assert tag_headline("Refinery output rises") == ["news"]
