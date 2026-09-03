# SPDX-License-Identifier: AGPL-3.0-or-later
"""Brave Search API (official, authenticated web search - not scraped HTML)"""

from urllib.parse import urlencode

about = {
    "website": "https://search.brave.com/",
    "wikidata_id": "Q22906900",
    "official_api_documentation": "https://api-dashboard.search.brave.com/app/documentation/web-search/get-started",
    "use_official_api": True,
    "require_api_key": True,
    "results": "JSON",
}

categories = ["general", "web"]
paging = True

base_url = "https://api.search.brave.com/res/v1/web/search"
api_key = ""  # set via settings.yml engine config


def request(query, params):
    args = {
        "q": query,
        "offset": params["pageno"] - 1,
    }
    params["url"] = f"{base_url}?{urlencode(args)}"
    params["headers"]["Accept"] = "application/json"
    params["headers"]["X-Subscription-Token"] = api_key
    return params


def response(resp):
    results = []
    data = resp.json()

    for item in data.get("web", {}).get("results", []):
        results.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "content": item.get("description", ""),
            }
        )

    return results
