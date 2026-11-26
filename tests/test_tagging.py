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


def test_fund_holding_reports_are_tagged_holdings():
    for title in [
        "Fiputnam Investment Management Co. Acquires 69,083 Shares of Broadcom Inc. $AVGO",
        "Meta Platforms, Inc. $META Shares Bought by State Board of Administration of Florida",
        "NVIDIA Corporation $NVDA Stock Purchased by Fifth Third Wealth Advisors LLC",
        "Vanguard Group Inc. Lowers Stake in Apple Inc. (NASDAQ:AAPL)",
        "Smith Capital Has $4.2 Million Position in Microsoft Co.",
        "Acme Advisors Takes New Position in Intel Corporation",
    ]:
        assert "holdings" in tag_headline(title), title


def test_real_news_is_not_tagged_holdings():
    for title in [
        "Nvidia beats estimates as data-centre revenue soars",
        "Apple strengthens its position in AI with new chip",
        "Microsoft to buy gaming studio in $2bn deal",
        "Tesla shares fall after delivery miss",
    ]:
        assert "holdings" not in tag_headline(title), title
