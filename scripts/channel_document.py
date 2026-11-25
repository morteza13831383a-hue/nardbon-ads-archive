"""Lexbor-backed document with the legacy extraction interface."""
from html_core import Document, Node

try:
    from selectolax.lexbor import LexborHTMLParser
except ImportError:
    LexborHTMLParser = None


def parse(html):
    if LexborHTMLParser is None:
        return Document(html)
    tree = LexborHTMLParser(html)
    doc = Document('')
    # Preserve text node order and construct the Node interface only once per page.
    stack = [(tree.root, doc.root)]
    while stack:
        node, parent = stack.pop()
        if node is None:
            continue
        stack.append((node.next, parent))
        if node.tag == '-text':
            parent.children.append(node.text())
        elif not node.tag.startswith('-'):
            child = Node(node.tag, node.attributes)
            parent.children.append(child)
            stack.append((node.child, child))
    return doc
