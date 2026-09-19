"""Markdown with local file links routed through the application's file viewer."""
from markdown_it import MarkdownIt
from textual.widgets import Markdown


def file_link_parser():
    parser = MarkdownIt("gfm-like")
    ordinary_link = parser.validateLink
    # markdown-it rejects file: by default, including our own workspace URLs.
    # The application handles clicks and confines files to the worker workspace.
    parser.validateLink = lambda url: url.lower().startswith("file:") or ordinary_link(url)
    return parser


class ReviewMarkdown(Markdown):
    def __init__(self, markdown="", **kwargs):
        super().__init__(markdown, parser_factory=file_link_parser, open_links=False, **kwargs)
