"""Small TTML documents for the tests."""


def doc(paragraphs, params=""):
    body = "".join('<p begin="%s" end="%s">%s</p>' % p for p in paragraphs)
    return ('<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttp="http://www.w3.org/ns/ttml#parameter" xml:lang="en"%s>'
            '<body><div>%s</div></body></tt>' % (params, body))
