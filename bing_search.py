      
"""
A web search server for ParlAI, including Blenderbot2.
See README.md
"""
import bs4
import html
import http.server
import json
import re
from typing import *
import urllib.parse

import chardet
import fire
import html2text
from serpapi import GoogleSearch
# import parlai.agents.rag.retrieve_api
import rich
import rich.markup
import requests
import time

print = rich.print

_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = 8081
_DELAY_SEARCH = 1.0  # Making this too low will get you IP banned
_STYLE_GOOD = "[green]"
_STYLE_SKIP = ""
_CLOSE_STYLE_GOOD = "[/]" if _STYLE_GOOD else ""
_CLOSE_STYLE_SKIP = "[/]" if _STYLE_SKIP else ""
_REQUESTS_GET_TIMEOUT = 5

_SECRET_KEY = "96aa25edca2c96d6307b9f1e3ce9156d4beba7effbefe18ac6dbc9932a1cf808"

def _parse_host(host: str) -> Tuple[str, int]:
    """ Parse the host string. 
    Should be in the format HOSTNAME:PORT. 
    Example: 0.0.0.0:8080
    """
    splitted = host.split(":")
    hostname = splitted[0]
    port = splitted[1] if len(splitted) > 1 else _DEFAULT_PORT
    return hostname, int(port)

def _get_and_parse(url: str) -> Dict[str, str]:
    """ Download a webpage and parse it. """

    try:
        resp = requests.get(url, timeout=_REQUESTS_GET_TIMEOUT)
    except requests.exceptions.RequestException as e:
        print(f"[!] {e} for url {url}")
        return None
    else:
        resp.encoding = resp.apparent_encoding
        page = resp.text
    
    ###########################################################################
    # Prepare the title
    ###########################################################################
    output_dict = dict(title="", content="", url=url)
    soup = bs4.BeautifulSoup(page, features="lxml")
    pre_rendered = soup.find("title")
    output_dict["title"] = (
        html.unescape(pre_rendered.renderContents().decode()) if pre_rendered else ""
    )
    
    output_dict["title"] = (
        output_dict["title"].replace("\n", "").replace("\r", "")
    )

    ###########################################################################
    # Prepare the content
    ###########################################################################
    text_maker = html2text.HTML2Text()
    text_maker.ignore_links = True
    text_maker.ignore_tables = True
    text_maker.ignore_images = True
    text_maker.ignore_emphasis = True
    text_maker.single_line = True
    output_dict["content"] = html.unescape(text_maker.handle(page).strip())

    return output_dict

class SearchABC(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        """ Handle POST requests from the client. (All requests are POST) """

        #######################################################################
        # Prepare and Parse
        #######################################################################
        content_length = int(self.headers["Content-Length"])
        post_data = self.rfile.read(content_length)

        # Figure out the encoding
        if "charset=" in self.headers["Content-Type"]:
            charset = re.match(r".*charset=([\w_\-]+)\b.*", self.headers["Content-Type"]).group(1)
        else:
            detector = chardet.UniversalDetector()
            detector.feed(post_data)
            detector.close()
            charset = detector.result["encoding"]

        post_data = post_data.decode(charset)
        parsed = urllib.parse.parse_qs(post_data)

        for v in parsed.values():
            assert len(v) == 1, len(v)
        parsed = {k: v[0] for k, v in parsed.items()}

        #######################################################################
        # Search, get the pages and parse the content of the pages
        #######################################################################
        print(f"\n[bold]Received query:[/] {parsed}")
        n = int(parsed["n"])
        q = parsed["q"]
        loc = parsed.get("cc","HK")

        # Over query a little bit in case we find useless URLs
        content = []
        # dupe_detection_set = set()
        # start = time.time()
        # results = self.search(q, n, loc)["organic_results"]
        for res in self.search(q, n, loc)["organic_results"]:
            # print("time cost: ", time.time() - start)
            if (len(content) >= n):
                break
            url = res['link']

            maybe_content = _get_and_parse(url)

            # Check that getting the content didn't fail
            reason_empty_response = maybe_content is None
            if not reason_empty_response:
                reason_content_empty = (
                    maybe_content["content"] is None
                    or len(maybe_content["content"]) == 0
                )
            else:
                reason_content_empty = False
            
            reasons = dict(
                reason_empty_response=reason_empty_response,
                reason_content_empty=reason_content_empty,
            )

            if not any(reasons.values()):
                ###############################################################
                # Log the entry
                ###############################################################
                title_str = (
                    f"`{rich.markup.escape(maybe_content['title'])}`"
                    if maybe_content["title"]
                    else "<No Title>"
                )
                print(
                    f" {_STYLE_GOOD}>{_CLOSE_STYLE_GOOD} Result: Title: {title_str}\n"
                    f"   {rich.markup.escape(maybe_content['url'])}"
                    # f"Content: {len(maybe_content['content'])}",
                )
                content.append(maybe_content)
                if len(content) >= n:
                    break

            else:
                ###############################################################
                # Log why it failed
                ###############################################################
                reason_string = ", ".join(
                    {
                        reason_name
                        for reason_name, whether_failed in reasons.items()
                        if whether_failed
                    }
                )
                print(f" {_STYLE_SKIP}x{_CLOSE_STYLE_SKIP} Excluding an URL because `{_STYLE_SKIP}{reason_string}{_CLOSE_STYLE_SKIP}`:\n"
                      f"   {url}") 

        ###############################################################
        # Prepare the answer and send it
        ###############################################################
        content = content[:n]  
        output = json.dumps(dict(response=content)).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-type", "text/html")
        self.send_header("Content-Length", len(output))
        self.end_headers()
        self.wfile.write(output)

    def search(self, q: str, n: int, loc:str) -> Generator[str, None, None]:
        return NotImplemented(
            "Search is an abstract base class, not meant to be directly "
            "instantiated. You should instantiate a derived class like "
            "GoogleSearch."
        )

class BingSearch(SearchABC):
    def search(self, q: str, n: int, loc: str) -> Generator[str, None, None]:
        params = {
            "engine": "bing",
            "q": q,
            "cc": loc,
            "num": n,
            "api_key": _SECRET_KEY
        }
        search = GoogleSearch(params)
        return search.get_dict()
        # return googlesearch.search(q, num=n, stop=None, pause=_DELAY_SEARCH)


class Application:
    def serve(
        self, host: str = _DEFAULT_HOST) -> NoReturn:
        """ Main entry point: Start the server.
        Arguments:
            host (str):
        HOSTNAME:PORT of the server. HOSTNAME can be an IP. 
        Most of the time should be 0.0.0.0. Port 8080 doesn't work on colab.
        Other ports also probably don't work on colab, test it out.

        """

        hostname, port = _parse_host(host)
        host = f"{hostname}:{port}"

        with http.server.ThreadingHTTPServer(
            (hostname, int(port)), BingSearch
        ) as server:
            print("Serving forever.")
            print(f"Host: {host}")
            server.serve_forever()




if __name__ == "__main__":
    fire.Fire(Application)

    