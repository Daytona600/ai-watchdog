# SPDX-License-Identifier: AGPL-3.0-or-later
"""Google Custom Search JSON API (official, authenticated web search - not scraped HTML)"""

from urllib.parse import urlencode

about = {
    "website": "https://programmablesearchengine.google.com/",
    "wikidata_id": "Q9366",
    "official_api_documentation": "https://developers.google.com/custom-search/v1/overview",
    "use_official_api": True,
    "require_api_key": True,
    "results": "JSON",
}

categories = ["general", "web"]
paging = True

base_url = "https://www.googleapis.com/customsearch/v1"
api_key = ""  # set via settings.yml engine config
search_engine_id = ""  # "cx" value from programmablesearchengine.google.com, set via settings.yml

results_per_page = 10


def request(query, params):
    args = {
        "key": api_key,
        "cx": search_engine_id,
        "q": query,
        "start": (params["pageno"] - 1) * results_per_page + 1,
    }
    params["url"] = f"{base_url}?{urlencode(args)}"
    return params


def response(resp):
    results = []
    data = resp.json()

    for item in data.get("items", []):
        results.append(
            {
                "title": item.get("title"),
                "url": item.get("link"),
                "content": item.get("snippet", ""),
            }
        )

    return results
