import flask
import werkzeug
import re


token_p = re.compile(r'''
    (   (?P<StartHeader>^::[ \t]+)
    |   (?P<Link><[^>]+>)
    |   (?P<BlankLine>^[ \t]*$)
    |   (?P<Text>[^<\n]+)
    |   (?P<Newline>\n)
    |   (?P<FAIL>.)
    )
''', re.VERBOSE | re.MULTILINE)


def tokenize(text):
    for m in token_p.finditer(text):
        d = {k: v for k, v in m.groupdict().items() if v is not None}
        typename, source = d.popitem()
        assert not d
        if typename == 'FAIL':
            raise ValueError('Malformed input: {}'.format(source))
        yield typename, source


def parse(text):
    stack = [('Par', ())]

    def match(*types):
        top_of_stack = stack[-len(types):]
        stack_types = tuple(a for a, *_ in top_of_stack)
        return stack_types == types

    def parselink(link):
        inside = link[1:-1]
        uri, _, linktext = inside.partition('|')
        if not linktext:
            linktext = uri
        if '/' in uri:
            linktype = 'ExternalLink'
        else:
            linktype = 'InternalLink'
        return (linktype, uri, (('Text', linktext), ))

    for tok in tokenize(text):
        stack.append(tok)

        while True:
            if match('Text'):
                item = stack.pop()
                stack.append(('Span', (item, )))
            elif match('Link'):
                _, linktext = stack.pop()
                stack.append(('Span', (parselink(linktext), )))
            elif match('Span', 'Span'):
                _, span2 = stack.pop()
                _, span1 = stack.pop()
                stack.append(('Span', span1 + span2))
            elif match('Par', 'Span'):
                _, span = stack.pop()
                _, par = stack.pop()
                stack.append(('Par', par + span))
            elif match('Par', 'Newline', 'Span'):
                _, span = stack.pop()
                stack.pop()
                _, par = stack.pop()
                if par:
                    textparts = (('Text', ' '), )
                else:
                    textparts = ()
                stack.append(('Par', par + textparts + span))
            elif match('StartHeader'):
                stack.pop()
                stack.append(('Header', ()))
            elif match('Header', 'Span'):
                _, span = stack.pop()
                _, header = stack.pop()
                stack.append(('Header', header + span))
            elif match('BlankLine'):
                stack.pop()
                stack.append(('Par', ()))
            else:
                break

    for part in stack:
        if part == ('Par', ()):
            continue
        if part[0] == 'Newline':
            continue
        yield part


def to_html(parse_result, dialect='xhtml', url_for=flask.url_for):
    """Convert parsed GitkiText to HTML."""
    html_builder = werkzeug.utils.HTMLBuilder(dialect)

    def part_to_html(part):
        part_type, *args = part
        if part_type == 'Text':
            return html_builder(args[0])
        if part_type == 'InternalLink':
            document, link_content = args
            return html_builder.a(
                *(part_to_html(part) for part in link_content),
                href=url_for('page', name=document))
        if part_type == 'ExternalLink':
            uri, link_content = args
            return html_builder.a(
                *(part_to_html(part) for part in link_content),
                href=uri, target='_blank')
        if part_type == 'Header':
            return html_builder.h2(*(part_to_html(part) for part in args[0]))
        if part_type == 'Par':
            return html_builder.p(*(part_to_html(part) for part in args[0]))
        raise ValueError('Unknown part type: {}'.format(part_type))

    return html_builder.div(*(part_to_html(part) for part in parse_result))
